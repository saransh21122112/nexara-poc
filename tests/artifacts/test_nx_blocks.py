"""The remaining vetted blocks: config, audit, redact, db, http, fs, cli."""
import io
import json
import os
import sqlite3
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backend.artifacts.blocks import nx_audit, nx_cli, nx_config, nx_db, nx_fs, nx_http, nx_redact


# ------------------------------------------------------------------ config
def test_config_loads_from_path_env_or_default_and_explains_what_is_missing(tmp_path, monkeypatch):
    f = tmp_path / "config.json"; f.write_text(json.dumps({"a": 1}))
    assert nx_config.load(str(f)) == {"a": 1}
    monkeypatch.setenv("NX_CONFIG", str(f)); assert nx_config.load() == {"a": 1}
    monkeypatch.delenv("NX_CONFIG")
    with pytest.raises(nx_config.ConfigError) as e:
        nx_config.load(str(tmp_path / "nope.json"))
    assert "config.example.json" in str(e.value)
    f.write_text("{bad"); 
    with pytest.raises(nx_config.ConfigError):
        nx_config.load(str(f))


def test_config_refuses_inline_secrets_and_reads_secrets_from_named_env_vars(tmp_path, monkeypatch):
    for bad in ({"password": "hunter2"}, {"api_key": "sk-123"}, {"db": {"token": "abc"}}, {"dsn": "postgres://u:pw@host/db"}, {"base_url": "https://user:pw@example.com"}):
        f = tmp_path / "c.json"; f.write_text(json.dumps(bad))
        with pytest.raises(nx_config.ConfigError) as e:
            nx_config.load(str(f))
        assert "environment variable" in str(e.value) and "hunter2" not in str(e.value) and "pw" not in str(e.value).replace("password", "")
    f.write_text(json.dumps({"dsn_env": "MY_DSN", "api_key_env": "MY_KEY"}))
    cfg = nx_config.load(str(f))
    monkeypatch.setenv("MY_DSN", "postgres://u:pw@h/db")
    assert nx_config.secret(cfg, "dsn_env") == "postgres://u:pw@h/db"
    monkeypatch.delenv("MY_KEY", raising=False)
    with pytest.raises(nx_config.ConfigError) as e:
        nx_config.secret(cfg, "api_key_env")
    assert "MY_KEY" in str(e.value)


# ------------------------------------------------------------------ audit
def test_audit_writes_json_lines_without_result_data_or_secrets_and_never_raises(tmp_path, capsys):
    p = tmp_path / "logs" / "audit.jsonl"
    a = nx_audit.Audit(str(p))
    a.log(tool="run_query", sql="SELECT 1", outcome="ok", rows=[{"email": "a@b.c"}], result="x", password="p", Authorization="Bearer x", note="n" * 2000)
    line = json.loads(p.read_text().splitlines()[0])
    assert line["tool"] == "run_query" and line["rows"] == "<omitted>" and line["result"] == "<omitted>" and line["password"] == "<omitted>" and line["Authorization"] == "<omitted>"
    assert len(line["note"]) <= 520 and "ts" in line and oct(p.stat().st_mode)[-3:] == "600"
    nx_audit.Audit("/proc/definitely/not/writable/a.jsonl").log(tool="x")           # must not raise
    assert "audit" in capsys.readouterr().err.lower()


# ------------------------------------------------------------------ redact
def test_masking_helpers_and_row_redaction():
    assert nx_redact.mask_email("jo.smith@example.com") == "j***@example.com" and nx_redact.mask_email("bad") == "***"
    assert nx_redact.mask_phone("+49 170 1234567") == "***4567" and nx_redact.mask_phone("12") == "***"
    assert nx_redact.mask_text("secret value") == "se***"
    r = nx_redact.Redactor(masked={"email": "email", "phone": "phone", "name": "text", "pin": "full"}, blocked={"passport"})
    rows = [{"id": 1, "email": "jo@example.com", "phone": "+491701234567", "name": "Jonas", "pin": "1234", "passport": "X1", "city": "Berlin"}]
    out = r.apply(rows)
    assert out == [{"id": 1, "email": "j***@example.com", "phone": "***4567", "name": "Jo***", "pin": "***", "city": "Berlin"}] and rows[0]["passport"] == "X1"
    assert r.apply([{"email": None}]) == [{"email": None}]


# ------------------------------------------------------------------ db
def make_db(tmp_path):
    p = tmp_path / "t.db"
    c = sqlite3.connect(p); c.execute("create table bookings (id integer, city text)"); c.executemany("insert into bookings values (?,?)", [(i, "Berlin") for i in range(50)]); c.commit(); c.close()
    return str(p)


