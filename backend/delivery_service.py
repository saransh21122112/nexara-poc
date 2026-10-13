"""Customer-repo delivery: preview, deliver (create repo + commit + invite), sync, refresh, re-invite.
Rules: every outward action (create repo, push, invite) is explicit (the API requires confirm=true); nothing the customer or an
engineer edited by hand is silently overwritten (per-file blob SHAs from the last sync are compared with the remote);
a partial failure leaves the repo in `needs_retry` so a retry continues instead of creating a duplicate."""
import difflib
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


class DevBranchBusy(DeliveryError):
    def __init__(self, authors: list[str]):
        super().__init__("The 'dev' branch of this repository has commits that Nexara did not make: "
                         + "; ".join(a[:80] for a in authors[:3]) + ". Look at them first, or retry with overwrite_dev to replace them.", 409)


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


def _repo_files(*a, **k) -> dict[str, str]:
    """repo_gen.repo_files, with a component-name clash between two agents reported as a normal conflict instead of a server error."""
    try:
        return repo_gen.repo_files(*a, **k)
    except ValueError as e:
        raise DeliveryError(f"These agents cannot share one plugin: {e}. Rename a component in one of them.", 409) from None


def code_status(spec: dict, checks: dict | None) -> dict:
    """Does this spec ship code (tool scripts / MCP servers), and have its checks passed for EXACTLY this content?"""
    from .artifacts import checks as code_checks
    has_code = bool(spec.get("scripts") or spec.get("mcp_servers"))
    c = checks if checks and checks.get("spec_digest") == code_checks.spec_digest(spec) else {}
    return {"has_code": has_code, "verified": bool(c.get("verified")) if has_code else True, "ran_code": bool(c.get("ran_code")), "reason": c.get("reason", "") if has_code else ""}


def require_code_ack(items: list[tuple[str, dict]], ack: bool) -> None:
    """items = [(agent_id, spec as it will be written)]. Code nobody has run is only delivered when a person says so explicitly."""
    if ack:
        return
    names = []
    for agent_id, spec in items:
        live = dbm.get_agent(agent_id)
        if code_status(spec, (live or {}).get("checks"))["has_code"] and not code_status(spec, (live or {}).get("checks"))["verified"]:
            names.append(spec["agent_name"])
    if names:
        raise DeliveryError("This delivery contains code whose checks have not passed on this host: " + ", ".join(names)
                            + ". Run the checks first, or acknowledge that a person reviewed the code (unverified_code_ack=true).", 409)


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


def _unique(base: str, taken: set) -> str:
    slug, n = base, 1
    while slug in taken:
        n += 1
        slug = f"{base}-{n}"
    return slug


def _migrating(repo: dict) -> bool:
    return (repo.get("layout") or 2) < 3          # layout 2 = one plugin per agent under plugins/; 3 = the repo root is the plugin


def legacy_names(repo: dict) -> list[str]:
    """Plugin names an older layout used (one plugin per agent): they go into the marketplace `renames` map for good."""
    names = set(repo.get("legacy") or [])
    if _migrating(repo):
        names |= {ra["plugin_slug"] for ra in dbm.list_repo_agents(repo["id"])}
    return sorted(names)


def _entries(repo: dict, extra: dict | None = None) -> list[dict]:
    """[{agent_id, slug, spec, synced_hash}] for every agent in the repo (live spec), plus optionally one new agent.
    A repo still on the old layout gets readable slugs recomputed (TokenSpendAnalyst -> token-spend-analyst) when it is next synced."""
    out, taken = [], set()
    for ra in dbm.list_repo_agents(repo["id"]):
        live = dbm.get_agent(ra["agent_id"])
        if live:
            slug = _unique(repo_gen.plugin_slug(live["spec"]), taken) if _migrating(repo) else ra["plugin_slug"]
            out.append({"agent_id": ra["agent_id"], "slug": slug, "spec": live["spec"], "synced_hash": ra["spec_hash"]})
            taken.add(slug)
    if extra and extra["agent_id"] not in {e["agent_id"] for e in out}:
        out.append({**extra, "slug": _unique(repo_gen.plugin_slug(extra["spec"]), taken), "synced_hash": None})
    return out


