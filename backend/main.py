import collections
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from . import agent_service, build_service, database as dbm, delivery_service, mailer, providers, verification  # noqa: E402
from .github_client import GitHubClient, GitHubError, NotConfigured as GitHubNotConfigured  # noqa: E402
from .models import (AGENT_TYPES, AgentSpec, AssistIn, AttachIn, CollaboratorIn, ConfirmIn, DeliverIn, LoginIn, NoteIn, OnboardingIn,  # noqa: E402
                     ManualVerifyIn, ProviderIn, RequestIn, RunIn, SyncIn, VerifyIn)
from .providers import NotConfigured, UpstreamError  # noqa: E402

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
TOKENS: set[str] = set()  # DEMO AUTH ONLY: in-memory tokens, not production security


@asynccontextmanager
async def lifespan(_):
    dbm.init_db()
    dbm.backfill_customers()
    dbm.interrupt_stale_builds()
    yield


# Public hosts (Render sets RENDER) don't expose the interactive API docs / schema.
_docs = not os.environ.get("RENDER") and os.environ.get("DISABLE_DOCS") != "1"
app = FastAPI(title="Nexara AI Agent Factory (POC)", lifespan=lifespan,
              docs_url="/docs" if _docs else None, redoc_url="/redoc" if _docs else None,
              openapi_url="/openapi.json" if _docs else None)


@app.exception_handler(NotConfigured)
async def _nc(_, e):
    return Response(status_code=503, media_type="application/json",
                    content=__import__("json").dumps({"detail": str(e), "code": "not_configured"}))


@app.exception_handler(UpstreamError)
async def _up(_, e):
    return Response(status_code=502, media_type="application/json",
                    content=__import__("json").dumps({"detail": str(e), "code": "upstream_error"}))


@app.exception_handler(delivery_service.DeliveryError)
async def _de(_, e):
    body = {"detail": str(e), "code": "conflict" if isinstance(e, delivery_service.Conflict) else "delivery_error"}
    if isinstance(e, delivery_service.Conflict):
        body["conflicts"] = e.conflicts
    return Response(status_code=e.status, media_type="application/json", content=__import__("json").dumps(body))


@app.exception_handler(GitHubError)
async def _gh(_, e):
    status = 503 if isinstance(e, GitHubNotConfigured) else (409 if e.code in ("repo_exists", "branch_moved") else 502)
    return Response(status_code=status, media_type="application/json", content=__import__("json").dumps({"detail": str(e), "code": e.code}))


def get_github() -> GitHubClient:
    return GitHubClient()  # reads GITHUB_TOKEN / GITHUB_OWNER / GITHUB_API_URL on every call


def _confirmed(confirm: bool, what: str):
    if not confirm:
        raise HTTPException(400, f"This will {what}. Send confirm=true to proceed.")


_buckets: dict[str, collections.deque] = {}  # ponytail: in-memory per-process limiter, use Redis if scaled out


def client_ip(request: Request) -> str:
    """Address used for rate limiting. Client-supplied headers are NOT trusted by default (trivially spoofed).
    - On Render (RENDER set) or with TRUST_CF_CONNECTING_IP=1: Cloudflare sits in front and sets CF-Connecting-IP itself,
      overwriting any client-sent value, so that header is trusted.
    - With TRUSTED_PROXY_HOPS=N: take the Nth entry from the RIGHT of X-Forwarded-For (the one your own proxy appended).
    - Otherwise: the socket peer."""
    if os.environ.get("TRUST_CF_CONNECTING_IP") == "1" or os.environ.get("RENDER"):
        if cf := request.headers.get("cf-connecting-ip", "").strip():
            return cf
    hops = int(os.environ.get("TRUSTED_PROXY_HOPS", "0"))
    xff = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if hops > 0 and len(xff) >= hops:
        return xff[-hops]
    return request.client.host if request.client else "unknown"


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
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"  # the factory login must not be framed (clickjacking)
    resp.headers["Referrer-Policy"] = "same-origin"
    if not request.url.path.startswith("/api"):
        resp.headers["Cache-Control"] = "no-cache"  # POC: always revalidate so edits show immediately
    return resp


@app.get("/api/health")
def health():
    return {"status": "ok", "agent_types": AGENT_TYPES, "github_configured": get_github().configured, **providers.status()}


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


_assist_day = [0, 0]   # [day number, count]: one global daily cap, because this public endpoint spends the owner's model quota


