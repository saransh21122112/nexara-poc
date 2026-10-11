"""Customer-repo delivery: preview, deliver (create repo + commit + invite), sync, refresh, re-invite.
Rules: every outward action (create repo, push, invite) is explicit (the API requires confirm=true); nothing the customer or an
engineer edited by hand is silently overwritten (per-file blob SHAs from the last sync are compared with the remote);
a partial failure leaves the repo in `needs_retry` so a retry continues instead of creating a duplicate."""
import os
import threading

from . import database as dbm
from . import repo_gen
from .github_client import GitHubClient, GitHubError

MAX_NAME_TRIES = 20
_locks: dict[str, threading.Lock] = {}  # ponytail: in-process; use a DB advisory lock if the app runs more than one worker


class DeliveryError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Conflict(DeliveryError):
    def __init__(self, conflicts: list[dict]):
        super().__init__("Files in the repository were changed by hand since the last sync. Review them, then retry with overwrite.", 409)
        self.conflicts = conflicts


def _lock(customer_id: str) -> threading.Lock:
    return _locks.setdefault(customer_id, threading.Lock())


# ---------------------------------------------------------------- lookups
def ensure_customer(request: dict) -> dict:
    """Requests created before customers existed get one now (and are linked to it)."""
    if request.get("customer_id") and dbm.get_customer(request["customer_id"]):
        return dbm.get_customer(request["customer_id"])
    cust = dbm.upsert_customer(request)
    dbm.attach_customer(request["id"], cust["id"])
    return cust


def _agent_ctx(agent_id: str):
    agent = dbm.get_agent(agent_id)
    if not agent:
        raise DeliveryError(f"Agent {agent_id} not found", 404)
    request = dbm.get_request(agent["request_id"])
    return agent, request, ensure_customer(request)


def invitee(customer: dict, request: dict, repo_exists: bool) -> tuple[str | None, str | None]:
    """(username to invite, reason it is blocked). Only an identity already established for the customer is trusted: the
    request form is public, so a username typed into a request never gets write access to an existing repo on its own."""
    cu, ru = customer.get("github_username"), request.get("github_username")
    if cu and ru and cu.lower() != ru.lower():
        return None, (f"This request names GitHub user @{ru}, but the customer is @{cu}. Verify with the customer, then add "
                      f"@{ru} explicitly from the Customers page.")
    if cu:
        return cu, None
    if ru and not repo_exists:
        return ru, None            # first delivery founded by this very request
    return None, "No established GitHub user for this customer: add a collaborator explicitly once you have verified them." if ru else None


def verification_required() -> bool:
    """On by default. REQUIRE_EMAIL_VERIFICATION=false is an explicit operator opt-out (e.g. no SMTP yet); the preview then warns."""
    return os.environ.get("REQUIRE_EMAIL_VERIFICATION", "true").strip().lower() not in ("0", "false", "no", "off")


def unverified_reason(request: dict) -> str | None:
    if request.get("email_verified") or not verification_required():
        return None
    return (f"The email on this request ({request.get('contact_email')}) is not verified. The requester must click the link we emailed "
            f"them, or you can verify them another way and record how (Client Requests -> Mark verified).")


def _entries(repo: dict, extra: dict | None = None) -> list[dict]:
    """[{agent_id, slug, spec, spec_hash_synced}] for every agent in the repo (live spec), plus optionally one new agent."""
    out, taken = [], set()
    for ra in dbm.list_repo_agents(repo["id"]):
        live = dbm.get_agent(ra["agent_id"])
        if live:
            out.append({"agent_id": ra["agent_id"], "slug": ra["plugin_slug"], "spec": live["spec"], "synced_hash": ra["spec_hash"]})
            taken.add(ra["plugin_slug"])
    if extra and extra["agent_id"] not in {e["agent_id"] for e in out}:
        base = repo_gen.plugin_slug(extra["spec"])
        slug, n = base, 1
        while slug in taken:
            n += 1
            slug = f"{base}-{n}"
        out.append({**extra, "slug": slug, "synced_hash": None})
    return out


def _repo_name_for(gh: GitHubClient, customer: dict) -> str:
    base = f"nexara-{customer['slug']}"[:90]
    for n in range(1, MAX_NAME_TRIES + 1):
        name = base if n == 1 else f"{base}-{n}"
        if not gh.repo_exists(name):
            return name
    raise DeliveryError("Could not find a free repository name.", 409)


