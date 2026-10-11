import collections
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from . import agent_service, database as dbm, dispatch, export, providers  # noqa: E402
from .models import AGENT_TYPES, AgentSpec, LoginIn, ProviderIn, RequestIn, RunIn  # noqa: E402
from .providers import NotConfigured, UpstreamError  # noqa: E402

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
TOKENS: set[str] = set()  # DEMO AUTH ONLY: in-memory tokens, not production security


@asynccontextmanager
async def lifespan(_):
    dbm.init_db()
    yield


app = FastAPI(title="Nexara AI Agent Factory (POC)", lifespan=lifespan)


@app.exception_handler(NotConfigured)
async def _nc(_, e):
    return Response(status_code=503, media_type="application/json",
                    content=__import__("json").dumps({"detail": str(e), "code": "not_configured"}))


@app.exception_handler(UpstreamError)
async def _up(_, e):
    return Response(status_code=502, media_type="application/json",
                    content=__import__("json").dumps({"detail": str(e), "code": "upstream_error"}))


_buckets: dict[str, collections.deque] = {}  # ponytail: in-memory per-process limiter, use Redis if scaled out


def client_ip(request: Request) -> str:
    # Fly/most proxies put the real client first in these headers; fall back to the socket peer
    return (request.headers.get("fly-client-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown"))


def limit(bucket: str, key: str, n: int, seconds: int, what: str):
    q = _buckets.setdefault(f"{bucket}:{key}", collections.deque())
    cutoff = time.monotonic() - seconds
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= n:
        raise HTTPException(429, f"Too many {what}. Try again later.")
    q.append(time.monotonic())


def factory_auth(x_factory_token: str | None = Header(default=None)):
    if not x_factory_token or x_factory_token not in TOKENS:
        raise HTTPException(401, "Factory login required (demo auth).")


def _agent_or_404(aid: str) -> dict:
    a = dbm.get_agent(aid)
    if not a:
        raise HTTPException(404, f"Agent {aid} not found")
    return a


@app.middleware("http")
async def no_cache_static(request, call_next):
    resp = await call_next(request)
    if not request.url.path.startswith("/api"):
        resp.headers["Cache-Control"] = "no-cache"  # POC: always revalidate so edits show immediately
    return resp