@app.post("/api/requests/assist")
async def assist_request(body: AssistIn, request: Request):
    if not (body.description.strip() or body.instruction.strip()):
        raise HTTPException(422, "Tell the assistant what you need.")
    limit("assist", client_ip(request), int(os.environ.get("ASSIST_PER_HOUR_PER_IP", "10")), 3600, "assistant requests from this address")
    day = int(time.time() // 86400)
    if _assist_day[0] != day:
        _assist_day[:] = [day, 0]
    if _assist_day[1] >= int(os.environ.get("ASSIST_PER_DAY", "200")):
        raise HTTPException(429, "The assistant has reached its daily limit. Please fill the form in by hand.")
    _assist_day[1] += 1
    try:
        return {"fields": await agent_service.assist_form(body)}
    except NotConfigured as e:
        raise HTTPException(503, str(e))
    except UpstreamError as e:
        raise HTTPException(502, str(e))


@app.post("/api/requests", status_code=201)
def create_request(body: RequestIn, request: Request, background: BackgroundTasks):
    limit("req", client_ip(request), int(os.environ.get("REQUESTS_PER_HOUR_PER_IP", "20")), 3600, "requests from this address")
    r = dbm.create_request(body.model_dump())
    required = delivery_service.verification_required()
    if required:
        background.add_task(verification.send_safely, r)   # same response whether or not mail is configured (no oracle)
    return {"request_id": r["id"], "message": "Request received", "verification_required": required, "request": r}


@app.post("/api/verify")
def verify_email(body: VerifyIn, request: Request):
    """Public: the requester confirms their address with the token from the emailed link (single use, expires)."""
    limit("verify", client_ip(request), 20, 3600, "confirmation attempts")
    if not verification.confirm(body.token):
        raise HTTPException(400, "This link is invalid, already used, or has expired.")   # one message for every failure: no oracle
    return {"ok": True}


@app.get("/verify")
def verify_page():
    return FileResponse(FRONTEND / "verify.html")


@app.get("/api/mail/status", dependencies=[Depends(factory_auth)])
def mail_status():
    return mailer.status()


@app.post("/api/requests/{request_id}/resend-verification", dependencies=[Depends(factory_auth)])
def resend_verification(request_id: str, body: ConfirmIn):
    r = dbm.get_request(request_id)
    if not r:
        raise HTTPException(404, f"Request {request_id} not found")
    if r.get("email_verified"):
        raise HTTPException(409, "This email is already verified.")
    _confirmed(body.confirm, f"email a confirmation link to {r['contact_email']}")
    try:
        mode = verification.send_verification(r)
    except verification.VerificationLimit as e:
        raise HTTPException(429, str(e))
    except mailer.MailNotConfigured as e:
        raise HTTPException(503, f"Email is not configured: {e}")
    except mailer.MailError as e:
        raise HTTPException(502, str(e))
    if r.get("customer_id"):
        dbm.add_event(r["customer_id"], "verification_sent", {"request": request_id, "mode": mode, "by": "staff"})
    return {"status": mode}


@app.post("/api/requests/{request_id}/verify-manually", dependencies=[Depends(factory_auth)])
def verify_manually(request_id: str, body: ManualVerifyIn):
    if not dbm.get_request(request_id):
        raise HTTPException(404, f"Request {request_id} not found")
    _confirmed(body.confirm, "record that you verified this requester yourself (your note is saved in the customer's timeline)")
    return dbm.mark_email_verified(request_id, "staff", body.note.strip())


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


@app.post("/api/requests/{request_id}/build", status_code=202, dependencies=[Depends(factory_auth)])
async def start_build(request_id: str):
    if not dbm.get_request(request_id):
        raise HTTPException(404, f"Request {request_id} not found")
    if build_service.is_running(request_id):
        raise HTTPException(409, "A build is already running for this request.")
    build_service.start(request_id)
    return {"started": True}


@app.get("/api/builds/{request_id}", dependencies=[Depends(factory_auth)])
def get_build(request_id: str):
    if not dbm.get_request(request_id):
        raise HTTPException(404, f"Request {request_id} not found")
    b = dbm.build_status(request_id)
    r = dbm.get_request(request_id)
    cust = dbm.get_customer(r["customer_id"]) if r.get("customer_id") else None
    b["delivery"] = [e for e in (dbm.list_events(cust["id"], 30) if cust else []) if e["kind"] in ("repo_created", "agent_added", "invited", "deliver_failed", "invite_failed")]
    return b


_TRACK = [("received", "Request received", "We have your request and an engineer is on it."),
          ("analyse", "Planning", "We are working out exactly what to build."),
          ("specify", "Designing", "We are writing the specification for your build."),
          ("test", "Testing", "We are running checks on real tasks."),
          ("package", "Packaging", "We are preparing your private repository."),
          ("ready", "Ready for handover", "Your build passed our checks and is waiting for final review.")]


@app.get("/api/track/{request_id}")
def track(request_id: str, request: Request, response: Response):
    """Public progress for a customer. Only step names and states: never company, spec, run output or error text."""
    limit("track", client_ip(request), int(os.environ.get("TRACK_PER_HOUR_PER_IP", "120")), 3600, "status checks")
    response.headers["Cache-Control"] = "no-store"
    if not dbm.get_request(request_id):
        raise HTTPException(404, "We could not find that reference.")
    b = dbm.build_status(request_id)
    steps = []
    for key, name, text in _TRACK:
        st = b["stages"].get(key, {}).get("state", "pending")
        steps.append({"key": key, "name": name, "text": text, "state": {"done": "done", "running": "active", "failed": "problem", "interrupted": "problem"}.get(st, "pending")})
    if b["status"] == "none":
        steps[0]["state"] = "done"           # the request exists, so it was received even before a build starts
        steps[1]["state"] = "active"
    t = b["stages"].get("test", {}).get("detail") or {}
    return {"status": b["status"], "steps": steps, "checks": {"passed": t.get("passed"), "total": t.get("total")} if t.get("total") else None}


@app.get("/track")
def track_page():
    return FileResponse(FRONTEND / "track.html")


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


# ---- delivery: one private GitHub repo per customer (see delivery_service) ----
SUPPORT_EMAIL = lambda: os.environ.get("SUPPORT_EMAIL", "")


@app.get("/api/github/status", dependencies=[Depends(factory_auth)])
def github_status():
    return get_github().status()


@app.get("/api/agents/{agent_id}/delivery-preview", dependencies=[Depends(factory_auth)])
def delivery_preview(agent_id: str):
    _agent_or_404(agent_id)
    return delivery_service.preview(get_github(), agent_id, SUPPORT_EMAIL())


@app.post("/api/agents/{agent_id}/deliver", status_code=201, dependencies=[Depends(factory_auth)])
def deliver(agent_id: str, body: DeliverIn):
    _agent_or_404(agent_id)
    _confirmed(body.confirm, "create/update the customer's private GitHub repository" + (" and invite them as a collaborator" if body.invite else ""))
    limit("gh", "factory", 20, 60, "delivery actions")
    return delivery_service.deliver(get_github(), agent_id, invite=body.invite, support_email=SUPPORT_EMAIL(), existing_repo_ack=body.existing_repo_ack)


@app.post("/api/requests/{request_id}/attach-customer", dependencies=[Depends(factory_auth)])
def attach_customer(request_id: str, body: AttachIn):
    _confirmed(body.confirm, "link this request to an existing customer, so its agents can be delivered into that customer's repository")
    return delivery_service.attach_request(request_id, body.customer_id)


@app.get("/api/customers", dependencies=[Depends(factory_auth)])
def customers():
    return dbm.list_customers()


@app.get("/api/customers/{customer_id}", dependencies=[Depends(factory_auth)])
def customer(customer_id: str):
    return delivery_service.customer_view(customer_id)


@app.get("/api/customers/{customer_id}/plugin-zip", dependencies=[Depends(factory_auth)])
def customer_plugin_zip(customer_id: str):
    """Fallback for customers without GitHub: the current plugin as a zip (no auto-update)."""
    data, plugin = delivery_service.plugin_zip_for(customer_id, SUPPORT_EMAIL())
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{plugin}.zip"'})


@app.post("/api/customers/{customer_id}/notes", status_code=201, dependencies=[Depends(factory_auth)])
def customer_note(customer_id: str, body: NoteIn):
    if not dbm.get_customer(customer_id):
        raise HTTPException(404, "Customer not found")
    repo = dbm.get_repo_for_customer(customer_id)
    return dbm.add_event(customer_id, "note", body.text.strip(), repo["id"] if repo else None)


@app.post("/api/customers/{customer_id}/onboarding", dependencies=[Depends(factory_auth)])
def customer_onboarding(customer_id: str, body: OnboardingIn):
    if not dbm.get_customer(customer_id):
        raise HTTPException(404, "Customer not found")
    return dbm.set_onboarding(customer_id, body.step, body.value)


@app.post("/api/customers/{customer_id}/refresh", dependencies=[Depends(factory_auth)])
def customer_refresh(customer_id: str):
    limit("gh", "factory", 20, 60, "delivery actions")
    return delivery_service.refresh(get_github(), customer_id)


@app.post("/api/customers/{customer_id}/sync", dependencies=[Depends(factory_auth)])
def customer_sync(customer_id: str, body: SyncIn):
    _confirmed(body.confirm, "push the latest agent versions to the customer's repository")
    limit("gh", "factory", 20, 60, "delivery actions")
    return delivery_service.sync(get_github(), customer_id, overwrite=body.overwrite_conflicts, support_email=SUPPORT_EMAIL())


@app.post("/api/customers/{customer_id}/reinvite", dependencies=[Depends(factory_auth)])
def customer_reinvite(customer_id: str, body: ConfirmIn):
    _confirmed(body.confirm, "send the customer a new GitHub invitation (GitHub emails them)")
    limit("gh", "factory", 20, 60, "delivery actions")
    return delivery_service.reinvite(get_github(), customer_id)


@app.post("/api/customers/{customer_id}/collaborators", status_code=201, dependencies=[Depends(factory_auth)])
def customer_collaborator(customer_id: str, body: CollaboratorIn):
    _confirmed(body.confirm, f"invite {body.username} to the customer's private repository (GitHub emails them)")
    limit("gh", "factory", 20, 60, "delivery actions")
    return delivery_service.add_collaborator(get_github(), customer_id, body.username)


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
