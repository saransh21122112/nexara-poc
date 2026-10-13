from langchain_core.messages import AIMessage

from backend import agent_service

REQ = dict(company_name="Acme", industry="Retail", agent_type="Skill", business_problem="We cannot analyse sales fast.",
           tasks="Summarise sales and find top product", existing_tools="Excel", required_output="Short report",
           contact_email="ops@acme.example", github_username="acme-dev", claude_status="have_claude_account")
SPEC = dict(agent_name="Sales Analyst", purpose="Analyse sales", business_problem="Slow analysis",
            instructions="You analyse sales data accurately using the tools provided.", capabilities=["analyse"],
            input_schema={"type": "object"}, output_schema={"type": "object"}, required_tools=["calculator"],
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


def test_agent_crud_run_evaluate(client, monkeypatch):
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
    assert r.status_code == 502 and len(calls) == 3  # two repair retries (the code gate reports what to change), then give up
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
    main._buckets.clear()


def test_spoofed_headers_cannot_evade_limits(client, monkeypatch):
    from backend import main
    main._buckets.clear()
    bad = lambda i, **h: client.post("/api/factory/login", json={"password": "bad"}, headers={"X-Forwarded-For": f"1.1.1.{i}", "Fly-Client-IP": f"2.2.2.{i}", **h}).status_code
    # default (no trusted proxy): forwarded headers are ignored entirely
    assert 429 in [bad(i) for i in range(15)]
    # behind one trusted proxy: only the rightmost (proxy-appended) entry counts; prepending fake IPs doesn't help
    main._buckets.clear()
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    codes = [client.post("/api/factory/login", json={"password": "bad"}, headers={"X-Forwarded-For": f"9.9.9.{i}, 7.7.7.7"}).status_code for i in range(15)]
    assert 429 in codes
    # ...while a genuinely different client (different rightmost entry) is counted separately
    assert client.post("/api/factory/login", json={"password": "nexara-demo"}, headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 200
    main._buckets.clear()


def test_security_headers(client):
    h = client.get("/api/health").headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"


def test_cloudflare_header_trusted_only_when_enabled(client, monkeypatch):
    from backend import main
    bad = lambda ip: client.post("/api/factory/login", json={"password": "bad"}, headers={"CF-Connecting-IP": ip}).status_code
    # default: the header is ignored, so rotating it cannot evade the lockout
    main._buckets.clear()
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("TRUST_CF_CONNECTING_IP", raising=False)
    assert 429 in [bad(f"5.5.5.{i}") for i in range(12)]
    # on Render (Cloudflare in front sets the header itself) each reported client is limited separately
    main._buckets.clear()
    monkeypatch.setenv("RENDER", "true")
    assert [bad("6.6.6.6") for _ in range(11)][-1] == 429
    assert client.post("/api/factory/login", json={"password": "nexara-demo"}, headers={"CF-Connecting-IP": "7.7.7.7"}).status_code == 200
    main._buckets.clear()


# ------------------------------------------------------------------ request-form assistant (public, rate limited)
def _assist_llm(monkeypatch, reply):
    from backend import providers, subscription
    async def fake(system, prompt):
        fake.seen = (system, prompt)
        return reply
    monkeypatch.setattr(subscription, "complete_json", fake)
    monkeypatch.setattr(providers, "missing", lambda p: None)
    monkeypatch.setattr(providers, "get_provider", lambda: "claude_subscription")
    return fake


def test_assist_fills_only_form_text_boxes(client, monkeypatch):
    _assist_llm(monkeypatch, '```json\n{"company_name":"Acme","agent_type":"Skill","tasks":"Summarise tickets","contact_email":"evil@x.io","github_username":"evil","os":"mac","bogus":"x"}\n```')
    r = client.post("/api/requests/assist", json={"description": "We are Acme and want a skill that summarises tickets"})
    assert r.status_code == 200 and r.json()["fields"] == {"company_name": "Acme", "agent_type": "Skill", "tasks": "Summarise tickets"}


def test_assist_edit_one_box_keeps_the_rest_and_sends_current_text(client, monkeypatch):
    fake = _assist_llm(monkeypatch, '{"tasks":"Summarise tickets and post to Slack","industry":"Retail"}')
    r = client.post("/api/requests/assist", json={"instruction": "also post to Slack", "field": "tasks", "current": {"tasks": "Summarise tickets", "industry": "SaaS"}})
    assert r.json()["fields"] == {"tasks": "Summarise tickets and post to Slack"}      # other boxes ignored when one is targeted
    assert "Summarise tickets" in fake.seen[1] and "also post to Slack" in fake.seen[1]


def test_assist_rejects_bad_input_and_bad_model_output(client, monkeypatch):
    _assist_llm(monkeypatch, "not json at all")
    assert client.post("/api/requests/assist", json={}).status_code == 422
    assert client.post("/api/requests/assist", json={"description": "x" * 1501}).status_code == 422
    assert client.post("/api/requests/assist", json={"description": "hi", "field": "contact_email"}).status_code == 422
    r = client.post("/api/requests/assist", json={"description": "hello there"})
    assert r.status_code == 502 and "by hand" in r.json()["detail"]
    _assist_llm(monkeypatch, '{"agent_type":"Agent"}')                                   # not one of the kinds
    assert client.post("/api/requests/assist", json={"description": "hello there"}).status_code == 502


def test_assist_unavailable_without_a_provider_and_is_rate_limited(client, monkeypatch):
    r = client.post("/api/requests/assist", json={"description": "hello there"})
    assert r.status_code == 503 and "by hand" in r.json()["detail"]
    monkeypatch.setenv("ASSIST_PER_HOUR_PER_IP", "2")
    codes = [client.post("/api/requests/assist", json={"description": "hello there"}).status_code for _ in range(3)]
    assert codes[-1] == 429


def test_assist_daily_cap(client, monkeypatch):
    from backend import main
    _assist_llm(monkeypatch, '{"tasks":"x y z"}')
    monkeypatch.setenv("ASSIST_PER_DAY", "1")
    main._assist_day[:] = [0, 0]
    assert client.post("/api/requests/assist", json={"description": "hello there"}).status_code == 200
    assert client.post("/api/requests/assist", json={"description": "hello again"}).status_code == 429


def test_request_kinds_and_legacy_rows(client):
    assert client.post("/api/requests", json={**REQ, "agent_type": "Data Analyst"}).status_code == 422   # retired kind refused on new input
    from backend import database as dbm
    rid = dbm.create_request({"company_name": "Old", "industry": "x", "agent_type": "Sales", "business_problem": "long enough text", "tasks": "do things",
                              "existing_tools": "x", "required_output": "y", "contact_email": "o@old.example", "github_username": "old-dev"})
    assert client.get(f"/api/requests/{rid['id']}").json()["agent_type"] == "Sales"                              # old stored value still shows
    assert "MCP server" in client.get("/api/health").json()["agent_types"]
