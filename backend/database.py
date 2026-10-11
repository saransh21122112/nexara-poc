import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "nexara.db"


class Row:
    """Row usable like sqlite3.Row: row[0], row["col"], dict(row), iteration over values."""
    def __init__(self, cols, vals):
        self._c, self._v = list(cols), tuple(vals)

    def __getitem__(self, k):
        return self._v[k] if isinstance(k, int) else self._v[self._c.index(k)]

    def keys(self):
        return self._c

    def __iter__(self):
        return iter(self._v)


class _Pg:
    """Postgres connection with the same execute() surface the rest of this module uses."""
    def __init__(self, url):
        import psycopg
        def factory(cur):
            cols = [d.name for d in cur.description] if cur.description else []
            return lambda vals: Row(cols, vals)
        self.conn = psycopg.connect(url, row_factory=factory)

    def execute(self, sql, params=()):
        return self.conn.execute(sql.replace("?", "%s"), params)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def database_url() -> str | None:
    u = os.environ.get("DATABASE_URL", "")
    return u if u.startswith(("postgres://", "postgresql://")) else None  # else SQLite (NEXARA_DB or data/nexara.db)


SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, created_at TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, request_id TEXT REFERENCES requests(id),
  created_at TEXT, updated_at TEXT, provider TEXT, model TEXT, spec TEXT);
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, agent_id TEXT REFERENCES agents(id), created_at TEXT,
  kind TEXT, task TEXT, provider TEXT, model TEXT, status TEXT, response TEXT, latency_ms INTEGER,
  input_tokens INTEGER, output_tokens INTEGER, tool_calls TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS dispatches (id TEXT PRIMARY KEY, agent_id TEXT REFERENCES agents(id), request_id TEXT,
  created_at TEXT, status TEXT, sent_at TEXT, to_email TEXT, subject TEXT, body TEXT, spec TEXT);
CREATE TABLE IF NOT EXISTS api_keys (id TEXT PRIMARY KEY, dispatch_id TEXT REFERENCES dispatches(id), agent_id TEXT,
  label TEXT, prefix TEXT, key_hash TEXT UNIQUE, created_at TEXT, revoked_at TEXT, last_used_at TEXT, request_count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS eval_runs (id TEXT PRIMARY KEY, agent_id TEXT REFERENCES agents(id), created_at TEXT,
  pass_rate DOUBLE PRECISION, results TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@contextmanager
def db():
    if url := database_url():
        conn = _Pg(url)
    else:
        conn = sqlite3.connect(os.environ.get("NEXARA_DB", str(DEFAULT_DB)))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as c:
        for stmt in SCHEMA.split(";"):  # one statement at a time: works on both SQLite and Postgres
            if stmt.strip():
                c.execute(stmt)


def _j(row, *json_cols):
    if row is None:
        return None
    d = dict(row)
    for k in json_cols:
        if d.get(k) is not None:
            d[k] = json.loads(d[k])
    return d


def create_request(data: dict) -> dict:
    rid = new_id("REQ")
    with db() as c:
        c.execute("INSERT INTO requests VALUES (?,?,?)", (rid, now(), json.dumps(data)))
    return get_request(rid)


def _req(row):
    d = _j(row, "data")
    return {"id": d["id"], "created_at": d["created_at"], **d["data"]} if d else None


def get_request(rid: str):
    with db() as c:
        row = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    r = _req(row)
    if r:
        r["agent_ids"] = [a["id"] for a in list_agents(request_id=rid)]
    return r


def list_requests():
    with db() as c:
        rows = c.execute("SELECT * FROM requests ORDER BY created_at DESC").fetchall()
        counts = dict(c.execute("SELECT request_id, COUNT(*) FROM agents GROUP BY request_id").fetchall())
    out = [_req(r) for r in rows]
    for r in out:
        r["agent_count"] = counts.get(r["id"], 0)
    return out


def create_agent(request_id: str, spec: dict, provider: str, model: str) -> dict:
    aid = new_id("AGT")
    with db() as c:
        c.execute("INSERT INTO agents VALUES (?,?,?,?,?,?,?)",
                  (aid, request_id, now(), now(), provider, model, json.dumps(spec)))
    return get_agent(aid)


def get_agent(aid: str):
    with db() as c:
        row = c.execute("SELECT * FROM agents WHERE id=?", (aid,)).fetchone()
        if not row:
            return None
        d = _j(row, "spec")
        d["tested"] = c.execute("SELECT COUNT(*) FROM runs WHERE agent_id=?", (aid,)).fetchone()[0] > 0 or \
            c.execute("SELECT COUNT(*) FROM eval_runs WHERE agent_id=?", (aid,)).fetchone()[0] > 0
        ev = c.execute("SELECT * FROM eval_runs WHERE agent_id=? ORDER BY created_at DESC LIMIT 1", (aid,)).fetchone()
    d["last_eval"] = _j(ev, "results")
    return d


def list_agents(request_id: str | None = None):
    with db() as c:
        q, a = ("SELECT id FROM agents WHERE request_id=? ORDER BY created_at DESC", (request_id,)) \
            if request_id else ("SELECT id FROM agents ORDER BY created_at DESC", ())
        ids = [r[0] for r in c.execute(q, a).fetchall()]
    return [get_agent(i) for i in ids]


def update_agent_spec(aid: str, spec: dict):
    with db() as c:
        c.execute("UPDATE agents SET spec=?, updated_at=? WHERE id=?", (json.dumps(spec), now(), aid))
    return get_agent(aid)


def save_run(agent_id: str, kind: str, task: str, provider: str, model: str, status: str, response: str,
             latency_ms: int, in_tok, out_tok, tool_calls: list, error: str | None) -> dict:
    rid = new_id("RUN")
    with db() as c:
        c.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (rid, agent_id, now(), kind, task, provider, model, status, response, latency_ms,
                   in_tok, out_tok, json.dumps(tool_calls), error))
        row = c.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    return _j(row, "tool_calls")


def list_runs(agent_id: str | None = None, limit: int = 50):
    with db() as c:
        if agent_id:
            rows = c.execute("SELECT * FROM runs WHERE agent_id=? ORDER BY created_at DESC LIMIT ?", (agent_id, limit))
        else:
            rows = c.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [_j(r, "tool_calls") for r in rows.fetchall()]


def create_dispatch(agent: dict, draft: dict) -> dict:
    did = new_id("DSP")
    with db() as c:
        c.execute("INSERT INTO dispatches VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (did, agent["id"], agent["request_id"], now(), "draft", None, draft["to"], draft["subject"], draft["body"],
                   json.dumps(agent["spec"])))
    return get_dispatch(did)


