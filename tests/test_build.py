"""The live build pipeline: real events in order, honest failures, and a public tracker that leaks nothing."""
import json
import time

from backend import agent_service, build_service, providers
from backend import database as dbm
from tests.test_api import REQ, SPEC


def _wait(client, rid, secs=10):
    end = time.time() + secs
    while time.time() < end:
        b = client.get(f"/api/builds/{rid}").json()
        if b["status"] != "running":
            return b
        time.sleep(0.05)
    raise AssertionError("build did not finish")


def _setup(client, monkeypatch, execute=None, spec=SPEC):
    async def fake_spec(_):
        return spec, "anthropic", "fake-model"
    async def ok(spec, task, p):
        return {"response": "The total is 1 unit.", "model": "fake", "tool_calls": [{"tool": "calculator", "ok": True}]}
    monkeypatch.setattr(agent_service, "generate_spec", fake_spec)
    monkeypatch.setattr(agent_service, "_execute", execute or ok)
    monkeypatch.setattr(providers, "missing", lambda p: None)
    return client.post("/api/requests", json=REQ).json()["request_id"]


def test_successful_build_emits_real_events_in_order(client, monkeypatch):
    rid = _setup(client, monkeypatch)
    assert client.post(f"/api/requests/{rid}/build").status_code == 202
    b = _wait(client, rid)
    assert b["status"] == "ready"
    assert [s for s in dbm.BUILD_STAGES if b["stages"][s]["state"] == "done"] == dbm.BUILD_STAGES
    assert b["stages"]["specify"]["detail"]["name"] == "Sales Analyst"
    assert len(b["stages"]["test"]["progress"]) == 1 and b["stages"]["test"]["progress"][0]["detail"]["passed"] is True
    files = [f["path"] for f in b["stages"]["package"]["detail"]["files"]]
    assert ".claude-plugin/plugin.json" in files and any(p.startswith("skills/") for p in files)
    assert [e["seq"] for e in b["events"]] == sorted(e["seq"] for e in b["events"])
    assert len(client.get(f"/api/requests/{rid}").json()["agent_ids"]) == 1            # a real agent was saved, nothing pushed anywhere


def test_failure_at_specify_stops_the_pipeline_with_the_real_message(client, monkeypatch):
    rid = _setup(client, monkeypatch)
    async def boom(_):
        raise providers.NotConfigured("Provider 'anthropic' is not configured: no key")
    monkeypatch.setattr(agent_service, "generate_spec", boom)
    client.post(f"/api/requests/{rid}/build")
    b = _wait(client, rid)
    assert b["status"] == "failed" and b["stages"]["specify"]["state"] == "failed" and "no key" in b["stages"]["specify"]["title"]
    assert "test" not in b["stages"] and "package" not in b["stages"]


def test_failed_test_scenario_fails_the_test_stage_and_stops(client, monkeypatch):
    async def empty(spec, task, p):
        return {"response": "", "model": "fake", "tool_calls": []}
    rid = _setup(client, monkeypatch, execute=empty)
    client.post(f"/api/requests/{rid}/build")
    b = _wait(client, rid)
    assert b["status"] == "failed" and b["stages"]["test"]["state"] == "failed" and b["stages"]["test"]["detail"] == {"passed": 0, "total": 1}
    assert "package" not in b["stages"]


def test_second_start_is_refused_while_running_and_allowed_after(client, monkeypatch):
    rid = _setup(client, monkeypatch)
    build_service._active.add(rid)
    assert client.post(f"/api/requests/{rid}/build").status_code == 409
    build_service._active.discard(rid)
    client.post(f"/api/requests/{rid}/build"); _wait(client, rid)
    client.post(f"/api/requests/{rid}/build"); b = _wait(client, rid)                    # retry starts a fresh run
    assert b["status"] == "ready" and sum(1 for e in b["events"] if e["stage"] == "received" and e["state"] == "running") == 1


def test_stale_running_build_is_marked_interrupted_on_startup(client, monkeypatch):
    rid = client.post("/api/requests", json=REQ).json()["request_id"]
    dbm.add_build_event(rid, "received", "running", "x")
    dbm.add_build_event(rid, "specify", "running", "writing")
    assert dbm.interrupt_stale_builds() == 1
    b = client.get(f"/api/builds/{rid}").json()
    assert b["status"] == "interrupted" and b["stages"]["specify"]["state"] == "interrupted"
    assert dbm.interrupt_stale_builds() == 0


def test_factory_build_endpoints_need_auth_and_404_on_unknown(client):
    assert client.post("/api/requests/REQ-NOPE/build").status_code == 404
    assert client.get("/api/builds/REQ-NOPE").status_code == 404
    client.headers.pop("X-Factory-Token")
    assert client.post("/api/requests/REQ-NOPE/build").status_code == 401
    assert client.get("/api/builds/REQ-NOPE").status_code == 401


