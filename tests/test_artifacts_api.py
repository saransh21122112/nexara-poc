"""Staff-facing parts of real deliverables: artifact files, run-checks, and the extra confirmation for unverified code."""
import pytest

from backend import database as dbm
from backend.models import AgentSpec
from tests.artifacts import golden
from tests.test_delivery import env  # noqa: F401  (fixture)


def code_spec():
    return AgentSpec(**golden.tool_only()).model_dump()


def test_artifact_endpoint_lists_files_with_code_but_not_the_vetted_library(env, monkeypatch):
    monkeypatch.delenv("NEXARA_RUN_GENERATED_CODE", raising=False)
    _, aid, _ = env.mk(spec=code_spec())
    r = env.client.get(f"/api/agents/{aid}/artifact").json()
    paths = {f["path"] for f in r["files"]}
    assert "tools/csv-check/csv-check.py" in paths and "skills/csv-check/SKILL.md" in paths
    code = next(f for f in r["files"] if f["path"] == "tools/csv-check/csv-check.py")
    assert "def handle(args):" in code["content"] and code["kind"] == "code"
    lib = next(f for f in r["files"] if f["path"] == "lib/nx_cli.py")
    assert lib["content"] is None and lib["kind"] == "vetted"
    assert r["run_enabled"] is False and r["structured"] is True


def test_run_checks_is_honest_when_the_switch_is_off_and_real_when_it_is_on(env, monkeypatch):
    _, aid, _ = env.mk(spec=code_spec())
    monkeypatch.delenv("NEXARA_RUN_GENERATED_CODE", raising=False)
    off = env.client.post(f"/api/agents/{aid}/run-checks").json()
    assert off["verified"] is False and off["ran_code"] is False and "NEXARA_RUN_GENERATED_CODE" in off["reason"]
    monkeypatch.setenv("NEXARA_RUN_GENERATED_CODE", "1")
    on = env.client.post(f"/api/agents/{aid}/run-checks").json()
    assert on["verified"] is True and on["ran_code"] is True
    assert env.client.get(f"/api/agents/{aid}/artifact").json()["checks"]["verified"] is True


def test_editing_the_spec_invalidates_the_verified_badge(env, monkeypatch):
    monkeypatch.setenv("NEXARA_RUN_GENERATED_CODE", "1")
    _, aid, _ = env.mk(spec=code_spec())
    env.client.post(f"/api/agents/{aid}/run-checks")
    spec = dbm.get_agent(aid)["spec"]
    spec["purpose"] = "Changed purpose."
    assert env.client.put(f"/api/agents/{aid}", json=spec).status_code == 200
    assert env.client.get(f"/api/agents/{aid}/artifact").json()["checks"] is None


def test_delivering_unverified_code_needs_an_explicit_acknowledgement(env, monkeypatch):
    monkeypatch.delenv("NEXARA_RUN_GENERATED_CODE", raising=False)
    _, aid, _ = env.mk(spec=code_spec())
    r = env.deliver_raw(aid)
    assert r.status_code == 409 and "unverified_code_ack" in r.json()["detail"] and env.fake.repos == {}
    prev = env.client.get(f"/api/agents/{aid}/delivery-preview").json()
    assert any("not been run" in w for w in prev["warnings"]) and prev["code"]["verified"] is False
    ok = env.deliver_raw(aid, unverified_code_ack=True)
    assert ok.status_code == 201


def test_verified_code_delivers_without_the_extra_acknowledgement(env, monkeypatch):
    monkeypatch.setenv("NEXARA_RUN_GENERATED_CODE", "1")
    _, aid, _ = env.mk(spec=code_spec())
    env.client.post(f"/api/agents/{aid}/run-checks")
    assert env.deliver_raw(aid).status_code == 201


def test_plain_agents_are_not_affected(env):
    _, aid, _ = env.mk()
    assert env.deliver_raw(aid).status_code == 201
    assert env.client.get(f"/api/agents/{aid}/artifact").json()["structured"] is False


