import hashlib
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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


# Tables from the retired hosted-API / zip-dispatch flow; dropped on startup (they only ever held test data).
OBSOLETE = ["api_keys", "dispatches"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, created_at TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, request_id TEXT REFERENCES requests(id),
  created_at TEXT, updated_at TEXT, provider TEXT, model TEXT, spec TEXT);
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, agent_id TEXT REFERENCES agents(id), created_at TEXT,
  kind TEXT, task TEXT, provider TEXT, model TEXT, status TEXT, response TEXT, latency_ms INTEGER,
  input_tokens INTEGER, output_tokens INTEGER, tool_calls TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS eval_runs (id TEXT PRIMARY KEY, agent_id TEXT REFERENCES agents(id), created_at TEXT,
  pass_rate DOUBLE PRECISION, results TEXT);
CREATE TABLE IF NOT EXISTS customers (id TEXT PRIMARY KEY, slug TEXT UNIQUE, company_name TEXT, contact_email TEXT,
  github_username TEXT, claude_status TEXT, os TEXT, created_at TEXT, onboarding TEXT);
CREATE TABLE IF NOT EXISTS repos (id TEXT PRIMARY KEY, customer_id TEXT UNIQUE REFERENCES customers(id), owner TEXT, name TEXT,
  url TEXT, created_at TEXT, state TEXT, invite_status TEXT, invited_user TEXT, invitation_id TEXT, head_sha TEXT,
  synced_at TEXT, last_error TEXT, files TEXT, layout INTEGER, legacy TEXT);
CREATE TABLE IF NOT EXISTS repo_agents (id TEXT PRIMARY KEY, repo_id TEXT REFERENCES repos(id), agent_id TEXT REFERENCES agents(id),
  plugin_slug TEXT, spec_hash TEXT, added_at TEXT, synced_at TEXT, UNIQUE (repo_id, agent_id));
CREATE TABLE IF NOT EXISTS email_verifications (id TEXT PRIMARY KEY, request_id TEXT REFERENCES requests(id), email TEXT,
  token_hash TEXT UNIQUE, created_at TEXT, expires_at TEXT, used_at TEXT);