def get_dispatch(did: str):
    with db() as c:
        row = c.execute("SELECT * FROM dispatches WHERE id=?", (did,)).fetchone()
    return _j(row, "spec")


def list_dispatches(agent_id: str):
    with db() as c:
        rows = c.execute("SELECT * FROM dispatches WHERE agent_id=? ORDER BY created_at DESC", (agent_id,)).fetchall()
    return [{k: v for k, v in dict(r).items() if k != "spec"} for r in rows]


def mark_sent(did: str):
    with db() as c:
        c.execute("UPDATE dispatches SET status='sent', sent_at=? WHERE id=?", (now(), did))
    return get_dispatch(did)


def create_api_key(dispatch: dict, label: str) -> tuple[dict, str]:
    """Returns (record, plaintext). Only a SHA-256 hash is stored; the plaintext is shown once. Keys are 256-bit random."""
    secret = "nxk_" + secrets.token_urlsafe(32)
    kid = new_id("KEY")
    with db() as c:
        c.execute("INSERT INTO api_keys (id,dispatch_id,agent_id,label,prefix,key_hash,created_at) VALUES (?,?,?,?,?,?,?)",
                  (kid, dispatch["id"], dispatch["agent_id"], label, secret[:10], hashlib.sha256(secret.encode()).hexdigest(), now()))
    return get_api_key(kid), secret


def _key(row):
    return {k: v for k, v in dict(row).items() if k != "key_hash"} if row else None


def get_api_key(kid: str):
    with db() as c:
        return _key(c.execute("SELECT * FROM api_keys WHERE id=?", (kid,)).fetchone())


def list_api_keys(dispatch_id: str):
    with db() as c:
        return [_key(r) for r in c.execute("SELECT * FROM api_keys WHERE dispatch_id=? ORDER BY created_at DESC", (dispatch_id,))]


def find_active_key(secret: str):
    with db() as c:
        return _key(c.execute("SELECT * FROM api_keys WHERE key_hash=? AND revoked_at IS NULL",
                              (hashlib.sha256(secret.encode()).hexdigest(),)).fetchone())


def touch_key(kid: str):
    with db() as c:
        c.execute("UPDATE api_keys SET request_count=request_count+1, last_used_at=? WHERE id=?", (now(), kid))


def revoke_api_key(kid: str):
    with db() as c:
        c.execute("UPDATE api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (now(), kid))
    return get_api_key(kid)


def save_eval(agent_id: str, pass_rate: float, results: dict) -> dict:
    eid = new_id("EVL")
    with db() as c:
        c.execute("INSERT INTO eval_runs VALUES (?,?,?,?,?)", (eid, agent_id, now(), pass_rate, json.dumps(results)))
    return {"id": eid, **results}


def stats() -> dict:
    with db() as c:
        one = lambda q: c.execute(q).fetchone()[0]
        tested = one("SELECT COUNT(*) FROM (SELECT agent_id FROM runs UNION SELECT agent_id FROM eval_runs) t")
        activity = []
        for r in c.execute("SELECT created_at, id, data FROM requests"):
            activity.append({"at": r[0], "type": "request", "text": f"Request {r[1]} from {json.loads(r[2])['company_name']}"})
        for r in c.execute("SELECT created_at, id, spec FROM agents"):
            activity.append({"at": r[0], "type": "agent", "text": f"Agent generated: {json.loads(r[2])['agent_name']} ({r[1]})"})
        for r in c.execute("SELECT created_at, kind, agent_id, status FROM runs"):
            activity.append({"at": r[0], "type": "run", "text": f"{r[1]} on {r[2]}: {r[3]}"})
        for r in c.execute("SELECT created_at, id, agent_id, status FROM dispatches"):
            activity.append({"at": r[0], "type": "dispatch", "text": f"Dispatch {r[1]} for {r[2]}: {r[3]}"})
        for r in c.execute("SELECT created_at, agent_id, pass_rate FROM eval_runs"):
            activity.append({"at": r[0], "type": "eval", "text": f"Tests on {r[1]}: {round(r[2]*100)}% pass"})
        return {"total_requests": one("SELECT COUNT(*) FROM requests"), "agents_generated": one("SELECT COUNT(*) FROM agents"),
                "agents_tested": tested, "agents_dispatched": one("SELECT COUNT(DISTINCT agent_id) FROM dispatches WHERE status='sent'"), "recent_activity": sorted(activity, key=lambda a: a["at"], reverse=True)[:12]}