@app.get("/api/health")
def health():
    return {"status": "ok", "agent_types": AGENT_TYPES, "public_base_url": os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL", ""), **providers.status()}


@app.post("/api/factory/login")
def login(body: LoginIn, request: Request):
    ip = client_ip(request)
    q = _buckets.get(f"login:{ip}")
    if q and len([t for t in q if t > time.monotonic() - 300]) >= 10:  # 10 failures / 5 min / IP
        raise HTTPException(429, "Too many failed logins. Try again in a few minutes.")
    if not secrets.compare_digest(body.password, os.environ.get("FACTORY_PASSWORD", "nexara-demo")):
        _buckets.setdefault(f"login:{ip}", collections.deque()).append(time.monotonic())
        raise HTTPException(401, "Wrong password")
    t = secrets.token_urlsafe(24)
    TOKENS.add(t)
    return {"token": t, "notice": "Demo authentication only - not production security."}


@app.get("/api/provider", dependencies=[Depends(factory_auth)])
def get_provider():
    return providers.status()


@app.put("/api/provider", dependencies=[Depends(factory_auth)])
def put_provider(body: ProviderIn):
    providers.set_provider(body.provider)
    return providers.status()


@app.post("/api/requests", status_code=201)
def create_request(body: RequestIn, request: Request):
    limit("req", client_ip(request), int(os.environ.get("REQUESTS_PER_HOUR_PER_IP", "20")), 3600, "requests from this address")
    r = dbm.create_request(body.model_dump())
    return {"request_id": r["id"], "message": "Request received", "request": r}


@app.get("/api/requests", dependencies=[Depends(factory_auth)])
def list_requests():
    return dbm.list_requests()


@app.get("/api/requests/{request_id}", dependencies=[Depends(factory_auth)])
def get_request(request_id: str):
    r = dbm.get_request(request_id)
    if not r:
        raise HTTPException(404, f"Request {request_id} not found")
    return r


@app.post("/api/requests/{request_id}/generate-agent", status_code=201, dependencies=[Depends(factory_auth)])
async def generate_agent(request_id: str):
    r = dbm.get_request(request_id)
    if not r:
        raise HTTPException(404, f"Request {request_id} not found")
    spec, provider, model = await agent_service.generate_spec(r)
    return dbm.create_agent(request_id, spec, provider, model)


@app.get("/api/agents", dependencies=[Depends(factory_auth)])
def list_agents():
    return dbm.list_agents()


@app.get("/api/agents/{agent_id}", dependencies=[Depends(factory_auth)])
def get_agent(agent_id: str):
    return _agent_or_404(agent_id)


@app.put("/api/agents/{agent_id}", dependencies=[Depends(factory_auth)])
def put_agent(agent_id: str, spec: AgentSpec):
    _agent_or_404(agent_id)
    return dbm.update_agent_spec(agent_id, spec.model_dump())


@app.post("/api/agents/{agent_id}/run", dependencies=[Depends(factory_auth)])
async def run_agent(agent_id: str, body: RunIn):
    return await agent_service.run_agent(_agent_or_404(agent_id), body.task)


@app.post("/api/agents/{agent_id}/evaluate", dependencies=[Depends(factory_auth)])
async def evaluate_agent(agent_id: str):
    return await agent_service.evaluate(_agent_or_404(agent_id))


@app.get("/api/agents/{agent_id}/export", dependencies=[Depends(factory_auth)])
def export_agent(agent_id: str, format: str = "portable"):
    spec = _agent_or_404(agent_id)["spec"]
    if format not in ("crewai", "portable"):
        raise HTTPException(422, "format must be 'crewai' or 'portable'")
    data = (export.crewai_bundle if format == "crewai" else export.portable_bundle)(spec)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{agent_id}-{format}.zip"'})


@app.post("/api/agents/{agent_id}/dispatch", status_code=201, dependencies=[Depends(factory_auth)])
def dispatch_agent(agent_id: str):
    """Prepares a package + DRAFT email. Never sends anything; a human sends it and then calls mark-sent."""
    agent = _agent_or_404(agent_id)
    req = dbm.get_request(agent["request_id"])
    return dbm.create_dispatch(agent, dispatch.email_draft(agent["spec"], req))


@app.get("/api/agents/{agent_id}/dispatches", dependencies=[Depends(factory_auth)])
def agent_dispatches(agent_id: str):
    _agent_or_404(agent_id)
    return dbm.list_dispatches(agent_id)


def _dispatch_or_404(did: str) -> dict:
    d = dbm.get_dispatch(did)
    if not d:
        raise HTTPException(404, f"Dispatch {did} not found")
    return d


@app.get("/api/dispatches/{dispatch_id}", dependencies=[Depends(factory_auth)])
def get_dispatch(dispatch_id: str):
    return {k: v for k, v in _dispatch_or_404(dispatch_id).items() if k != "spec"}


@app.get("/api/dispatches/{dispatch_id}/package", dependencies=[Depends(factory_auth)])
def dispatch_package(dispatch_id: str):
    d = _dispatch_or_404(dispatch_id)
    company = dbm.get_request(d["request_id"])["company_name"]
    return Response(dispatch.package(d["spec"], company), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{dispatch_id}-agent-package.zip"'})


@app.post("/api/dispatches/{dispatch_id}/mark-sent", dependencies=[Depends(factory_auth)])
def dispatch_mark_sent(dispatch_id: str):
    _dispatch_or_404(dispatch_id)
    return {k: v for k, v in dbm.mark_sent(dispatch_id).items() if k != "spec"}


class KeyIn(BaseModel):
    label: str = Field(default="Customer key", min_length=1, max_length=80)


@app.post("/api/dispatches/{dispatch_id}/keys", status_code=201, dependencies=[Depends(factory_auth)])
def create_key(dispatch_id: str, body: KeyIn | None = None):
    rec, secret = dbm.create_api_key(_dispatch_or_404(dispatch_id), (body or KeyIn()).label.strip())
    return {**rec, "key": secret, "notice": "Copy this key now - it is stored only as a hash and cannot be shown again."}


@app.get("/api/dispatches/{dispatch_id}/keys", dependencies=[Depends(factory_auth)])
def list_keys(dispatch_id: str):
    _dispatch_or_404(dispatch_id)
    return dbm.list_api_keys(dispatch_id)


@app.post("/api/keys/{key_id}/revoke", dependencies=[Depends(factory_auth)])
def revoke_key(key_id: str):
    if not dbm.get_api_key(key_id):
        raise HTTPException(404, f"Key {key_id} not found")
    return dbm.revoke_api_key(key_id)


# ---- Customer-facing API (authenticated by a per-customer API key, not the factory token) ----
RATE_PER_MIN = int(os.environ.get("CUSTOMER_API_RATE_PER_MIN", "30"))
_hits: dict[str, collections.deque] = {}  # ponytail: in-memory per-process limiter, use Redis if scaled out


def customer_key(authorization: str = Header(default="")):
    secret = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    rec = dbm.find_active_key(secret) if secret.startswith("nxk_") else None
    if not rec:
        raise HTTPException(401, "Invalid, revoked or missing API key. Send 'Authorization: Bearer <key>'.")
    q = _hits.setdefault(rec["id"], collections.deque())
    cutoff = time.monotonic() - 60
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= RATE_PER_MIN:
        raise HTTPException(429, f"Rate limit: {RATE_PER_MIN} requests per minute per key.")
    q.append(time.monotonic())
    return rec


@app.get("/v1/agent")
def v1_agent(key: dict = Depends(customer_key)):
    spec = dbm.get_dispatch(key["dispatch_id"])["spec"]
    return {k: spec[k] for k in ("agent_name", "purpose", "capabilities", "input_schema", "output_schema")}


@app.post("/v1/run")
async def v1_run(body: RunIn, key: dict = Depends(customer_key)):
    if providers.get_provider() == "claude_subscription":
        raise HTTPException(503, "The customer API is disabled while the Claude-subscription provider is selected "
                                 "(subscription logins are for local demos only). Switch to the anthropic or openai provider.")
    d = dbm.get_dispatch(key["dispatch_id"])  # runs the dispatched snapshot, not later edits
    run = await agent_service.run_agent({"id": d["agent_id"], "spec": d["spec"]}, body.task, kind="api")
    dbm.touch_key(key["id"])
    if run["status"] != "success":
        raise HTTPException(502, f"The agent could not complete the request (reference {run['id']}).")
    return {"response": run["response"], "tool_calls": [{"tool": c["tool"], "args": c["args"], "ok": c.get("ok")} for c in run["tool_calls"]],
            "latency_ms": run["latency_ms"], "reference": run["id"]}


@app.get("/api/runs", dependencies=[Depends(factory_auth)])
def runs(agent_id: str | None = None):
    return dbm.list_runs(agent_id)


@app.get("/api/stats", dependencies=[Depends(factory_auth)])
def stats():
    return dbm.stats()


@app.get("/request")
def request_page():
    return FileResponse(FRONTEND / "request.html")


@app.get("/factory")
def factory_page():
    return FileResponse(FRONTEND / "factory.html")


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="site")