def onboarding_draft(customer: dict, repo_url: str, owner: str, name: str, agents: list[dict]) -> str:
    status = customer.get("claude_status")
    osn = customer.get("os")
    install = {"mac": "curl -fsSL https://claude.ai/install.sh | bash", "linux": "curl -fsSL https://claude.ai/install.sh | bash",
               "windows": "irm https://claude.ai/install.ps1 | iex (in PowerShell)"}.get(osn or "", "see START-HERE.md for your system")
    step = {
        "have_claude_code": "You already use Claude Code, so you can skip the install step.",
        "have_claude_account": "You have a Claude account: the only extra step is installing Claude Code (about two minutes): " + install + ".",
        "no_claude": "You will need a paid Claude account (Pro, Max, Team or Enterprise; the free plan does not include Claude Code) and to install Claude Code: " + install + ". START-HERE.md walks through it, and we are happy to do a short call.",
        "not_sure": "Run `claude --version` in a terminal. If it prints a version you are set; if not, START-HERE.md shows the two-minute install.",
    }.get(status or "", "START-HERE.md covers installing Claude Code if you need it.")
    names = ", ".join(a["spec"]["agent_name"] for a in agents) or "your agent"
    return (f"Hi,\n\nYour Claude Code agents ({names}) are ready in a private GitHub repository just for {customer['company_name']}:\n"
            f"{repo_url}\n\n"
            f"1. GitHub emails you an invitation to this repository (it expires after 7 days). Accept it: {repo_url}/invitations or the email.\n"
            f"2. {step}\n"
            f"3. Open {repo_url}/blob/main/START-HERE.md and run scripts/setup.sh (Windows: scripts/setup.ps1). It connects the agents to your project and turns on auto-update, so improvements we make arrive on their own.\n\n"
            f"If anything is unclear, run scripts/doctor.sh and send us the output, or open an issue in the repository.\n\nBest regards,\nNexara AI\n")


# ---------------------------------------------------------------- preview
def preview(gh: GitHubClient, agent_id: str, support_email: str = "") -> dict:
    agent, request, customer = _agent_ctx(agent_id)
    repo = dbm.get_repo_for_customer(customer["id"])
    warnings = []
    if not agent["tested"]:
        warnings.append("This agent has not been run or tested yet.")
    elif agent.get("last_eval") and agent["last_eval"].get("pass_rate", 1) < 1:
        warnings.append(f"Last test pass rate was {round(agent['last_eval']['pass_rate'] * 100)}%.")
    if not customer.get("github_username") and not request.get("github_username"):
        warnings.append("No GitHub username on file: the customer cannot be invited.")
    user, blocked = invitee(customer, request, bool(repo))
    unverified = unverified_reason(request)
    if not request.get("email_verified") and not verification_required():
        warnings.append("Email verification is switched OFF (REQUIRE_EMAIL_VERIFICATION=false): nobody has confirmed that "
                        f"{request.get('contact_email')} belongs to the requester. Check this request really comes from this customer.")
    if blocked:
        warnings.append(blocked)
    if repo and (request.get("contact_email") or "").lower() != (customer.get("contact_email") or "").lower():
        warnings.append(f"This request's contact email ({request.get('contact_email')}) differs from the customer's ({customer.get('contact_email')}).")
    if repo and dbm.list_repo_agents(repo["id"]) and dbm.get_repo_agent(repo["id"], agent_id) is None:
        warnings.append("This adds content to an EXISTING customer repository. Check that this request really comes from this customer.")
    entries = _entries(repo, {"agent_id": agent_id, "spec": agent["spec"]}) if repo else [{"agent_id": agent_id, "slug": repo_gen.plugin_slug(agent["spec"]), "spec": agent["spec"], "synced_hash": None}]
    in_repo = bool(repo) and dbm.get_repo_agent(repo["id"], agent_id) is not None
    name = repo["name"] if repo else _repo_name_for(gh, customer)
    owner = repo["owner"] if repo else gh.owner
    files = repo_gen.repo_files(customer, {"owner": owner, "name": name}, entries, support_email)
    url = repo["url"] if repo else f"https://github.com/{owner}/{name}"
    invited_already = bool(repo) and (repo.get("invited_user") or "").lower() == (user or "").lower() and repo.get("invite_status") in ("pending", "accepted")
    return {
        "mode": "update" if in_repo else ("add" if repo else "create"),
        "customer": {k: customer[k] for k in ("id", "company_name", "slug", "github_username", "claude_status", "os")},
        "repo": {"owner": owner, "name": name, "url": url, "exists": bool(repo), "private": True},
        "plugin_slug": next(e["slug"] for e in entries if e["agent_id"] == agent_id),
        "files": sorted(files) + ["CHANGELOG.md"],
        "request": {"id": request["id"], "company_name": request.get("company_name"), "contact_email": request.get("contact_email"),
                    "github_username": request.get("github_username"), "problem": (request.get("business_problem") or "")[:300]},
        "invite": {"username": user, "blocked_reason": blocked, "will_invite": bool(user) and not invited_already, "access": "write (GitHub gives collaborators on personal-account repos write access)"},
        "onboarding_draft": onboarding_draft(customer, url, owner, name, entries),
        "warnings": warnings,
        "blocked": [unverified] if unverified else [],
        "email_verified": bool(request.get("email_verified")),
    }