def _repo_name_for(gh: GitHubClient, customer: dict) -> str:
    base = f"nexara-{customer['slug']}"[:90]
    for n in range(1, MAX_NAME_TRIES + 1):
        name = base if n == 1 else f"{base}-{n}"
        if not gh.repo_exists(name):
            return name
    raise DeliveryError("Could not find a free repository name.", 409)


def onboarding_draft(customer: dict, repo_url: str, owner: str, name: str, agents: list[dict]) -> str:
    """A message for a HUMAN to send. Leads with the one-line install; extra steps only for people who need them."""
    status = customer.get("claude_status")
    plugin = repo_gen.company_slug(customer["slug"])
    install = {"windows": "irm https://claude.ai/install.ps1 | iex   (in PowerShell)"}.get(customer.get("os") or "", "curl -fsSL https://claude.ai/install.sh | bash")
    clone = (f"gh repo clone {owner}/{name} $HOME\\.claude\\skills\\{plugin}" if customer.get("os") == "windows"
             else f"gh repo clone {owner}/{name} ~/.claude/skills/{plugin}")
    prep = {
        "have_claude_code": "You already use Claude Code, so that is all you need.",
        "have_claude_account": f"You have a Claude account, so first install Claude Code (two minutes): {install} - then open a new terminal and run `claude` once to sign in.",
        "no_claude": f"You will need a paid Claude plan (Pro, Max, Team or Enterprise; the free plan does not include Claude Code). Then install Claude Code: {install} - open a new terminal and run `claude` once to sign in. START-HERE.md walks through everything, and we are happy to do a short call.",
        "not_sure": f"Run `claude --version` in a terminal. If it prints a version you are set; if not, install it with: {install}",
    }.get(status or "", "START-HERE.md covers installing Claude Code if you need it.")
    names = ", ".join(a["spec"]["agent_name"] for a in agents) or "your agent"
    cmds = ", ".join(f"/{plugin}:{a['slug']}" for a in agents)
    return (f"Hi,\n\nYour Claude Code agents ({names}) are ready in a private GitHub repository just for {customer['company_name']}:\n{repo_url}\n\n"
            f"1. GitHub emails you an invitation to that repository (it expires after 7 days). Accept it: {repo_url}/invitations\n"
            f"2. {prep}\n"
            f"3. Let git read the private repo once: `gh auth login` then `gh auth setup-git` (GitHub CLI: https://cli.github.com).\n"
            f"4. Install your agents with one command:\n   {clone}\n"
            f"5. Open Claude Code in any project and run {cmds or 'your agent'} followed by what you want done.\n\n"
            f"To update later: `git -C ~/.claude/skills/{plugin} pull`. Prefer automatic updates, or no GitHub account? Both are explained in START-HERE.md in the repo "
            f"(or ask us for a zip).\n\nIf anything is unclear, run scripts/doctor.sh and send us the output, or open an issue in the repository.\n\nBest regards,\nNexara AI\n")


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
    code = code_status(agent["spec"], agent.get("checks"))
    if code["has_code"] and not code["verified"]:
        warnings.append("This contains code that has not been run and checked here" + (f" ({code['reason']})" if code["reason"] else "") + ". Review the Artifact panel before delivering.")
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
    files = _repo_files(customer, {"owner": owner, "name": name}, entries, support_email, legacy_names(repo) if repo else [])
    url = repo["url"] if repo else f"https://github.com/{owner}/{name}"
    invited_already = bool(repo) and (repo.get("invited_user") or "").lower() == (user or "").lower() and repo.get("invite_status") in ("pending", "accepted")
    return {
        "mode": "update" if in_repo else ("add" if repo else "create"),
        "customer": {k: customer[k] for k in ("id", "company_name", "slug", "github_username", "claude_status", "os")},
        "repo": {"owner": owner, "name": name, "url": url, "exists": bool(repo), "private": True},
        "plugin_slug": next(e["slug"] for e in entries if e["agent_id"] == agent_id),
        "plugin": repo_gen.company_slug(customer["slug"]),
        "command": f"/{repo_gen.company_slug(customer['slug'])}:{next(e['slug'] for e in entries if e['agent_id'] == agent_id)}",
        "files": sorted(files) + ["CHANGELOG.md"],
        "request": {"id": request["id"], "company_name": request.get("company_name"), "contact_email": request.get("contact_email"),
                    "github_username": request.get("github_username"), "problem": (request.get("business_problem") or "")[:300]},
        "invite": {"username": user, "blocked_reason": blocked, "will_invite": bool(user) and not invited_already, "access": "write (GitHub gives collaborators on personal-account repos write access)"},
        "onboarding_draft": onboarding_draft(customer, url, owner, name, entries),
        "warnings": warnings,
        "code": code,
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


DEV = "dev"
MAIN = "main"


def _plan(gh: GitHubClient, customer: dict, repo: dict, entries: list[dict], message_lines: list[str], overwrite: bool, support_email: str) -> dict:
    """Pure computation against what is on main right now: the files to write, hand-edit conflicts and the resulting changes."""
    legacy = legacy_names(repo)
    files = _repo_files(customer, {"owner": repo["owner"], "name": repo["name"]}, entries, support_email, legacy)
    base = gh.head(repo["name"])
    remote = gh.tree(repo["name"], base)
    # The customer's own area (custom/) is created once if missing and then left strictly alone: even the starter file we ship there
    # may be edited, and a reworded template must never be treated as a conflict or overwrite it.
    files = {p: c for p, c in files.items() if repo_gen.is_tracked(p) or p not in remote}
    stored = dict(repo.get("files") or {})
    conflicts = _conflicts(files, stored, remote)
    if conflicts and not overwrite:
        raise Conflict(conflicts)
    changes: dict[str, str | None] = {p: c for p, c in files.items() if remote.get(p) != repo_gen.git_blob_sha(c)}
    for path, known in stored.items():
        if path not in files and remote.get(path) is not None:
            changes[path] = None                      # no longer generated -> remove
    if changes:
        entry = repo_gen.changelog_entry(message_lines)
        old = gh.blob_text(repo["name"], remote["CHANGELOG.md"]) if stored and "CHANGELOG.md" in remote else None  # first delivery starts a new file
        changes["CHANGELOG.md"] = repo_gen.prepend_changelog(old, entry) if old else repo_gen.new_changelog(entry)
    return {"files": files, "base": base, "remote": remote, "changes": changes, "conflicts": conflicts, "legacy": legacy,
            "tracked": {p: repo_gen.git_blob_sha(c) for p, c in files.items() if repo_gen.is_tracked(p)}}


def _record_main(repo: dict, plan: dict, entries: list[dict], commit: str, legacy: list[str]):
    dbm.update_repo(repo["id"], head_sha=commit, synced_at=dbm.now(), state="ready", last_error=None, layout=repo_gen.GENERATOR_VERSION)
    dbm.set_repo_legacy(repo["id"], legacy)
    dbm.set_repo_files(repo["id"], plan["tracked"])
    for e in entries:
        dbm.upsert_repo_agent(repo["id"], e["agent_id"], e["slug"], repo_gen.spec_hash(e["spec"]))


def _push(gh: GitHubClient, customer: dict, repo: dict, entries: list[dict], message_lines: list[str], overwrite: bool,
          support_email: str, via_pr: bool = False, overwrite_dev: bool = False) -> dict:
    """First delivery commits to main (a PR needs a main to merge into). Every later change goes to `dev` and a pull request."""
    plan = _plan(gh, customer, repo, entries, message_lines, overwrite, support_email)
    changes, base = plan["changes"], plan["base"]
    if not changes:
        return {"status": "up_to_date", "commit": base, "changed": []}
    executable = frozenset(p for p in changes if changes[p] is not None and repo_gen.is_executable(p))
    msg = "Nexara: " + "; ".join(message_lines)[:200]
    changed = sorted(p for p in changes if p != "CHANGELOG.md")
    if not via_pr:
        commit = gh.commit_files(repo["name"], base, changes, msg, executable=executable)
        _record_main(repo, plan, entries, commit, plan["legacy"])
        return {"status": "pushed", "commit": commit, "changed": changed, "overwritten": [c["path"] for c in plan["conflicts"]]}
    return _propose(gh, customer, repo, entries, message_lines, plan, msg, executable, overwrite_dev)


def _propose(gh, customer, repo, entries, message_lines, plan, msg, executable, overwrite_dev) -> dict:
    name, changes = repo["name"], plan["changes"]
    if gh.branch_head(name, DEV) is not None:
        foreign = [c["message"].splitlines()[0] for c in gh.commits_ahead(name, MAIN, DEV) if not c["message"].startswith("Nexara:")]
        if foreign and not overwrite_dev:
            raise DevBranchBusy(foreign)
    commit = gh.commit_files(name, plan["base"], changes, msg, executable=executable, branch=None)   # main + one Nexara commit
    gh.set_branch(name, DEV, commit)
    changed = sorted(p for p in changes if p != "CHANGELOG.md")
    title = "Nexara update: " + "; ".join(message_lines)[:200]
    body = ("This update was prepared by Nexara AI and is waiting for review. Nothing is merged automatically.\n\n"
            + "\n".join(f"- {l}" for l in message_lines) + "\n\nFiles changed:\n" + "\n".join(f"- `{p}`" for p in changed[:60]))
    pr = gh.find_open_pr(name, DEV, MAIN)
    opened = pr is None
    pr = gh.create_pr(name, DEV, MAIN, title, body) if opened else gh.update_pr(name, pr["number"], title, body)
    agents = [{"agent_id": e["agent_id"], "slug": e["slug"], "spec_hash": repo_gen.spec_hash(e["spec"])} for e in entries]
    cur = dbm.open_change(repo["id"])
    fields = dict(pr_number=pr["number"], pr_url=pr["url"], title=title, summary=message_lines, commit_sha=commit, files=plan["tracked"], agents=agents)
    if cur and cur["pr_number"] == pr["number"]:
        ch = dbm.update_change(cur["id"], **fields)
    else:
        if cur:
            dbm.update_change(cur["id"], state="closed")
        ch = dbm.create_change(repo["id"], DEV, pr["number"], pr["url"], title, message_lines, commit, plan["tracked"], agents)
    dbm.update_repo(repo["id"], state="ready", last_error=None)
    dbm.add_event(customer["id"], "pr_opened" if opened else "pr_updated", {"pr": pr["number"], "url": pr["url"], "files": changed[:30]}, repo["id"])
    return {"status": "proposed", "commit": commit, "changed": changed, "pull_request": pr, "change_id": ch["id"],
            "overwritten": [c["path"] for c in plan["conflicts"]]}


# ---------------------------------------------------------------- deliver
def deliver(gh: GitHubClient, agent_id: str, invite: bool = True, overwrite: bool = False, support_email: str = "",
            existing_repo_ack: bool = False, overwrite_dev: bool = False, unverified_code_ack: bool = False) -> dict:
    agent, request, customer = _agent_ctx(agent_id)
    if reason := unverified_reason(request):
        raise DeliveryError(reason + " Nothing was created.", 409)
    with _lock(customer["id"]):
        repo = dbm.get_repo_for_customer(customer["id"])
        user, blocked = invitee(customer, request, bool(repo))
        if invite and blocked and (request.get("github_username") or customer.get("github_username")):
            raise DeliveryError(blocked + " Nothing was created. Deliver without the invitation to proceed.", 409)
        # a delivery rewrites the whole repository: every agent it will push needs to be verified, not only the one being delivered
        require_code_ack([(e["agent_id"], e["spec"]) for e in (_entries(repo, {"agent_id": agent_id, "spec": agent["spec"]}) if repo else [{"agent_id": agent_id, "spec": agent["spec"]}])],
                         unverified_code_ack)
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
            require_code_ack([(e["agent_id"], e["spec"]) for e in entries], unverified_code_ack)      # again on exactly what is pushed (a spec may have been edited meanwhile)
            slug = next(e["slug"] for e in entries if e["agent_id"] == agent_id)
            first = not dbm.list_repo_agents(repo["id"])
            lines = [f"{'Initial delivery: ' if created or first else 'Added/updated agent: '}{agent['spec']['agent_name']} (/{slug})"]
            result = _push(gh, customer, repo, entries, lines, overwrite, support_email, via_pr=not (created or first), overwrite_dev=overwrite_dev)
        except (Conflict, DevBranchBusy):
            raise
        except Exception as e:
            dbm.update_repo(repo["id"], state="needs_retry", last_error=str(e)[:500])
            dbm.add_event(customer["id"], "deliver_failed", str(e)[:300], repo["id"])
            raise
        dbm.add_event(customer["id"], "agent_proposed" if result["status"] == "proposed" else "agent_added",
                      {"agent": agent["spec"]["agent_name"], "slug": slug, "commit": result["commit"]}, repo["id"])
        repo = dbm.get_repo(repo["id"])
        invite_result = None
        if invite and user:
            invite_result = _invite(gh, customer, repo, user)
        url = repo["url"]
        return {"status": result["status"], "repo": {"owner": repo["owner"], "name": repo["name"], "url": url},
                "pull_request": result.get("pull_request"), "commit": result["commit"], "changed": result["changed"], "plugin_slug": slug, "plugin": repo_gen.company_slug(customer["slug"]),
                "command": f"/{repo_gen.company_slug(customer['slug'])}:{slug}", "invite": invite_result,
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


def sync(gh: GitHubClient, customer_id: str, overwrite: bool = False, support_email: str = "", overwrite_dev: bool = False, unverified_code_ack: bool = False) -> dict:
    """Propose the current specs as an update: branch `dev` + pull request into `main` (nothing is merged by us)."""
    cust, repo = _customer_repo(customer_id)
    with _lock(customer_id):
        entries = _entries(repo)
        changed_agents = behind(entries)
        require_code_ack([(e["agent_id"], e["spec"]) for e in entries], unverified_code_ack)
        lines = [f"Updated agent: {e['spec']['agent_name']} (/{e['slug']})" for e in changed_agents] or ["Re-synced repository files"]
        try:
            result = _push(gh, cust, repo, entries, lines, overwrite, support_email, via_pr=bool(dbm.list_repo_agents(repo["id"])), overwrite_dev=overwrite_dev)
        except DevBranchBusy:
            raise
        except Conflict as c:
            dbm.add_event(customer_id, "sync_conflict", {"paths": [x["path"] for x in c.conflicts]}, repo["id"])
            raise
        except Exception as e:
            dbm.update_repo(repo["id"], state="needs_retry", last_error=str(e)[:500])
            raise
        if result["status"] in ("pushed", "proposed"):
            dbm.add_event(customer_id, "synced" if result["status"] == "pushed" else "sync_proposed", {"commit": result["commit"], "files": result["changed"][:30], "overwrote": result.get("overwritten", [])}, repo["id"])
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
    out["change"] = _check_change(gh, cust, repo, out["errors"])
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


def _check_change(gh: GitHubClient, cust: dict, repo: dict, errors: list) -> dict | None:
    """If a proposed update was merged or closed on GitHub, record it: merged -> main now holds those files and agent versions."""
    ch = dbm.open_change(repo["id"])
    if not ch:
        return dbm.latest_change(repo["id"])
    try:
        pr = gh.get_pr(repo["name"], ch["pr_number"])
    except GitHubError as e:
        errors.append(str(e))
        return ch
    if pr["state"] == "merged":
        head = gh.head(repo["name"])
        dbm.set_repo_legacy(repo["id"], legacy_names(repo))          # before the agents' slugs are rewritten below
        dbm.set_repo_files(repo["id"], ch["files"])
        for a in ch["agents"]:
            dbm.upsert_repo_agent(repo["id"], a["agent_id"], a["slug"], a["spec_hash"])
        dbm.update_repo(repo["id"], head_sha=head, synced_at=dbm.now(), state="ready", last_error=None, layout=repo_gen.GENERATOR_VERSION)
        dbm.add_event(cust["id"], "pr_merged", {"pr": ch["pr_number"], "url": ch["pr_url"]}, repo["id"])
        return dbm.update_change(ch["id"], state="merged", merged_at=dbm.now())
    if pr["state"] == "closed":
        dbm.add_event(cust["id"], "pr_closed", {"pr": ch["pr_number"], "url": ch["pr_url"]}, repo["id"])
        return dbm.update_change(ch["id"], state="closed")
    return ch


def diff(gh: GitHubClient, customer_id: str, support_email: str = "") -> dict:
    """What an update would change on main right now, per file, with unified diffs (read-only; nothing is written)."""
    cust, repo = _customer_repo(customer_id)
    entries = _entries(repo)
    plan = _plan(gh, cust, repo, entries, ["preview"], True, support_email)
    out = []
    for path, content in sorted(plan["changes"].items()):
        if path == "CHANGELOG.md":
            continue
        before = gh.blob_text(repo["name"], plan["remote"][path]) if path in plan["remote"] else None
        status = "removed" if content is None else "added" if before is None else "modified"
        text = "".join(difflib.unified_diff((before or "").splitlines(True), (content or "").splitlines(True), f"a/{path}", f"b/{path}", n=2))
        out.append({"path": path, "status": status, "diff": text[:20000], "truncated": len(text) > 20000})
    return {"files": out, "conflicts": plan["conflicts"], "agents_behind": [e["spec"]["agent_name"] for e in behind(entries)],
            "open_change": dbm.open_change(repo["id"])}


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
        agents = [{"agent_id": e["agent_id"], "agent_name": e["spec"]["agent_name"], "plugin_slug": e["slug"],
                   "command": f"/{repo_gen.company_slug(cust['slug'])}:{e['slug']}", "behind": e["agent_id"] in stale} for e in entries]
    change = dbm.latest_change(repo["id"]) if repo else None
    if change:
        change = {k: v for k, v in change.items() if k not in ("files", "agents")}
    return {"customer": cust, "repo": {k: v for k, v in repo.items() if k != "files"} if repo else None, "agents": agents,
            "change": change, "requests": requests, "events": dbm.list_events(customer_id)}


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


def plugin_zip_for(customer_id: str, support_email: str = "", unverified_code_ack: bool = False) -> tuple[bytes, str]:
    """Fallback for customers without GitHub: the current plugin built from the live specs. Returns (zip bytes, plugin name)."""
    cust = dbm.get_customer(customer_id)
    if not cust:
        raise DeliveryError("Customer not found", 404)
    repo = dbm.get_repo_for_customer(customer_id)
    if not repo:
        raise DeliveryError("This customer has no delivered agents yet.", 409)
    entries = _entries(repo)
    if not entries:
        raise DeliveryError("This customer has no delivered agents yet.", 409)
    require_code_ack([(e["agent_id"], e["spec"]) for e in entries], unverified_code_ack)
    files = _repo_files(cust, {"owner": repo["owner"], "name": repo["name"]}, entries, support_email, legacy_names(repo))
    plugin = repo_gen.company_slug(cust["slug"])
    return repo_gen.plugin_zip(files, plugin), plugin