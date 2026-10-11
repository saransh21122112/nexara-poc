import io
import zipfile

import yaml
from langchain_core.messages import AIMessage

from backend import agent_service

REQ = dict(company_name="Acme", industry="Retail", agent_type="Data Analyst", business_problem="We cannot analyse sales fast.",
           tasks="Summarise sales and find top product", existing_tools="Excel", required_output="Short report",
           contact_email="ops@acme.example")
SPEC = dict(agent_name="Sales Analyst", purpose="Analyse sales", business_problem="Slow analysis",
            instructions="You analyse sales data accurately using the tools provided.", capabilities=["analyse"],
            input_schema={"type": "object"}, output_schema={"type": "object"}, required_tools=["calculator", "lookup_business_data"],
            data_sources=["sales"], safety_restrictions=["Never invent numbers"],
            test_scenarios=[dict(name="t1", task="Total units sold?", expected_behavior="uses tools", must_contain=["1"])],
            success_criteria=["accurate"])


def test_health_and_auth(client):
    assert client.get("/api/health").json()["providers"]["anthropic"]["configured"] is False
    client.headers.pop("X-Factory-Token")
    assert client.get("/api/requests").status_code == 401
    assert client.post("/api/factory/login", json={"password": "bad"}).status_code == 401


def test_request_flow_and_validation(client):
    assert client.post("/api/requests", json={**REQ, "contact_email": "nope"}).status_code == 422
    rid = client.post("/api/requests", json=REQ).json()["request_id"]
    assert client.get(f"/api/requests/{rid}").json()["company_name"] == "Acme"
    assert len(client.get("/api/requests").json()) == 1
    assert client.get("/api/requests/REQ-NOPE").status_code == 404
    s = client.get("/api/stats").json()
    assert (s["total_requests"], s["agents_generated"], s["agents_tested"]) == (1, 0, 0)


def test_generate_without_key_is_503_not_fake(client):
    rid = client.post("/api/requests", json=REQ).json()["request_id"]
    r = client.post(f"/api/requests/{rid}/generate-agent")
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]
    assert client.get("/api/agents").json() == []


def test_provider_switch(client):
    assert client.put("/api/provider", json={"provider": "openai"}).json()["current"] == "openai"
    rid = client.post("/api/requests", json=REQ).json()["request_id"]
    assert "OPENAI_API_KEY" in client.post(f"/api/requests/{rid}/generate-agent").json()["detail"]
    assert client.put("/api/provider", json={"provider": "bogus"}).status_code == 422


def _make_agent(client, monkeypatch):
    async def fake_spec(_):
        return SPEC, "anthropic", "fake-model"
    monkeypatch.setattr(agent_service, "generate_spec", fake_spec)
    rid = client.post("/api/requests", json=REQ).json()["request_id"]
    return client.post(f"/api/requests/{rid}/generate-agent").json()


def test_agent_crud_run_evaluate_export(client, monkeypatch):
    a = _make_agent(client, monkeypatch)
    assert a["spec"]["agent_name"] == "Sales Analyst"
    edited = {**SPEC, "agent_name": "Renamed"}
    assert client.put(f"/api/agents/{a['id']}", json=edited).json()["spec"]["agent_name"] == "Renamed"
    assert client.put(f"/api/agents/{a['id']}", json={"agent_name": "x"}).status_code == 422

    async def fake_exec(spec, task, p):
        return {"response": "Total 1 unit", "tool_calls": [{"tool": "calculator", "args": {}, "ok": True}],
                "input_tokens": 5, "output_tokens": 7}
    monkeypatch.setattr(agent_service, "_execute", fake_exec)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    run = client.post(f"/api/agents/{a['id']}/run", json={"task": "hi"}).json()
    assert run["status"] == "success" and run["output_tokens"] == 7 and run["provider"] == "anthropic"
    ev = client.post(f"/api/agents/{a['id']}/evaluate").json()
    assert ev["pass_rate"] == 1.0 and ev["results"][0]["passed"]
    assert client.get("/api/stats").json()["agents_tested"] == 1
    assert len(client.get("/api/runs").json()) == 2

    for fmt, expect in (("crewai", "config/agents.yaml"), ("portable", "Dockerfile")):
        z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/agents/{a['id']}/export?format={fmt}").content))
        assert expect in z.namelist()
    yaml.safe_load(zipfile.ZipFile(io.BytesIO(client.get(f"/api/agents/{a['id']}/export?format=crewai").content)).read("config/tasks.yaml"))