# ---------------------------------------------------------------- core push
def _conflicts(files: dict[str, str], stored: dict[str, str], remote: dict[str, str]) -> list[dict]:
    out = []
    for path, content in files.items():
        want, have, known = repo_gen.git_blob_sha(content), remote.get(path), stored.get(path)
        if have == want or not stored:        # unchanged, or first delivery (auto-init README etc. may be replaced)
            continue
        if have is not None and known is not None and have != known:
            out.append({"path": path, "why": "edited in the repository since the last sync"})
        elif have is None and known is not None:
            out.append({"path": path, "why": "deleted from the repository since the last sync"})
        elif have is not None and known is None:
            out.append({"path": path, "why": "a file with this name was added in the repository"})
    for path, known in stored.items():
        if path not in files and remote.get(path) not in (None, known):
            out.append({"path": path, "why": "edited in the repository but no longer generated"})
    return out


def _push(gh: GitHubClient, customer: dict, repo: dict, entries: list[dict], message_lines: list[str], overwrite: bool,
          support_email: str) -> dict:
    files = repo_gen.repo_files(customer, {"owner": repo["owner"], "name": repo["name"]}, entries, support_email)
    base = gh.head(repo["name"])
    remote = gh.tree(repo["name"], base)
    stored = dict(repo.get("files") or {})
    conflicts = _conflicts(files, stored, remote)
    if conflicts and not overwrite:
        raise Conflict(conflicts)
    changes: dict[str, str | None] = {p: c for p, c in files.items() if remote.get(p) != repo_gen.git_blob_sha(c)}
    for path, known in stored.items():
        if path not in files and remote.get(path) is not None:
            changes[path] = None                      # no longer generated -> remove
    if not changes:
        return {"status": "up_to_date", "commit": base, "changed": []}
    entry = repo_gen.changelog_entry(message_lines)
    old = gh.blob_text(repo["name"], remote["CHANGELOG.md"]) if stored and "CHANGELOG.md" in remote else None  # first delivery starts a new file
    changes["CHANGELOG.md"] = repo_gen.prepend_changelog(old, entry) if old else repo_gen.new_changelog(entry)
    commit = gh.commit_files(repo["name"], base, changes, "Nexara: " + "; ".join(message_lines)[:200],
                             executable=frozenset(p for p in changes if changes[p] is not None and repo_gen.is_executable(p)))
    tracked = {p: repo_gen.git_blob_sha(c) for p, c in files.items() if repo_gen.is_tracked(p)}
    dbm.update_repo(repo["id"], head_sha=commit, synced_at=dbm.now(), state="ready", last_error=None)
    dbm.set_repo_files(repo["id"], tracked)
    for e in entries:
        dbm.upsert_repo_agent(repo["id"], e["agent_id"], e["slug"], repo_gen.spec_hash(e["spec"]))
    return {"status": "pushed", "commit": commit, "changed": sorted(p for p in changes if p != "CHANGELOG.md"),
            "overwritten": [c["path"] for c in conflicts]}


