"""Builds a real customer package, extracts it and RUNS it (HTTP auth, tool loop via real SDKs against fake servers, MCP)."""
import asyncio
import importlib
import io
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend import agent_service, dispatch
from tests.test_api import REQ, SPEC
from tests.test_providers import fake_url  # noqa: F401  (fixture)


def test_dispatch_api_flow(client, monkeypatch):
    async def fake_spec(_):
        return SPEC, "anthropic", "m"
    monkeypatch.setattr(agent_service, "generate_spec", fake_spec)
    rid = client.post("/api/requests", json=REQ).json()["request_id"]
    a = client.post(f"/api/requests/{rid}/generate-agent").json()
    d = client.post(f"/api/agents/{a['id']}/dispatch").json()
    assert d["status"] == "draft" and d["to_email"] == REQ["contact_email"] and "Sales Analyst" in d["subject"]
    assert "sk-" not in d["body"] and "API key" in d["body"]
    # edits after dispatch don't change the dispatched snapshot
    client.put(f"/api/agents/{a['id']}", json={**SPEC, "agent_name": "Changed"})
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/dispatches/{d['id']}/package").content))
    assert '"Sales Analyst"' in z.read("spec.json").decode() and {"server.py", "mcp_server.py", "runtime.py", "tools.py", "Dockerfile"} <= set(z.namelist())
    assert client.get(f"/api/agents/{a['id']}/dispatches").json()[0]["status"] == "draft"
    assert client.get("/api/stats").json()["agents_dispatched"] == 0
    assert client.post(f"/api/dispatches/{d['id']}/mark-sent").json()["status"] == "sent"
    assert client.get("/api/stats").json()["agents_dispatched"] == 1
    assert client.get("/api/dispatches/DSP-NOPE/package").status_code == 404
    assert client.post("/api/agents/AGT-NOPE/dispatch").status_code == 404
    client.headers.pop("X-Factory-Token")
    assert client.post(f"/api/agents/{a['id']}/dispatch").status_code == 401


@pytest.fixture
def pkg(tmp_path, monkeypatch):
    dispatch_zip = dispatch.package(SPEC, "Acme")
    zipfile.ZipFile(io.BytesIO(dispatch_zip)).extractall(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    for m in ("tools", "runtime", "server", "mcp_server"):
        sys.modules.pop(m, None)
    yield tmp_path
    for m in ("tools", "runtime", "server", "mcp_server"):
        sys.modules.pop(m, None)


@pytest.mark.parametrize("prov", ["anthropic", "openai"])
def test_package_runtime_tool_loop(pkg, fake_url, monkeypatch, prov):  # noqa: F811
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", fake_url)
    monkeypatch.setenv("OPENAI_BASE_URL", fake_url + "/v1")
    monkeypatch.setenv("AGENT_PROVIDER", prov)
    rt = importlib.import_module("runtime")
    out = rt.run("2+3?")
    assert out["response"] == "Answer is 5" and out["tool_calls"] == [{"tool": "calculator", "args": {"expression": "2+3"}, "ok": True}]


def test_package_server_requires_key_and_runs(pkg, monkeypatch):
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        importlib.import_module("server")
    sys.modules.pop("server", None)
    monkeypatch.setenv("AGENT_API_KEY", "k" * 20)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_PROVIDER", raising=False)
    srv = importlib.import_module("server")
    c = TestClient(srv.app)
    assert c.get("/health").json()["agent"] == "Sales Analyst"
    assert c.post("/run", json={"task": "x"}).status_code == 401
    assert c.post("/run", json={"task": "x"}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.post("/run", json={"task": "x"}, headers={"Authorization": "Bearer " + "k" * 20})
    assert r.status_code == 503 and "API_KEY" in r.json()["detail"]  # honest error, no fake answer
    assert c.post("/run", json={"task": ""}, headers={"Authorization": "Bearer " + "k" * 20}).status_code == 422


def test_package_mcp_lists_tool(pkg):
    mod = importlib.import_module("mcp_server")
    tools = asyncio.run(mod.mcp.list_tools())
    assert [t.name for t in tools] == ["ask_agent"]


def test_package_tools_use_bundled_data(pkg):
    t = importlib.import_module("tools")
    assert t.run_tool("lookup_business_data", {"dataset": "orders"})["output"]["count"] == 3


def test_zip_entries_are_world_readable():
    for blob in (dispatch.package(SPEC, "Acme"), __import__("backend.export", fromlist=["x"]).portable_bundle(SPEC)):
        for info in zipfile.ZipFile(io.BytesIO(blob)).infolist():
            assert (info.external_attr >> 16) & 0o444 == 0o444, info.filename