class FakeLLM:
    """Scripted chat model: first asks for a tool call, then answers."""
    def __init__(self):
        self.n = 0

    def bind_tools(self, _):
        return self

    async def ainvoke(self, _):
        self.n += 1
        if self.n == 1:
            return AIMessage(content="", tool_calls=[{"name": "calculator", "args": {"expression": "2+3"}, "id": "c1"}])
        return AIMessage(content="The answer is 5")


async def test_langgraph_tool_loop(monkeypatch):
    graph = agent_service.build_graph(FakeLLM(), ["calculator"])
    from langchain_core.messages import HumanMessage
    out = await graph.ainvoke({"messages": [HumanMessage("2+3?")]})
    tool_msg = out["messages"][-2]
    assert '"result": 5' in tool_msg.content and out["messages"][-1].content == "The answer is 5"


async def test_disabled_tool_is_refused():
    llm = FakeLLM()
    graph = agent_service.build_graph(llm, [])
    from langchain_core.messages import HumanMessage
    out = await graph.ainvoke({"messages": [HumanMessage("x")]})
    assert '"ok": false' in out["messages"][-2].content


def test_invalid_model_output_is_502_and_nothing_saved(client, monkeypatch):
    from backend import providers, subscription
    calls = []

    async def garbage(*a):
        calls.append(1)
        return "not json at all"
    monkeypatch.setattr(subscription, "complete_json", garbage)
    monkeypatch.setattr(providers, "missing", lambda p: None)
    client.put("/api/provider", json={"provider": "claude_subscription"})
    rid = client.post("/api/requests", json=REQ).json()["request_id"]
    r = client.post(f"/api/requests/{rid}/generate-agent")
    assert r.status_code == 502 and len(calls) == 2  # one retry, then give up
    assert client.get("/api/agents").json() == []


def test_upstream_exception_is_logged_as_error_run(client, monkeypatch):
    a = _make_agent(client, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    async def boom(*a):
        raise RuntimeError("rate limited")
    monkeypatch.setattr(agent_service, "_execute", boom)
    run = client.post(f"/api/agents/{a['id']}/run", json={"task": "hi"}).json()
    assert run["status"] == "error" and "rate limited" in run["error"]
    ev = client.post(f"/api/agents/{a['id']}/evaluate").json()
    assert ev["pass_rate"] == 0.0 and not ev["results"][0]["passed"]


def test_login_bruteforce_is_limited(client):
    from backend import main
    main._buckets.clear()
    codes = [client.post("/api/factory/login", json={"password": "bad"}).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
    # even the right password is refused while locked out
    assert client.post("/api/factory/login", json={"password": "nexara-demo"}).status_code == 429
    main._buckets.clear()
    assert client.post("/api/factory/login", json={"password": "nexara-demo"}).status_code == 200


def test_public_request_form_is_rate_limited(client, monkeypatch):
    from backend import main
    main._buckets.clear()
    monkeypatch.setenv("REQUESTS_PER_HOUR_PER_IP", "2")
    codes = [client.post("/api/requests", json=REQ).status_code for _ in range(3)]
    assert codes == [201, 201, 429]
    # a different client IP is counted separately
    assert client.post("/api/requests", json=REQ, headers={"Fly-Client-IP": "9.9.9.9"}).status_code == 201
    main._buckets.clear()