# ---------------------------------------------------------------- deliver
def deliver(gh: GitHubClient, agent_id: str, invite: bool = True, overwrite: bool = False, support_email: str = "",
            existing_repo_ack: bool = False) -> dict:
    agent, request, customer = _agent_ctx(agent_id)
    if reason := unverified_reason(request):
        raise DeliveryError(reason + " Nothing was created.", 409)
    with _lock(customer["id"]):
        repo = dbm.get_repo_for_customer(customer["id"])
        user, blocked = invitee(customer, request, bool(repo))
        if invite and blocked and (request.get("github_username") or customer.get("github_username")):
            raise DeliveryError(blocked + " Nothing was created. Deliver without the invitation to proceed.", 409)
        has_content = bool(repo) and bool(dbm.list_repo_agents(repo["id"]))        # an empty repo from a failed first delivery is just a retry
        if has_content and not existing_repo_ack and dbm.get_repo_agent(repo["id"], agent_id) is None:
            raise DeliveryError("This adds content to an existing customer repository. Acknowledge that you checked the request "
                                "comes from this customer (existing_repo_ack=true).", 409)
        created = False
        if not repo:
            name = _repo_name_for(gh, customer)
            made = gh.create_repo(name, f"Claude Code agents for {customer['company_name']} (Nexara AI)")
            repo = dbm.create_repo(customer["id"], gh.owner, made["name"], made["url"])
            dbm.add_event(customer["id"], "repo_created", {"repo": f"{gh.owner}/{made['name']}", "private": True}, repo["id"])
            created = True
        try:
            entries = _entries(repo, {"agent_id": agent_id, "spec": agent["spec"]})
            slug = next(e["slug"] for e in entries if e["agent_id"] == agent_id)
            first = not dbm.list_repo_agents(repo["id"])
            lines = [f"{'Initial delivery: ' if created or first else 'Added/updated agent: '}{agent['spec']['agent_name']} (/{slug})"]
            result = _push(gh, customer, repo, entries, lines, overwrite, support_email)
        except Conflict:
            raise
        except Exception as e:
            dbm.update_repo(repo["id"], state="needs_retry", last_error=str(e)[:500])
            dbm.add_event(customer["id"], "deliver_failed", str(e)[:300], repo["id"])
            raise
        dbm.add_event(customer["id"], "agent_added", {"agent": agent["spec"]["agent_name"], "slug": slug, "commit": result["commit"]}, repo["id"])
        repo = dbm.get_repo(repo["id"])
        invite_result = None
        if invite and user:
            invite_result = _invite(gh, customer, repo, user)
        url = repo["url"]
        return {"status": result["status"], "repo": {"owner": repo["owner"], "name": repo["name"], "url": url},
                "commit": result["commit"], "changed": result["changed"], "plugin_slug": slug, "invite": invite_result,
                "onboarding_draft": onboarding_draft(customer, url, repo["owner"], repo["name"], entries)}


def _invite(gh: GitHubClient, customer: dict, repo: dict, user: str) -> dict:
    try:
        r = gh.add_collaborator(repo["name"], user)
    except GitHubError as e:
        dbm.update_repo(repo["id"], last_error=f"invite failed: {e}"[:500])
        dbm.add_event(customer["id"], "invite_failed", {"user": user, "error": str(e)[:300]}, repo["id"])
        return {"status": "failed", "username": user, "error": str(e)}
    status = "accepted" if r["status"] == "already_collaborator" else "pending"
    dbm.update_repo(repo["id"], invited_user=user, invitation_id=r["invitation_id"], invite_status=status, last_error=None)
    dbm.add_event(customer["id"], "invited", {"user": user, "status": status}, repo["id"])
    return {"status": status, "username": user, "invitation_id": r["invitation_id"]}


# ---------------------------------------------------------------- sync / refresh / reinvite
def _customer_repo(customer_id: str):
    cust = dbm.get_customer(customer_id)
    if not cust:
        raise DeliveryError("Customer not found", 404)
    repo = dbm.get_repo_for_customer(customer_id)
    if not repo:
        raise DeliveryError("This customer has no repository yet: deliver an agent first.", 409)
    return cust, repo


def behind(entries: list[dict]) -> list[dict]:
    return [e for e in entries if e["synced_hash"] != repo_gen.spec_hash(e["spec"])]


def sync(gh: GitHubClient, customer_id: str, overwrite: bool = False, support_email: str = "") -> dict:
    cust, repo = _customer_repo(customer_id)
    with _lock(customer_id):
        entries = _entries(repo)
        changed_agents = behind(entries)
        lines = [f"Updated agent: {e['spec']['agent_name']} (/{e['slug']})" for e in changed_agents] or ["Re-synced repository files"]
        try:
            result = _push(gh, cust, repo, entries, lines, overwrite, support_email)
        except Conflict as c:
            dbm.add_event(customer_id, "sync_conflict", {"paths": [x["path"] for x in c.conflicts]}, repo["id"])
            raise
        except Exception as e:
            dbm.update_repo(repo["id"], state="needs_retry", last_error=str(e)[:500])
            raise
        if result["status"] == "pushed":
            dbm.add_event(customer_id, "synced", {"commit": result["commit"], "files": result["changed"][:30], "overwrote": result.get("overwritten", [])}, repo["id"])
        return result


