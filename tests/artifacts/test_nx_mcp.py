"""The vetted MCP runtime block: protocol behaviour must be exactly right because every delivered server depends on it."""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.artifacts.blocks import nx_mcp as m

ROOT = Path(__file__).resolve().parents[2]


def server():
    def add(a):
        if a["b"] == 0:
            raise m.ToolError("b must not be zero")
        return {"sum": a["a"] + a["b"]}

    def crash(a):
        raise RuntimeError("secret internal path /var/lib/x and password hunter2")
    schema = {"type": "object", "properties": {"a": {"type": "integer", "minimum": 0, "maximum": 100}, "b": {"type": "integer"}, "mode": {"enum": ["x", "y"]}},
              "required": ["a", "b"], "additionalProperties": False}
    return m.Server("demo", "1.2.3", [m.Tool("add", "Add numbers.", schema, add), m.Tool("crash", "Crashes.", {"type": "object"}, crash),
                                      m.Tool("big", "Big output.", {"type": "object"}, lambda a: "x" * 150_000),
                                      m.Tool("txt", "Text.", {"type": "object"}, lambda a: "plain")], instructions="Be careful.")


def call(srv, method, params=None, rid=1):
    return srv.handle({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})


def test_initialize_negotiates_version_and_declares_tools_only():
    s = server()
    r = call(s, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})["result"]
    assert r["protocolVersion"] == "2024-11-05" and r["capabilities"] == {"tools": {"listChanged": False}}
    assert r["serverInfo"] == {"name": "demo", "version": "1.2.3"} and r["instructions"] == "Be careful."
    assert call(s, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == m.SUPPORTED_VERSIONS[0]
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None and s.initialized


def test_tools_list_has_schema_and_read_only_annotation():
    t = {x["name"]: x for x in call(server(), "tools/list")["result"]["tools"]}
    assert t["add"]["inputSchema"]["required"] == ["a", "b"] and t["add"]["annotations"] == {"readOnlyHint": True}


def test_tool_call_success_returns_text_and_structured_content():
    r = call(server(), "tools/call", {"name": "add", "arguments": {"a": 2, "b": 3}})["result"]
    assert r["isError"] is False and json.loads(r["content"][0]["text"]) == {"sum": 5} and r["structuredContent"] == {"sum": 5}
    assert call(server(), "tools/call", {"name": "txt"})["result"]["content"][0]["text"] == "plain"


@pytest.mark.parametrize("args,needle", [({"a": "2", "b": 1}, "expected integer"), ({"a": 200, "b": 1}, "above 100"), ({"b": 1}, "a: required"),
                                         ({"a": 1, "b": 1, "x": 1}, "not allowed"), ({"a": 1, "b": 1, "mode": "z"}, "one of"), ({"a": True, "b": 1}, "expected integer")])
def test_invalid_arguments_are_rejected_before_the_handler_runs(args, needle):
    r = call(server(), "tools/call", {"name": "add", "arguments": args})["result"]
    assert r["isError"] is True and needle in r["content"][0]["text"]


def test_expected_errors_are_shown_but_crashes_never_leak_internals(capsys):
    r = call(server(), "tools/call", {"name": "add", "arguments": {"a": 1, "b": 0}})["result"]
    assert r["isError"] is True and "b must not be zero" in r["content"][0]["text"]
    r = call(server(), "tools/call", {"name": "crash"})["result"]
    text = r["content"][0]["text"]
    assert r["isError"] is True and text == "Internal error while running this tool." and "hunter2" not in text and "/var/lib" not in text
    assert "hunter2" in capsys.readouterr().err                       # details go to stderr only


def test_unknown_methods_tools_and_bad_requests():
    s = server()
    assert call(s, "nope")["error"]["code"] == -32601
    assert call(s, "tools/call", {"name": "ghost"})["error"]["code"] == -32602
    assert s.handle({"id": 1, "method": "ping"})["error"]["code"] == -32600
    assert s.handle("junk")["error"]["code"] == -32600
    assert s.handle({"jsonrpc": "2.0", "method": "weird/notification"}) is None          # notifications are never answered
    assert call(s, "ping")["result"] == {}


def test_huge_output_is_truncated():
    t = call(server(), "tools/call", {"name": "big"})["result"]["content"][0]["text"]
    assert len(t) < 100_100 and t.endswith("[output truncated]")


def test_duplicate_tool_names_are_refused():
    with pytest.raises(ValueError):
        m.Server("x", "1", [m.Tool("a", "d", {}, lambda a: 1), m.Tool("a", "d", {}, lambda a: 1)])


def test_stdio_loop_handles_garbage_batches_blank_lines_and_oversize():
    s = server()
    lines = ["", "not json", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
             json.dumps([{"jsonrpc": "2.0", "id": 2, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"}]),
             json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "txt", "arguments": {"p": "y" * 1_100_000}}})]
    out = io.StringIO()
    s.serve(stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out)
    got = [json.loads(x) for x in out.getvalue().splitlines()]
    assert got[0]["error"]["code"] == -32700 and got[1]["id"] == 1 and got[2] == [{"jsonrpc": "2.0", "id": 2, "result": {}}] and got[3]["error"]["code"] == -32600
    assert all("\n" not in json.dumps(x) for x in got)


def test_real_process_over_stdio_only_protocol_on_stdout(tmp_path):
    script = tmp_path / "srv.py"
    script.write_text("import sys\nsys.path.insert(0, %r)\nimport nx_mcp as m\nprint('log line', file=sys.stderr)\n"
                      "m.Server('p', '1', [m.Tool('t', 'd', {'type': 'object'}, lambda a: {'ok': 1})]).serve()\n" % str(ROOT / "backend" / "artifacts" / "blocks"))
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "t", "arguments": {}}}]
    p = subprocess.run([sys.executable, "-I", str(script)], input="\n".join(json.dumps(x) for x in msgs) + "\n", capture_output=True, text=True, timeout=20)
    out = [json.loads(x) for x in p.stdout.splitlines()]
    assert [o["id"] for o in out] == [1, 2, 3] and out[2]["result"]["structuredContent"] == {"ok": 1} and "log line" in p.stderr
