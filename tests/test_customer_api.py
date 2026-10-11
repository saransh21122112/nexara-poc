import pytest

from backend import agent_service, main, providers
from backend import database as dbm
from tests.test_api import REQ, SPEC


@pytest.fixture
def dispatch(client, monkeypatch):
    async def fake_spec(_):
        return SPEC, "anthropic", "m"
    monkeypatch.setattr(agent_service, "generate_spec", fake_spec)
    rid = client.post("/api/requests", json=REQ).json()["request_id"]
    a = client.post(f"/api/requests/{rid}/generate-agent").json()
    d = client.post(f"/api/agents/{a['id']}/dispatch").json()
    return a, d


@pytest.fixture
def api_ready(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")  # provider 'anthropic' counts as configured
    seen = []

    async def fake_exec(spec, task, p):
        seen.append(spec["agent_name"])
        return {"response": f"echo: {task}", "tool_calls": [{"tool": "calculator", "args": {"expression": "1+1"}, "ok": True, "result": 2}],
                "input_tokens": 3, "output_tokens": 4}
    monkeypatch.setattr(agent_service, "_execute", fake_exec)
    main._hits.clear()
    return seen


def bearer(k):
    return {"Authorization": f"Bearer {k}"}


def test_key_lifecycle_and_storage(client, dispatch, api_ready, tmp_path):
    a, d = dispatch
    r = client.post(f"/api/dispatches/{d['id']}/keys", json={"label": "Acme prod"})
    assert r.status_code == 201
    k = r.json()
    assert k["key"].startswith("nxk_") and k["label"] == "Acme prod" and "key_hash" not in k
    listed = client.get(f"/api/dispatches/{d['id']}/keys").json()
    assert listed[0]["prefix"] == k["key"][:10] and "key" not in listed[0] and "key_hash" not in listed[0]
    with dbm.db() as c:  # plaintext never stored (works on SQLite and Postgres)
        assert k["key"] not in "".join(str(list(row)) for row in c.execute("SELECT * FROM api_keys").fetchall())
    # use it (no factory token needed on /v1)
    client.headers.pop("X-Factory-Token")
    run = client.post("/v1/run", json={"task": "hello"}, headers=bearer(k["key"]))
    assert run.status_code == 200 and run.json()["response"] == "echo: hello" and run.json()["tool_calls"][0]["tool"] == "calculator"
    assert client.get("/v1/agent", headers=bearer(k["key"])).json()["agent_name"] == "Sales Analyst"
    assert "instructions" not in client.get("/v1/agent", headers=bearer(k["key"])).json()


def test_auth_failures_and_revocation(client, dispatch, api_ready):
    a, d = dispatch
    k = client.post(f"/api/dispatches/{d['id']}/keys").json()
    tok = client.headers.pop("X-Factory-Token")
    for h in ({}, bearer("nxk_wrong"), bearer("nope"), {"Authorization": "Bearer "}, {"Authorization": k["key"]}):
        assert client.post("/v1/run", json={"task": "x"}, headers=h).status_code == 401
    assert client.post("/v1/run", json={"task": "x"}, headers={"X-Factory-Token": tok}).status_code == 401  # factory token != customer key
    client.headers["X-Factory-Token"] = tok
    assert client.get("/api/requests", headers=bearer(k["key"]), ).status_code == 200  # (factory token still there)
    client.headers.pop("X-Factory-Token")
    assert client.get("/api/requests", headers=bearer(k["key"])).status_code == 401  # customer key can't reach factory API
    assert client.post("/v1/run", json={"task": ""}, headers=bearer(k["key"])).status_code == 422
    client.headers["X-Factory-Token"] = tok
    assert client.post(f"/api/keys/{k['id']}/revoke").json()["revoked_at"]
    client.headers.pop("X-Factory-Token")
    assert client.post("/v1/run", json={"task": "x"}, headers=bearer(k["key"])).status_code == 401
    assert client.post("/api/keys/KEY-NOPE/revoke").status_code == 401


def test_key_management_needs_factory_auth_and_404(client, dispatch):
    a, d = dispatch
    assert client.post("/api/dispatches/DSP-NOPE/keys").status_code == 404
    assert client.get("/api/dispatches/DSP-NOPE/keys").status_code == 404
    assert client.post("/api/keys/KEY-NOPE/revoke").status_code == 404
    client.headers.pop("X-Factory-Token")
    assert client.post(f"/api/dispatches/{d['id']}/keys").status_code == 401


def test_rate_limit(client, dispatch, api_ready, monkeypatch):
    a, d = dispatch
    k = client.post(f"/api/dispatches/{d['id']}/keys").json()
    client.headers.pop("X-Factory-Token")
    monkeypatch.setattr(main, "RATE_PER_MIN", 2)
    codes = [client.post("/v1/run", json={"task": "x"}, headers=bearer(k["key"])).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_refuses_subscription_provider(client, dispatch, api_ready, monkeypatch):
    a, d = dispatch
    k = client.post(f"/api/dispatches/{d['id']}/keys").json()
    providers.set_provider("claude_subscription")
    client.headers.pop("X-Factory-Token")
    r = client.post("/v1/run", json={"task": "x"}, headers=bearer(k["key"]))
    assert r.status_code == 503 and "subscription" in r.json()["detail"]


def test_unconfigured_provider_503_and_error_run_502(client, dispatch, monkeypatch):
    a, d = dispatch
    k = client.post(f"/api/dispatches/{d['id']}/keys").json()
    tok = client.headers.pop("X-Factory-Token")
    assert client.post("/v1/run", json={"task": "x"}, headers=bearer(k["key"])).status_code == 503  # no ANTHROPIC key in test env

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    async def boom(*a):
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr(agent_service, "_execute", boom)
    r = client.post("/v1/run", json={"task": "x"}, headers=bearer(k["key"]))
    assert r.status_code == 502 and "secret internal detail" not in r.text and "RUN-" in r.text


def test_runs_use_dispatched_snapshot_and_usage_counted(client, dispatch, api_ready):
    a, d = dispatch
    k = client.post(f"/api/dispatches/{d['id']}/keys").json()
    client.put(f"/api/agents/{a['id']}", json={**SPEC, "agent_name": "Edited Later"})
    tok = client.headers.pop("X-Factory-Token")
    client.post("/v1/run", json={"task": "x"}, headers=bearer(k["key"]))
    assert api_ready == ["Sales Analyst"]  # snapshot, not the edit
    client.headers["X-Factory-Token"] = tok
    rec = client.get(f"/api/dispatches/{d['id']}/keys").json()[0]
    assert rec["request_count"] == 1 and rec["last_used_at"]
    assert client.get("/api/runs").json()[0]["kind"] == "api"


def test_get_dispatch_hides_spec(client, dispatch):
    a, d = dispatch
    got = client.get(f"/api/dispatches/{d['id']}").json()
    assert got["id"] == d["id"] and "spec" not in got
    assert client.get("/api/dispatches/DSP-NOPE").status_code == 404
