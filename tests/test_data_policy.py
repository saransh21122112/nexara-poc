"""Spec 007 behaviours 9-11: no dummy dataset; customers' own examples; AI-generated test cases."""
import json
from pathlib import Path

from backend import agent_service, providers, tools
from backend.models import ASSIST_FIELDS
from tests.test_api import REQ, SPEC

ROOT = Path(__file__).resolve().parent.parent


def test_the_sample_dataset_and_its_tool_are_gone():
    assert "lookup_business_data" not in tools.REGISTRY and not (ROOT / "data" / "sample_business_data.json").exists()
    assert not tools.run_tool("lookup_business_data", {"dataset": "sales"})["ok"]
    assert "calculator" in tools.REGISTRY


def test_spec_generator_assumes_no_dataset_and_demands_inline_inputs():
    s = agent_service.SPEC_SYSTEM.lower()
    assert "lookup_business_data" not in s and "inline" in s and "customer's own" in s


def test_old_agents_with_the_removed_tool_still_run_and_adapt(client):
    legacy = {**SPEC, "required_tools": ["calculator", "lookup_business_data"]}
    assert agent_service.enabled_tools(legacy) == ["calculator"]


def test_request_examples_are_optional_stored_and_capped(client):
    assert client.post("/api/requests", json=REQ).status_code == 201
    r = client.post("/api/requests", json={**REQ, "examples": "Order 1: 3 items, 40 EUR, late by 2 days"})
    assert r.status_code == 201 and r.json()["request"]["examples"].startswith("Order 1")
    assert client.post("/api/requests", json={**REQ, "examples": "x" * 4001}).status_code == 422
    assert "examples" in ASSIST_FIELDS
    text = agent_service._req_text(r.json()["request"])
    assert "Order 1: 3 items" in text


def test_requests_without_examples_still_work_in_the_generator_text(client):
    r = client.post("/api/requests", json=REQ).json()["request"]
    assert "examples" not in agent_service._req_text(r).lower() or "none" in agent_service._req_text(r).lower()


def _agent(client, monkeypatch):
    async def fake_spec(_):
        return SPEC, "anthropic", "m"
    monkeypatch.setattr(agent_service, "generate_spec", fake_spec)
    rid = client.post("/api/requests", json=REQ).json()["request_id"]
    return client.post(f"/api/requests/{rid}/generate-agent").json()


def _llm(monkeypatch, reply):
    from backend import subscription
    seen = []

    async def fake(system, prompt):
        seen.append(prompt)
        return reply
    monkeypatch.setattr(subscription, "complete_json", fake)
    monkeypatch.setattr(providers, "missing", lambda p: None)
    monkeypatch.setattr(providers, "get_provider", lambda: "claude_subscription")
    return seen


GOOD = [{"name": "Late order", "task": "Here is an order: ORD-9 shipped 3 days late for 40 EUR. Draft a reply.", "expected_behavior": "apologises, no refund promise",
         "must_contain": ["sorry"]}]


def test_generate_tests_returns_validated_scenarios_and_saves_nothing(client, monkeypatch):
    a = _agent(client, monkeypatch)
    seen = _llm(monkeypatch, json.dumps({"scenarios": GOOD}))
    r = client.post(f"/api/agents/{a['id']}/generate-tests", json={"count": 2, "focus": "angry customers"})
    assert r.status_code == 200 and r.json()["scenarios"][0]["name"] == "Late order"
    assert "angry customers" in seen[0] and "inline" in seen[0].lower()
    assert client.get(f"/api/agents/{a['id']}").json()["spec"]["test_scenarios"] == SPEC["test_scenarios"]


def test_generate_tests_rejects_bad_output_bad_input_and_missing_auth(client, monkeypatch):
    a = _agent(client, monkeypatch)
    _llm(monkeypatch, "not json")
    assert client.post(f"/api/agents/{a['id']}/generate-tests", json={}).status_code == 502
    _llm(monkeypatch, json.dumps({"scenarios": [{"name": "x"}]}))
    assert client.post(f"/api/agents/{a['id']}/generate-tests", json={}).status_code == 502
    assert client.post(f"/api/agents/{a['id']}/generate-tests", json={"count": 99}).status_code == 422
    assert client.post("/api/agents/AGT-NOPE/generate-tests", json={}).status_code == 404
    client.headers.pop("X-Factory-Token")
    assert client.post(f"/api/agents/{a['id']}/generate-tests", json={}).status_code == 401


# ---- adding accepted test cases saves through a small dedicated call (not a full-spec PUT)
def test_accepted_test_cases_are_appended_deduplicated_and_validated(client, monkeypatch):
    a = _agent(client, monkeypatch)
    new = [{"name": "Late order", "task": "Order ORD-9 is 3 days late. Draft a reply.", "expected_behavior": "apologises", "must_contain": ["sorry"]},
           {"name": "No data", "task": "Reply to an empty message.", "expected_behavior": "asks for details"}]
    r = client.post(f"/api/agents/{a['id']}/test-scenarios", json={"scenarios": new})
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["spec"]["test_scenarios"]]
    assert names == [s["name"] for s in SPEC["test_scenarios"]] + ["Late order", "No data"]
    again = client.post(f"/api/agents/{a['id']}/test-scenarios", json={"scenarios": new}).json()          # same cases again: no duplicates
    assert [s["name"] for s in again["spec"]["test_scenarios"]] == names
    assert client.get(f"/api/agents/{a['id']}").json()["spec"]["purpose"] == SPEC["purpose"]             # nothing else touched


def test_test_case_save_rejects_bad_input_unknown_agents_and_missing_auth(client, monkeypatch):
    a = _agent(client, monkeypatch)
    ok = {"name": "x", "task": "do it", "expected_behavior": "done"}
    assert client.post(f"/api/agents/{a['id']}/test-scenarios", json={"scenarios": [{"name": "x"}]}).status_code == 422
    assert client.post(f"/api/agents/{a['id']}/test-scenarios", json={"scenarios": []}).status_code == 422
    assert client.post(f"/api/agents/{a['id']}/test-scenarios", json={"scenarios": [{**ok, "name": f"n{i}"} for i in range(11)]}).status_code == 422
    assert client.post("/api/agents/AGT-NOPE/test-scenarios", json={"scenarios": [ok]}).status_code == 404
    for i in range(2):                                                                                     # 1 existing + 16 = 17 cases
        assert client.post(f"/api/agents/{a['id']}/test-scenarios", json={"scenarios": [{**ok, "name": f"m{i}{j}", "task": f"t{i}{j}"} for j in range(8)]}).status_code == 200
    capped = client.post(f"/api/agents/{a['id']}/test-scenarios", json={"scenarios": [{**ok, "name": f"over{j}", "task": f"o{j}"} for j in range(4)]})
    assert capped.status_code == 409 and "20" in capped.json()["detail"]                                  # a build keeps a sensible number of cases
    client.headers.pop("X-Factory-Token")
    assert client.post(f"/api/agents/{a['id']}/test-scenarios", json={"scenarios": [ok]}).status_code == 401
