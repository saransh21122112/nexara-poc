"""Runs the real LangChain clients (ChatAnthropic / ChatOpenAI) against local fake API servers,
so the request/response plumbing is exercised without keys. Does NOT prove behaviour of the live APIs."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from backend import agent_service, providers
from tests.test_api import REQ, SPEC

SEEN = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append((self.path, body))
        if self.path.endswith("/messages"):  # Anthropic
            names = [t["name"] for t in body.get("tools", [])]
            if not names:  # spec generation: plain JSON text, wrapped in a fence to test fence stripping
                content = [{"type": "text", "text": "```json\n" + json.dumps(SPEC) + "\n```"}]
            elif any(m["role"] == "user" and isinstance(m["content"], list) and m["content"][0].get("type") == "tool_result"
                     for m in body["messages"]):
                content = [{"type": "text", "text": "Answer is 5"}]
            else:
                content = [{"type": "tool_use", "id": "t2", "name": "calculator", "input": {"expression": "2+3"}}]
            self._send({"id": "m", "type": "message", "role": "assistant", "model": body["model"], "content": content,
                        "stop_reason": "tool_use" if content[0]["type"] == "tool_use" else "end_turn",
                        "usage": {"input_tokens": 11, "output_tokens": 7}})
        else:  # OpenAI chat completions
            names = [t["function"]["name"] for t in body.get("tools", [])]
            had_tool_result = any(m["role"] == "tool" for m in body["messages"])
            if "AgentSpec" in names:
                msg = {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "AgentSpec", "arguments": json.dumps(SPEC)}}]}
            elif had_tool_result:
                msg = {"role": "assistant", "content": "Answer is 5"}
            else:
                msg = {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c2", "type": "function", "function": {"name": "calculator", "arguments": '{"expression":"2+3"}'}}]}
            self._send({"id": "x", "object": "chat.completion", "created": 1, "model": body["model"],
                        "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}})


@pytest.fixture(scope="module")
def fake_url():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(params=["anthropic", "openai"])
def prov(request, fake_url, monkeypatch):
    p = request.param
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("ANTHROPIC_API_URL", fake_url)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", fake_url)
    monkeypatch.setenv("OPENAI_BASE_URL", fake_url + "/v1")
    providers.set_provider(p)
    yield p
    providers._current = None


async def test_generate_spec_real_client(prov):
    spec, p, model = await agent_service.generate_spec({**REQ, "id": "REQ-1"})
    assert p == prov and spec["agent_name"] == "Sales Analyst" and model == providers.model_name(prov)


async def test_agent_loop_real_client(prov):
    out = await agent_service._execute({**SPEC}, "2+3?", prov)
    assert out["response"] == "Answer is 5"
    assert out["tool_calls"][0]["tool"] == "calculator" and out["tool_calls"][0]["ok"]
    assert out["tool_calls"][0]["result"]["result"] == 5
    assert out["input_tokens"] == 22 and out["output_tokens"] == 14  # two model turns


def test_an_invalid_ai_provider_value_is_reported_to_staff_not_silently_ignored(monkeypatch):
    from backend import providers
    providers._current = None
    monkeypatch.setenv("AI_PROVIDER", "CLAUDE_CODE_OAUTH_TOKEN")        # a real mistake: the token's name pasted as the value
    s = providers.status()
    assert s["current"] == "anthropic" and "AI_PROVIDER" in s["warning"] and "claude_subscription" in s["warning"]
    monkeypatch.setenv("AI_PROVIDER", "claude_subscription")
    assert providers.status()["warning"] is None and providers.get_provider() == "claude_subscription"
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    assert providers.status()["warning"] is None
