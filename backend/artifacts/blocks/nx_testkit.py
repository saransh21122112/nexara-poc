"""Runs an artifact's declared examples against the REAL server or script (vetted block, stdlib only; shipped in every delivered artifact).
Usage: python nx_testkit.py path/to/tests.json     Exit code 0 only if every check passed; a JSON report is printed on stdout.
tests.json: {"kind": "mcp"|"script", "name", "entry", "fixtures": {...}, "tools": [...] | "examples": [...], "config_example": {...}}
Fixtures are declarative (no code): sqlite_sql, files, http routes, env, config template with {{sqlite_path}} {{docs_dir}} {{http_base}} {{tmp}}."""
import json
import os
import queue
import sqlite3
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TIMEOUT = 20


def _expect(text, is_error, ex):
    low = text.lower()
    if ex.get("expect_error"):
        if not is_error:
            return False, "expected an error, got success"
        if ex["expect_error"].lower() not in low:
            return False, f"error did not mention {ex['expect_error']!r}: {text[:200]}"
    elif is_error:
        return False, f"unexpected error: {text[:200]}"
    for s in ex.get("expect_contains") or []:
        if s.lower() not in low:
            return False, f"output is missing {s!r}: {text[:200]}"
    for s in ex.get("expect_not_contains") or []:
        if s.lower() in low:
            return False, f"output must not contain {s!r}"
    return True, ""


class _Routes(BaseHTTPRequestHandler):
    routes = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        r = self.routes.get("GET " + self.path) or self.routes.get("GET " + self.path.split("?")[0])
        if r is None:
            self.send_response(404); self.end_headers(); return
        body = json.dumps(r["json"]).encode() if "json" in r else str(r.get("body", "")).encode()
        self.send_response(int(r.get("status", 200)))
        self.send_header("Content-Type", "application/json" if "json" in r else "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    do_POST = do_PUT = do_DELETE = lambda self: (self.send_response(405), self.end_headers())


def _safe_env(env):
    """Fixture env vars may add data (a fake token), never change how the interpreter or the guard starts."""
    bad = ("NX_", "PYTHON", "LD_", "DYLD_")
    return {k: str(v) for k, v in (env or {}).items() if isinstance(k, str) and k not in ("PATH", "HOME", "TMPDIR") and not k.startswith(bad)}


def _build(fx, tmp, config_example):
    ph = {"{{tmp}}": str(tmp)}
    if fx.get("sqlite_sql"):
        p = tmp / "test.db"
        c = sqlite3.connect(p); c.executescript(fx["sqlite_sql"]); c.commit(); c.close()
        ph["{{sqlite_path}}"] = str(p)
    docs = tmp / "docs"
    docs.mkdir(exist_ok=True)
    for rel, content in (fx.get("files") or {}).items():
        t = (docs / rel).resolve()
        if docs.resolve() not in t.parents:
            raise ValueError("fixture file escapes the docs folder")
        t.parent.mkdir(parents=True, exist_ok=True); t.write_text(content, encoding="utf-8")
    ph["{{docs_dir}}"] = str(docs)
    server = None
    if fx.get("http"):
        _Routes.routes = dict(fx["http"])
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Routes); server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        ph["{{http_base}}"] = f"http://127.0.0.1:{server.server_port}"
        ph["{{http_host}}"] = "127.0.0.1"
    cfg = json.dumps(fx.get("config") or config_example or {})
    for k, v in ph.items():
        cfg = cfg.replace(k, v.replace("\\", "\\\\").replace('"', '\\"'))
    cfg_path = tmp / "config.json"
    cfg_path.write_text(cfg, encoding="utf-8")
    return cfg_path, server


def _cmd(entry):
    guard = os.environ.get("NX_GUARD_PATH")
    return [sys.executable] + (["-I", guard] if guard else []) + [str(entry)]