def test_sqlite_adapter_is_read_only_caps_rows_and_times_out(tmp_path):
    db = nx_db.connect("sqlite", path=make_db(tmp_path), timeout_s=1, max_rows=10)
    r = db.query("SELECT id, city FROM bookings ORDER BY id")
    assert r.columns == ["id", "city"] and len(r.rows) == 10 and r.truncated is True and r.rows[0] == {"id": 0, "city": "Berlin"}
    assert db.query("SELECT id FROM bookings WHERE id < 3").truncated is False
    for sql in ("INSERT INTO bookings VALUES (99, 'x')", "DROP TABLE bookings", "CREATE TABLE z (a int)"):
        with pytest.raises(nx_db.DbError):
            db.query(sql)
    with pytest.raises(nx_db.DbError) as e:
        db.query("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT count(*) FROM c")
    assert "too long" in str(e.value).lower()
    assert db.query("SELECT COUNT(*) AS n FROM bookings").rows == [{"n": 50}]


def test_missing_sqlite_file_is_a_clear_error_not_a_new_empty_database(tmp_path):
    with pytest.raises(nx_db.DbError):
        nx_db.connect("sqlite", path=str(tmp_path / "missing.db"))
    assert not (tmp_path / "missing.db").exists()


def test_network_adapters_force_read_only_timeouts_and_tls(monkeypatch):
    seen = {}
    pg = types.ModuleType("psycopg")

    class Cur:
        description = [types.SimpleNamespace(name="a")]
        def execute(self, sql, params=None): seen["sql"] = sql
        def fetchmany(self, n): return [(1,)]
        def close(self): pass
    class Conn:
        def cursor(self): return Cur()
        def close(self): pass
        def set_read_only(self, v): seen["ro"] = v
    def connect(dsn, **kw): seen["pg"] = (dsn, kw); return Conn()
    pg.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", pg)
    db = nx_db.connect("postgres", dsn="postgresql://u:p@db.example.com/x", timeout_s=7, max_rows=5)
    kw = seen["pg"][1]
    assert "default_transaction_read_only=on" in kw["options"] and "statement_timeout=7000" in kw["options"] and kw["sslmode"] == "require" and seen["ro"] is True
    assert db.query("SELECT 1").rows == [{"a": 1}]
    with pytest.raises(nx_db.DbError):
        nx_db.connect("postgres", dsn="postgresql://u:p@db.example.com/x?sslmode=disable")
    monkeypatch.setitem(sys.modules, "psycopg", None)                                     # makes "import psycopg" raise ImportError
    with pytest.raises(nx_db.DbError) as e:
        nx_db.connect("postgres", dsn="postgresql://u:p@h/x")
    assert "psycopg" in str(e.value)
    with pytest.raises(nx_db.DbError):
        nx_db.connect("oracle", dsn="x")