def test_sync_of_unverified_code_also_needs_the_acknowledgement(env, monkeypatch):
    monkeypatch.delenv("NEXARA_RUN_GENERATED_CODE", raising=False)
    _, aid, cid = env.mk(spec=code_spec())
    assert env.deliver_raw(aid, unverified_code_ack=True).status_code == 201
    env.merge_open_pr(cid) if dbm.open_change(dbm.get_repo_for_customer(cid)["id"]) else None
    spec = dbm.get_agent(aid)["spec"]
    spec["purpose"] = "A newer purpose."
    env.client.put(f"/api/agents/{aid}", json=spec)
    r = env.sync_raw(cid, {"confirm": True})
    assert r.status_code == 409 and "unverified_code_ack" in r.json()["detail"]
    assert env.sync_raw(cid, {"confirm": True, "unverified_code_ack": True}).status_code == 200


def test_a_report_only_counts_for_the_exact_spec_it_was_run_on(env, monkeypatch):
    """Review finding: a check that finishes after the spec was edited must not make the NEW spec look verified."""
    monkeypatch.setenv("NEXARA_RUN_GENERATED_CODE", "1")
    _, aid, _ = env.mk(spec=code_spec())
    old_spec = dbm.get_agent(aid)["spec"]
    from backend.artifacts import checks
    report = checks.run_checks(old_spec, "acme")
    assert report["verified"] is True
    edited = dict(old_spec, scripts=[dict(old_spec["scripts"][0], handler_source=old_spec["scripts"][0]["handler_source"] + "\n# edited\n")])
    dbm.update_agent_spec(aid, edited)
    dbm.save_checks(aid, report)                                  # the slow run lands AFTER the edit
    assert env.client.get(f"/api/agents/{aid}/artifact").json()["checks"] is None
    r = env.deliver_raw(aid)
    assert r.status_code == 409 and "unverified_code_ack" in r.json()["detail"]


def test_the_zip_download_is_gated_like_delivery(env, monkeypatch):
    monkeypatch.delenv("NEXARA_RUN_GENERATED_CODE", raising=False)
    _, aid, cid = env.mk(spec=code_spec())
    assert env.deliver_raw(aid, unverified_code_ack=True).status_code == 201
    r = env.client.get(f"/api/customers/{cid}/plugin-zip")
    assert r.status_code == 409 and "unverified_code_ack" in r.json()["detail"]
    ok = env.client.get(f"/api/customers/{cid}/plugin-zip?unverified_code_ack=true")
    assert ok.status_code == 200 and ok.headers["content-type"] == "application/zip"


def test_sync_checks_every_agent_it_pushes_not_only_the_changed_ones(env, monkeypatch):
    monkeypatch.delenv("NEXARA_RUN_GENERATED_CODE", raising=False)
    _, aid, cid = env.mk(spec=code_spec())
    assert env.deliver_raw(aid, unverified_code_ack=True).status_code == 201
    r = env.sync_raw(cid, {"confirm": True})                       # nothing changed, but the push would still carry unverified code
    assert r.status_code == 409 and "unverified_code_ack" in r.json()["detail"]


def test_delivering_a_plain_agent_cannot_carry_another_agents_unverified_code_along(env, monkeypatch):
    """Review finding: a delivery rewrites the whole repo, so every agent it pushes needs to be verified, not only the one being delivered."""
    monkeypatch.delenv("NEXARA_RUN_GENERATED_CODE", raising=False)
    _, a1, cid = env.mk(spec=code_spec())
    assert env.deliver_raw(a1, unverified_code_ack=True).status_code == 201
    env.merge_open_pr(cid) if dbm.open_change(dbm.get_repo_for_customer(cid)["id"]) else None
    _, a2, _ = env.mk()                                     # a plain, harmless second agent for the same customer
    r = env.deliver_raw(a2, existing_repo_ack=True)
    assert r.status_code == 409 and "unverified_code_ack" in r.json()["detail"], r.text
    assert env.deliver_raw(a2, existing_repo_ack=True, unverified_code_ack=True).status_code == 201


def test_two_agents_with_the_same_component_name_conflict_cleanly(env):
    _, a1, _ = env.mk(spec=code_spec())
    assert env.deliver_raw(a1, unverified_code_ack=True).status_code == 201
    _, a2, _ = env.mk(spec=code_spec())
    r = env.deliver_raw(a2, existing_repo_ack=True, unverified_code_ack=True)
    assert r.status_code == 409 and "cannot share one plugin" in r.json()["detail"]