class _Rpc:
    def __init__(self, cmd, env, cwd):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=cwd)
        self.q, self.n = queue.Queue(), 0
        threading.Thread(target=lambda: [self.q.put(l) for l in self.p.stdout] or self.q.put(None), daemon=True).start()
        threading.Thread(target=lambda: self.p.stderr.read(), daemon=True).start()

    def call(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.p.stdin.write(json.dumps(msg) + "\n"); self.p.stdin.flush()
        if notify:
            return None
        line = self.q.get(timeout=TIMEOUT)
        if line is None:
            raise RuntimeError("the server exited")
        return json.loads(line)

    def close(self):
        try:
            self.p.stdin.close(); self.p.wait(timeout=5)
        except Exception:                                   # noqa: BLE001
            self.p.kill()


def _run_mcp(spec, base, tmp, cfg_path):
    res = []

    def check(name, ok, detail=""):
        res.append({"name": name, "ok": bool(ok), "detail": detail})
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp), "TMPDIR": str(tmp), "NX_CONFIG": str(cfg_path), **_safe_env(spec.get("fixtures", {}).get("env"))}
    rpc = _Rpc(_cmd(base / spec["entry"]), env, str(tmp))
    try:
        init = rpc.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "nx-testkit", "version": "1"}})
        check("initialize", "result" in init and init["result"].get("serverInfo", {}).get("name"), json.dumps(init)[:200])
        rpc.call("notifications/initialized", notify=True)
        check("ping", rpc.call("ping").get("result") == {})
        tools = rpc.call("tools/list")["result"]["tools"]
        want = {t["name"] for t in spec["tools"]}
        check("tools/list matches the spec", {t["name"] for t in tools} == want, f"server has {sorted(t['name'] for t in tools)}, spec has {sorted(want)}")
        check("every tool is read-only and documented", all(t.get("annotations", {}).get("readOnlyHint") is True and t.get("description") and t.get("inputSchema") for t in tools))
        check("unknown tools are refused", "error" in rpc.call("tools/call", {"name": "definitely_not_a_tool", "arguments": {}}))
        for t in spec["tools"]:
            for i, ex in enumerate(t["examples"], 1):
                r = rpc.call("tools/call", {"name": t["name"], "arguments": ex["args"]})
                if "error" in r:
                    ok, why = _expect(r["error"].get("message", ""), True, ex)
                else:
                    text = "".join(c.get("text", "") for c in r["result"].get("content", []))
                    ok, why = _expect(text, bool(r["result"].get("isError")), ex)
                check(f"{t['name']} example {i}", ok, why)
    except Exception as e:                                  # noqa: BLE001
        check("server session", False, f"{type(e).__name__}: {e}")
    finally:
        rpc.close()
    return res


def _run_script(spec, base, tmp, cfg_path):
    res = []
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp), "TMPDIR": str(tmp), "NX_CONFIG": str(cfg_path), **_safe_env(spec.get("fixtures", {}).get("env"))}
    entry = base / spec["entry"]
    for i, ex in enumerate(spec["examples"], 1):
        p = subprocess.run(_cmd(entry), input=json.dumps(ex["args"]), capture_output=True, text=True, env=env, cwd=str(tmp), timeout=TIMEOUT)
        try:
            out = json.loads(p.stdout.strip().splitlines()[-1])
            is_err, text = not out.get("ok"), (out.get("error") if not out.get("ok") else json.dumps(out.get("result")))
        except (ValueError, IndexError):
            is_err, text = True, f"not JSON on stdout: {p.stdout[:120]!r} {p.stderr[:120]!r}"
        ok, why = _expect(str(text), is_err, ex)
        res.append({"name": f"example {i}", "ok": ok, "detail": why})
    p = subprocess.run(_cmd(entry) + ["--selftest"], capture_output=True, text=True, env=env, cwd=str(tmp), timeout=TIMEOUT)
    res.append({"name": "--selftest", "ok": p.returncode == 0, "detail": p.stdout[:200]})
    return res


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]).resolve()
    spec = json.loads(path.read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp(prefix="nx-test-"))
    cfg_path, server = _build(spec.get("fixtures") or {}, tmp, spec.get("config_example"))
    try:
        results = (_run_mcp if spec["kind"] == "mcp" else _run_script)(spec, path.parent, tmp, cfg_path)
    finally:
        if server:
            server.shutdown()
    failed = [r for r in results if not r["ok"]]
    print(json.dumps({"ok": not failed, "passed": len(results) - len(failed), "failed": len(failed), "results": results}, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