def test_public_tracker_shows_progress_but_leaks_nothing(client, monkeypatch):
    rid = _setup(client, monkeypatch)
    client.post(f"/api/requests/{rid}/build"); _wait(client, rid)
    client.headers.pop("X-Factory-Token")
    r = client.get(f"/api/track/{rid}")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    j = r.json()
    assert j["status"] == "ready" and [s["state"] for s in j["steps"]] == ["done"] * 6 and j["checks"] == {"passed": 1, "total": 1}
    blob = json.dumps(j).lower()
    for secret in ("acme", "ops@acme.example", "acme-dev", "sales analyst", "instructions", "files", "never invent", "fake-model", rid.lower()):
        assert secret not in blob, secret


def test_public_tracker_hides_failure_details_and_unknown_ids_look_alike(client, monkeypatch):
    rid = _setup(client, monkeypatch)
    async def boom(_):
        raise providers.UpstreamError("secret upstream detail sk-123")
    monkeypatch.setattr(agent_service, "generate_spec", boom)
    client.post(f"/api/requests/{rid}/build"); _wait(client, rid)
    j = client.get(f"/api/track/{rid}").json()
    assert j["status"] == "failed" and [s["state"] for s in j["steps"]][2] == "problem" and "sk-123" not in json.dumps(j)
    a, b = client.get("/api/track/REQ-00000000"), client.get("/api/track/REQ-FFFFFFFF")
    assert a.status_code == b.status_code == 404 and a.json() == b.json()


def test_tracker_before_any_build_and_rate_limit(client, monkeypatch):
    rid = client.post("/api/requests", json=REQ).json()["request_id"]
    j = client.get(f"/api/track/{rid}").json()
    assert j["status"] == "none" and j["steps"][0]["state"] == "done" and j["steps"][1]["state"] == "active"
    monkeypatch.setenv("TRACK_PER_HOUR_PER_IP", "2")
    codes = [client.get(f"/api/track/{rid}").status_code for _ in range(3)]
    assert codes[-1] == 429


# ---- structured deliverables: the code checks are a real part of the pipeline
def _structured(spec_fn):
    from tests.artifacts import golden
    return spec_fn(golden)


def test_code_checks_are_skipped_honestly_when_the_switch_is_off(client, monkeypatch):
    monkeypatch.delenv("NEXARA_RUN_GENERATED_CODE", raising=False)
    from backend.models import AgentSpec
    spec = AgentSpec(**_structured(lambda g: g.tool_only())).model_dump()
    rid = _setup(client, monkeypatch, spec=spec)
    client.post(f"/api/requests/{rid}/build")
    b = _wait(client, rid)
    assert b["status"] == "ready"
    first = b["stages"]["test"]["progress"][0]["detail"]
    assert first["name"] == "Code checks" and first["skipped"] is True and "NEXARA_RUN_GENERATED_CODE" in first["reason"]
    agent = dbm.get_agent(client.get(f"/api/requests/{rid}").json()["agent_ids"][0])
    assert agent["checks"]["verified"] is False and agent["checks"]["ran_code"] is False
    files = [f["path"] for f in b["stages"]["package"]["detail"]["files"]]
    assert "tools/csv-check/csv-check.py" in files and "lib/nx_cli.py" in files


def test_failing_checks_trigger_a_repair_and_the_repaired_spec_is_verified(client, monkeypatch):
    monkeypatch.setenv("NEXARA_RUN_GENERATED_CODE", "1")
    from backend.models import AgentSpec
    from tests.artifacts import golden
    bad = golden.tool_only()
    bad["scripts"][0]["examples"][0]["expect_contains"] = ["definitely-not-in-the-output"]
    seen = []

    async def fake_revise(spec, instruction, request=None):
        seen.append(instruction)
        return AgentSpec(**golden.tool_only()).model_dump(), "anthropic", "fake-model"
    monkeypatch.setattr(agent_service, "revise_spec", fake_revise)
    rid = _setup(client, monkeypatch, spec=AgentSpec(**bad).model_dump())
    client.post(f"/api/requests/{rid}/build")
    b = _wait(client, rid, 60)
    assert b["status"] == "ready" and len(seen) == 1 and "definitely-not-in-the-output" in seen[0]
    agent = dbm.get_agent(client.get(f"/api/requests/{rid}").json()["agent_ids"][0])
    assert agent["checks"]["verified"] is True and agent["checks"]["ran_code"] is True
    assert agent["spec"]["scripts"][0]["examples"][0]["expect_contains"] != ["definitely-not-in-the-output"]


def test_checks_that_cannot_be_repaired_stop_the_build(client, monkeypatch):
    monkeypatch.setenv("NEXARA_RUN_GENERATED_CODE", "1")
    from backend.models import AgentSpec
    from tests.artifacts import golden
    bad = golden.tool_only()
    bad["scripts"][0]["examples"][0]["expect_contains"] = ["nope"]
    calls = []

    async def same(spec, instruction, request=None):
        calls.append(1)
        return AgentSpec(**bad).model_dump(), "anthropic", "fake-model"
    monkeypatch.setattr(agent_service, "revise_spec", same)
    rid = _setup(client, monkeypatch, spec=AgentSpec(**bad).model_dump())
    client.post(f"/api/requests/{rid}/build")
    b = _wait(client, rid, 90)
    assert b["status"] == "failed" and len(calls) == build_service.MAX_REPAIRS and "package" not in b["stages"]
    assert "did not pass its own checks" in b["stages"]["test"]["title"]