def refresh(gh: GitHubClient, customer_id: str) -> dict:
    """Pulls live state from GitHub: invitation/collaborator status, hand-made changes, open issues. Records transitions."""
    cust, repo = _customer_repo(customer_id)
    out = {"remote_head": None, "manual_changes": [], "issues": [], "invite_status": repo["invite_status"], "errors": []}
    try:
        head = gh.head(repo["name"])
        out["remote_head"] = head
        remote = gh.tree(repo["name"], head)
        stored = repo.get("files") or {}
        out["manual_changes"] = sorted(p for p, known in stored.items() if remote.get(p) != known)
    except GitHubError as e:
        out["errors"].append(str(e))
    user = repo.get("invited_user")
    if user:
        try:
            if gh.collaborator_status(repo["name"], user) == "collaborator":
                new = "accepted"
            else:
                inv = [i for i in gh.invitations(repo["name"]) if i["login"].lower() == user.lower()]
                new = "none" if not inv else ("expired" if inv[0]["expired"] else "pending")
                if not inv and repo["invite_status"] == "pending":
                    new = "expired"   # invitation vanished without being accepted
            if new != repo["invite_status"]:
                dbm.update_repo(repo["id"], invite_status=new)
                dbm.add_event(cust["id"], f"invite_{new}", {"user": user}, repo["id"])
            out["invite_status"] = new
        except GitHubError as e:
            out["errors"].append(str(e))
    try:
        out["issues"] = gh.open_issues(repo["name"])
    except GitHubError as e:
        out["errors"].append(str(e))
    return out


def reinvite(gh: GitHubClient, customer_id: str) -> dict:
    cust, repo = _customer_repo(customer_id)
    user = repo.get("invited_user") or cust.get("github_username")
    if not user:
        raise DeliveryError("No GitHub username on file for this customer.", 409)
    with _lock(customer_id):
        if repo.get("invitation_id"):
            try:
                gh.cancel_invitation(repo["name"], repo["invitation_id"])
            except GitHubError:
                pass  # already gone/expired
        return _invite(gh, cust, repo, user)


def add_collaborator(gh: GitHubClient, customer_id: str, username: str) -> dict:
    cust, repo = _customer_repo(customer_id)
    try:
        r = gh.add_collaborator(repo["name"], username)
    except GitHubError as e:
        dbm.add_event(customer_id, "invite_failed", {"user": username, "error": str(e)[:300]}, repo["id"])
        raise
    dbm.add_event(customer_id, "invited", {"user": username, "status": "accepted" if r["status"] == "already_collaborator" else "pending", "extra": True}, repo["id"])
    return {"username": username, **r}


# ---------------------------------------------------------------- read model for the UI
def customer_view(customer_id: str) -> dict:
    cust = dbm.get_customer(customer_id)
    if not cust:
        raise DeliveryError("Customer not found", 404)
    repo = dbm.get_repo_for_customer(customer_id)
    agents, requests = [], []
    for r in dbm.list_requests():
        if r.get("customer_id") == customer_id:
            requests.append({"id": r["id"], "agent_type": r["agent_type"], "created_at": r["created_at"], "agent_count": r["agent_count"],
                             "github_username": r.get("github_username"), "email_verified": bool(r.get("email_verified"))})
    if repo:
        entries = _entries(repo)
        stale = {e["agent_id"] for e in behind(entries)}
        agents = [{"agent_id": e["agent_id"], "agent_name": e["spec"]["agent_name"], "plugin_slug": e["slug"], "behind": e["agent_id"] in stale}
                  for e in entries]
    return {"customer": cust, "repo": {k: v for k, v in repo.items() if k != "files"} if repo else None, "agents": agents,
            "requests": requests, "events": dbm.list_events(customer_id)}


def attach_request(request_id: str, customer_id: str) -> dict:
    """Deliberate staff action: link a request to an existing customer (e.g. a colleague of the same company)."""
    req = dbm.get_request(request_id)
    cust = dbm.get_customer(customer_id)
    if not req or not cust:
        raise DeliveryError("Request or customer not found", 404)
    previous = req.get("customer_id")
    dbm.attach_customer(request_id, customer_id)
    dbm.add_event(customer_id, "request_attached", {"request": request_id, "by": "staff", "previous_customer": previous,
                                                    "request_email": req.get("contact_email"), "request_github": req.get("github_username")})
    return {"request_id": request_id, "customer_id": customer_id}