CREATE TABLE IF NOT EXISTS repo_events (id TEXT PRIMARY KEY, customer_id TEXT REFERENCES customers(id), repo_id TEXT,
  at TEXT, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS build_events (id TEXT PRIMARY KEY, request_id TEXT REFERENCES requests(id), seq INTEGER, at TEXT,
  stage TEXT, state TEXT, title TEXT, detail TEXT)
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
        for t in OBSOLETE:
            c.execute(f"DROP TABLE IF EXISTS {t}")
        for stmt in SCHEMA.split(";"):  # one statement at a time: works on both SQLite and Postgres
            if stmt.strip():
                c.execute(stmt)
        for col, typ in (("layout", "INTEGER"), ("legacy", "TEXT")):      # columns added after first release
            if database_url():
                c.execute(f"ALTER TABLE repos ADD COLUMN IF NOT EXISTS {col} {typ}")
            elif col not in [r[1] for r in c.execute("PRAGMA table_info(repos)").fetchall()]:
                c.execute(f"ALTER TABLE repos ADD COLUMN {col} {typ}")


def _j(row, *json_cols):
    if row is None:
        return None
    d = dict(row)
    for k in json_cols:
        if d.get(k) is not None:
            d[k] = json.loads(d[k])
    return d


# ------------------------------------------------------------------ customers
def slugify(name: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:limit].strip("-") or "company"


def upsert_customer(data: dict) -> dict:
    """One customer per company. The request form is public and unauthenticated, so identity claims are unverified: a request
    is attached to an existing customer only if the slug matches AND the GitHub username AND the contact email both match.
    Anything else becomes a separate customer (slug -2, -3...), so a stranger who knows a company name and an email cannot get
    their request merged into that company's repo. Deliberate merges are an explicit staff action (attach_customer)."""
    base = slugify(data["company_name"])
    gh = (data.get("github_username") or "").lower()
    email = data["contact_email"].lower()
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM customers WHERE slug LIKE ?", (base + "%",)).fetchall()]
        same = [r for r in rows if re.fullmatch(re.escape(base) + r"(-\d+)?", r["slug"])]
        for r in same:
            if (r["github_username"] or "").lower() == gh and (r["contact_email"] or "").lower() == email:
                return _cust(r)
        taken = {r["slug"] for r in same}
        slug, n = base, 1
        while slug in taken:
            n += 1
            slug = f"{base}-{n}"
        cid = new_id("CUS")
        c.execute("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?)",
                  (cid, slug, data["company_name"], data["contact_email"], data.get("github_username"),
                   data.get("claude_status"), data.get("os"), now(), json.dumps({})))
        return _cust(dict(c.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()))


def _cust(row: dict) -> dict:
    d = dict(row)
    d["onboarding"] = json.loads(d["onboarding"]) if d.get("onboarding") else {}
    return d


def get_customer(cid: str):
    with db() as c:
        row = c.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    return _cust(row) if row else None


def list_customers() -> list[dict]:
    with db() as c:
        custs = [_cust(r) for r in c.execute("SELECT * FROM customers ORDER BY created_at DESC").fetchall()]
        repos = {r["customer_id"]: dict(r) for r in c.execute("SELECT * FROM repos").fetchall()}
        n_agents = dict(c.execute("SELECT repo_id, COUNT(*) FROM repo_agents GROUP BY repo_id").fetchall())
        n_req = {}
        for r in c.execute("SELECT data FROM requests").fetchall():
            cid = json.loads(r[0]).get("customer_id")
            n_req[cid] = n_req.get(cid, 0) + 1
    for x in custs:
        x["repo"] = repos.get(x["id"])
        x["delivered_agents"] = n_agents.get(x["repo"]["id"], 0) if x["repo"] else 0
        x["requests"] = n_req.get(x["id"], 0)
    return custs


def set_onboarding(cid: str, step: str, value: bool) -> dict:
    cust = get_customer(cid)
    ob = {**cust["onboarding"], step: bool(value)}
    with db() as c:
        c.execute("UPDATE customers SET onboarding=? WHERE id=?", (json.dumps(ob), cid))
    return get_customer(cid)


# ------------------------------------------------------------------ email verification
def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_verification(request_id: str, email: str, ttl_hours: int = 48) -> str:
    """Returns the plaintext token (only ever emailed). Only its SHA-256 is stored."""
    token = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat(timespec="microseconds")
    with db() as c:
        c.execute("INSERT INTO email_verifications VALUES (?,?,?,?,?,?,?)",
                  (new_id("EVF"), request_id, email.lower(), _h(token), now(), exp, None))
    return token


def verification_times(email: str, since_iso: str) -> list[str]:
    with db() as c:
        return [r[0] for r in c.execute("SELECT created_at FROM email_verifications WHERE email=? AND created_at>=? ORDER BY created_at",
                                        (email.lower(), since_iso)).fetchall()]


def consume_verification(token: str) -> str | None:
    """Single use. Returns the request id if the token is valid, unused and unexpired, else None. Invalidates the request's other tokens."""
    with db() as c:
        row = c.execute("SELECT id, request_id FROM email_verifications WHERE token_hash=? AND used_at IS NULL AND expires_at>?",
                        (_h(token), now())).fetchone()
        if not row:
            return None
        c.execute("UPDATE email_verifications SET used_at=? WHERE request_id=? AND used_at IS NULL", (now(), row[1]))
        return row[1]


def mark_email_verified(request_id: str, via: str, note: str | None = None) -> dict | None:
    """via: 'link' (requester clicked the emailed link) or 'staff' (a person confirmed it another way; note says how)."""
    with db() as c:
        row = c.execute("SELECT data FROM requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            return None
        d = {**json.loads(row[0]), "email_verified": True, "email_verified_at": now(), "email_verified_via": via}
        if note:
            d["email_verified_note"] = note
        c.execute("UPDATE requests SET data=? WHERE id=?", (json.dumps(d), request_id))
    if d.get("customer_id"):
        add_event(d["customer_id"], "email_verified", {"request": request_id, "via": via, "note": note})
    return get_request(request_id)


# ------------------------------------------------------------------ requests
def create_request(data: dict) -> dict:
    rid = new_id("REQ")
    cust = upsert_customer(data)
    with db() as c:
        c.execute("INSERT INTO requests VALUES (?,?,?)", (rid, now(), json.dumps({**data, "customer_id": cust["id"]})))
    return get_request(rid)


def attach_customer(request_id: str, customer_id: str) -> None:
    with db() as c:
        row = c.execute("SELECT data FROM requests WHERE id=?", (request_id,)).fetchone()
        if row:
            c.execute("UPDATE requests SET data=? WHERE id=?", (json.dumps({**json.loads(row[0]), "customer_id": customer_id}), request_id))


def backfill_customers() -> int:
    """Requests created before customers existed get a customer record (idempotent). Returns how many were linked."""
    with db() as c:
        rows = [(r[0], r[1]) for r in c.execute("SELECT id, data FROM requests").fetchall()]
    n = 0
    for rid, data in rows:
        d = json.loads(data)
        if not d.get("customer_id") or not get_customer(d["customer_id"]):
            attach_customer(rid, upsert_customer({**d, "company_name": d.get("company_name") or "Unknown", "contact_email": d.get("contact_email") or "unknown@unknown"})["id"])
            n += 1
    return n


def _req(row):
    d = _j(row, "data")
    return {"id": d["id"], "created_at": d["created_at"], "email_verified": False, **d["data"]} if d else None


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


# ------------------------------------------------------------------ agents
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


def save_eval(agent_id: str, pass_rate: float, results: dict) -> dict:
    eid = new_id("EVL")
    with db() as c:
        c.execute("INSERT INTO eval_runs VALUES (?,?,?,?,?)", (eid, agent_id, now(), pass_rate, json.dumps(results)))
    return {"id": eid, **results}


# ------------------------------------------------------------------ repos
def _repo(row):
    if not row:
        return None
    d = dict(row)
    d["files"] = json.loads(d["files"]) if d.get("files") else {}
    d["layout"] = d.get("layout") or 2                      # rows from before the column existed are the old per-agent layout
    d["legacy"] = json.loads(d["legacy"]) if d.get("legacy") else []
    return d


def create_repo(customer_id: str, owner: str, name: str, url: str, state: str = "creating", layout: int = 3) -> dict:
    rid = new_id("REPO")
    with db() as c:
        c.execute("INSERT INTO repos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (rid, customer_id, owner, name, url, now(), state, "none", None, None, None, None, None, None, layout, json.dumps([])))
    return get_repo(rid)


def get_repo(rid: str):
    with db() as c:
        return _repo(c.execute("SELECT * FROM repos WHERE id=?", (rid,)).fetchone())


def get_repo_for_customer(cid: str):
    with db() as c:
        return _repo(c.execute("SELECT * FROM repos WHERE customer_id=?", (cid,)).fetchone())


_REPO_FIELDS = {"owner", "name", "url", "state", "invite_status", "invited_user", "invitation_id", "head_sha", "synced_at", "last_error", "layout"}


def set_repo_legacy(rid: str, names: list[str]) -> None:
    """Plugin names used by older layouts; kept forever so the marketplace `renames` map keeps migrating old installs."""
    with db() as c:
        c.execute("UPDATE repos SET legacy=? WHERE id=?", (json.dumps(sorted(set(names))), rid))


def update_repo(rid: str, **fields) -> dict:
    bad = set(fields) - _REPO_FIELDS
    if bad:
        raise ValueError(f"unknown repo fields: {bad}")
    if fields:
        with db() as c:
            c.execute(f"UPDATE repos SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), rid))
    return get_repo(rid)


def set_repo_files(rid: str, files: dict) -> None:
    """path -> git blob sha of every Nexara-managed file as last pushed (used to detect hand edits)."""
    with db() as c:
        c.execute("UPDATE repos SET files=? WHERE id=?", (json.dumps(files), rid))


def upsert_repo_agent(repo_id: str, agent_id: str, plugin_slug: str, spec_hash: str) -> dict:
    with db() as c:
        row = c.execute("SELECT id FROM repo_agents WHERE repo_id=? AND agent_id=?", (repo_id, agent_id)).fetchone()
        if row:
            c.execute("UPDATE repo_agents SET plugin_slug=?, spec_hash=?, synced_at=? WHERE id=?", (plugin_slug, spec_hash, now(), row[0]))
        else:
            c.execute("INSERT INTO repo_agents VALUES (?,?,?,?,?,?,?)",
                      (new_id("RAG"), repo_id, agent_id, plugin_slug, spec_hash, now(), now()))
    return get_repo_agent(repo_id, agent_id)


def get_repo_agent(repo_id: str, agent_id: str):
    with db() as c:
        row = c.execute("SELECT * FROM repo_agents WHERE repo_id=? AND agent_id=?", (repo_id, agent_id)).fetchone()
    return dict(row) if row else None


def list_repo_agents(repo_id: str) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT * FROM repo_agents WHERE repo_id=? ORDER BY added_at", (repo_id,)).fetchall()
    return [dict(r) for r in rows]


def repo_agents_for_agent(agent_id: str) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT * FROM repo_agents WHERE agent_id=?", (agent_id,)).fetchall()
    return [dict(r) for r in rows]


def add_event(customer_id: str, kind: str, detail: dict | str | None = None, repo_id: str | None = None) -> dict:
    eid = new_id("EVT")
    with db() as c:
        c.execute("INSERT INTO repo_events VALUES (?,?,?,?,?,?)", (eid, customer_id, repo_id, now(), kind, json.dumps(detail)))
    return {"id": eid, "kind": kind}


def list_events(customer_id: str, limit: int = 100) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT * FROM repo_events WHERE customer_id=? ORDER BY at DESC LIMIT ?", (customer_id, limit)).fetchall()
    return [_j(r, "detail") for r in rows]


# ------------------------------------------------------------------ stats
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
        for r in c.execute("SELECT created_at, agent_id, pass_rate FROM eval_runs"):
            activity.append({"at": r[0], "type": "eval", "text": f"Tests on {r[1]}: {round(r[2]*100)}% pass"})
        for r in c.execute("SELECT e.at, e.kind, cu.company_name FROM repo_events e JOIN customers cu ON cu.id = e.customer_id "
                           "WHERE e.kind IN ('repo_created','synced','invited','agent_added')"):
            activity.append({"at": r[0], "type": "delivery", "text": f"{r[1].replace('_', ' ')}: {r[2]}"})
        return {"total_requests": one("SELECT COUNT(*) FROM requests"), "agents_generated": one("SELECT COUNT(*) FROM agents"),
                "agents_tested": tested, "customers": one("SELECT COUNT(*) FROM customers"),
                "agents_delivered": one("SELECT COUNT(DISTINCT agent_id) FROM repo_agents"),
                "recent_activity": sorted(activity, key=lambda a: a["at"], reverse=True)[:12]}


# ------------------------------------------------------------------ build events (the live pipeline view)
BUILD_STAGES = ["received", "analyse", "specify", "test", "package", "ready"]
BUILD_TERMINAL = {"failed", "interrupted"}      # a build also ends when "ready" is done


def add_build_event(request_id: str, stage: str, state: str, title: str, detail: dict | None = None) -> dict:
    with db() as c:
        seq = (c.execute("SELECT MAX(seq) FROM build_events WHERE request_id=?", (request_id,)).fetchone()[0] or 0) + 1
        eid = new_id("BEV")
        c.execute("INSERT INTO build_events VALUES (?,?,?,?,?,?,?,?)", (eid, request_id, seq, now(), stage, state, title, json.dumps(detail)))
    return {"id": eid, "seq": seq}


def list_build_events(request_id: str) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT * FROM build_events WHERE request_id=? ORDER BY seq", (request_id,)).fetchall()
    return [_j(r, "detail") for r in rows]


def build_status(request_id: str) -> dict:
    """Derived from the events of the LATEST build (everything since the last 'received running'). status: none|running|failed|interrupted|ready."""
    evs = list_build_events(request_id)
    starts = [i for i, e in enumerate(evs) if e["stage"] == "received" and e["state"] == "running"]
    evs = evs[starts[-1]:] if starts else []
    stages: dict = {}
    for e in evs:
        cur = stages.setdefault(e["stage"], {"state": "pending", "title": "", "detail": None, "at": e["at"], "progress": []})
        if e["state"] == "progress":
            cur["progress"].append({"title": e["title"], "detail": e["detail"], "at": e["at"]})
        else:
            cur.update(state=e["state"], title=e["title"], detail=e["detail"] if e["detail"] is not None else cur["detail"], done_at=e["at"])
    last = evs[-1] if evs else None
    if not last:
        status = "none"
    elif last["state"] in BUILD_TERMINAL:
        status = last["state"]
    elif last["stage"] == "ready" and last["state"] == "done":
        status = "ready"
    else:
        status = "running"
    return {"status": status, "stages": stages, "events": evs, "started_at": evs[0]["at"] if evs else None}


def interrupt_stale_builds() -> int:
    """A redeploy kills a running build task; mark such builds interrupted so no screen spins forever."""
    with db() as c:
        ids = [r[0] for r in c.execute("SELECT DISTINCT request_id FROM build_events").fetchall()]
    n = 0
    for rid in ids:
        if build_status(rid)["status"] == "running":
            last = list_build_events(rid)[-1]
            add_build_event(rid, last["stage"], "interrupted", "The server restarted during this step. Start the build again.")
            n += 1
    return n