# ------------------------------------------------------------------ http
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/redir"):
            self.send_response(302); self.send_header("Location", "http://evil.example.net/x"); self.end_headers(); return
        if self.path.startswith("/big"):
            body = b"x" * 3_000_000
        else:
            body = json.dumps({"path": self.path, "auth": self.headers.get("Authorization")}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    do_POST = do_DELETE = do_PUT = lambda self: (self.send_response(500), self.end_headers())


@pytest.fixture
def web():
    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    s.daemon_threads = True                                 # a handler stuck writing a big body must not block shutdown
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()


def test_http_allows_listed_hosts_only_and_sends_headers_and_params(web):
    c = nx_http.HttpGet(["127.0.0.1"], allow_insecure_hosts=["127.0.0.1"], allow_private_hosts=["127.0.0.1"], headers={"Authorization": "Bearer t"})
    r = c.get(web + "/items", params={"q": "a b", "n": 2})
    assert r.status == 200 and r.json["path"] == "/items?q=a+b&n=2" and r.json["auth"] == "Bearer t"
    for url in ("https://example.com/x", "http://127.0.0.1.evil.example/x", "ftp://127.0.0.1/x", "file:///etc/passwd", "http://user:pw@127.0.0.1/x", "//127.0.0.1/x", "http://[::1]/x", "http://localhost/x"):
        with pytest.raises(nx_http.HttpDenied):
            c.get(url)
    with pytest.raises(nx_http.HttpDenied):
        nx_http.HttpGet(["127.0.0.1"]).get(web + "/x")                              # plain http needs an explicit insecure allowance
    assert nx_http.HttpGet(["*.example.com"]).allowed_host("api.example.com") and not nx_http.HttpGet(["*.example.com"]).allowed_host("example.com.evil.net")


def test_http_blocks_redirects_off_the_list_and_caps_the_body(web):
    c = nx_http.HttpGet(["127.0.0.1"], allow_insecure_hosts=["127.0.0.1"], allow_private_hosts=["127.0.0.1"], max_bytes=100_000)
    with pytest.raises(nx_http.HttpDenied):
        c.get(web + "/redir")
    with pytest.raises(nx_http.HttpDenied) as e:
        c.get(web + "/big")
    assert "too large" in str(e.value).lower()


def test_http_refuses_private_targets_even_for_allowlisted_names(monkeypatch):
    """An allowlisted NAME that resolves to a private, loopback or cloud-metadata address (DNS rebinding, internal host) is refused."""
    import socket
    def fake(host, *a, **k):
        return [(2, 1, 6, "", ({"api.example.com": "10.0.0.5", "meta.example.com": "169.254.169.254", "lo.example.com": "127.0.0.1", "ok.example.com": "93.184.216.34"}[host], 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    c = nx_http.HttpGet(["*.example.com"])
    for host in ("api.example.com", "meta.example.com", "lo.example.com"):
        with pytest.raises(nx_http.HttpDenied) as e:
            c.get(f"https://{host}/x")
        assert "private" in str(e.value).lower()
    c._check("https://ok.example.com/x")                                                   # a public address passes the check
    with pytest.raises(nx_http.HttpDenied):
        nx_http.HttpGet(["10.0.0.5"]).get("https://10.0.0.5/x")                            # IP literals are private too, unless listed explicitly
    nx_http.HttpGet(["10.0.0.5"], allow_private_hosts=["10.0.0.5"])._check("https://10.0.0.5/x")


# ------------------------------------------------------------------ fs
def test_fs_stays_inside_roots_even_with_traversal_and_symlinks(tmp_path):
    root = tmp_path / "docs"; (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text("hello"); (root / "sub" / "b.md").write_text("deep"); (tmp_path / "secret.txt").write_text("nope")
    (root / "link.md").symlink_to(tmp_path / "secret.txt")
    r = nx_fs.Roots([str(root)])
    assert r.read_text("a.md") == "hello" and r.read_text("sub/b.md") == "deep" and r.read_text(str(root / "a.md")) == "hello"
    for p in ("../secret.txt", "/etc/passwd", "sub/../../secret.txt", "link.md", str(tmp_path / "secret.txt"), "a.md\x00.txt", "~/x"):
        with pytest.raises(nx_fs.FsDenied):
            r.read_text(p)
    assert sorted(x.name for x in r.walk((".md",))) == ["a.md", "b.md"]                  # the symlink to outside is not listed
    (root / "big.md").write_text("y" * 500_000)
    with pytest.raises(nx_fs.FsDenied):
        r.read_text("big.md", max_bytes=100_000)
    with pytest.raises(nx_fs.FsDenied):
        nx_fs.Roots([])


# ------------------------------------------------------------------ cli
def run_cli(handler, argv, stdin="", selftest=None):
    out, code = io.StringIO(), None
    try:
        code = nx_cli.main(handler, argv=argv, stdin=io.StringIO(stdin), stdout=out, selftest=selftest)
    except SystemExit as e:
        code = e.code
    return code, json.loads(out.getvalue())


def test_cli_json_in_json_out_exit_codes_and_selftest():
    def h(a):
        if "x" not in a:
            raise nx_cli.UserError("x is required")
        if a["x"] == "boom":
            raise RuntimeError("secret /path")
        return {"y": a["x"] * 2}
    assert run_cli(h, [], '{"x": 2}') == (0, {"ok": True, "result": {"y": 4}})
    assert run_cli(h, ["--json", '{"x": 3}']) == (0, {"ok": True, "result": {"y": 6}})
    assert run_cli(h, [], "{}") == (2, {"ok": False, "error": "x is required"})
    assert run_cli(h, [], "not json")[0] == 2
    code, out = run_cli(h, [], '{"x": "boom"}')
    assert code == 1 and out == {"ok": False, "error": "Internal error."}
    assert run_cli(h, ["--selftest"], selftest=lambda: [("adds", True)])[0] == 0
    code, out = run_cli(h, ["--selftest"], selftest=lambda: [("adds", True), ("breaks", False)])
    assert code == 1 and out["failed"] == ["breaks"]
