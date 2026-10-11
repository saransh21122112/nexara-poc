import json
import threading

import pytest

from backend import agent_service, delivery_service, main
from backend import database as dbm
from backend.github_client import GitHubClient
from tests.fake_github import FakeGitHub
from tests.test_api import REQ, SPEC

OWNER = "saransh21122112"


@pytest.fixture
def env(client, monkeypatch):
    fake = FakeGitHub(token="t", owner=OWNER)
    monkeypatch.setattr(main, "get_github", lambda: fake.client())
    cur = {"spec": SPEC}

    async def fake_spec(_):
        return cur["spec"], "anthropic", "m"
    monkeypatch.setattr(agent_service, "generate_spec", fake_spec)

    def mk(company="Acme Corp", gh="acme-dev", email="ops@acme.example", spec=None, agent_type="Data Analyst", verified=True):
        cur["spec"] = spec or SPEC
        body = {**REQ, "company_name": company, "github_username": gh, "contact_email": email, "agent_type": agent_type}
        r = client.post("/api/requests", json=body).json()
        if verified:                                  # most tests are about delivery, not about verification
            dbm.mark_email_verified(r["request_id"], "link")
        a = client.post(f"/api/requests/{r['request_id']}/generate-agent").json()
        return r["request_id"], a["id"], r["request"]["customer_id"]

    class E:
        pass
    e = E()
    e.client, e.fake, e.mk = client, fake, mk
    e.deliver = lambda aid, **kw: client.post(f"/api/agents/{aid}/deliver", json={"confirm": True, **kw})
    return e


def spec2(name="Acme Sales Analyst"):
    return {**SPEC, "agent_name": name, "purpose": "Ranks products by revenue.", "capabilities": ["Rank products"]}


def repo_of(e, cid):
    return dbm.get_repo_for_customer(cid)


def slug_of(spec):
    from backend import repo_gen
    return repo_gen.plugin_slug(spec)


# ------------------------------------------------------------------ preview / confirm gates
def test_preview_changes_nothing_and_shows_the_plan(env):
    _, aid, cid = env.mk()
    env.fake.calls.clear()
    p = env.client.get(f"/api/agents/{aid}/delivery-preview").json()
    assert p["mode"] == "create" and p["repo"]["name"] == "nexara-acme-corp" and p["repo"]["owner"] == OWNER and p["repo"]["private"] is True
    assert p["invite"]["username"] == "acme-dev" and p["invite"]["will_invite"] is True and "write" in p["invite"]["access"]
    assert {"scripts/setup.sh", "START-HERE.md", "CHANGELOG.md", ".claude-plugin/marketplace.json"} <= set(p["files"])
    assert any("not been run or tested" in w for w in p["warnings"])
    assert "Claude account" in p["onboarding_draft"] and "nexara-acme-corp" in p["onboarding_draft"]
    assert all(m == "GET" for m, _ in env.fake.calls) and env.fake.repos == {}     # nothing written on GitHub
    assert repo_of(env, cid) is None


def test_deliver_needs_explicit_confirm(env):
    _, aid, cid = env.mk()
    for body in ({}, {"confirm": False}, {"invite": True}):
        assert env.client.post(f"/api/agents/{aid}/deliver", json=body).status_code == 400
    assert env.fake.repos == {} and repo_of(env, cid) is None


def test_all_new_endpoints_require_login(env):
    _, aid, cid = env.mk()
    env.client.headers.pop("X-Factory-Token")
    for method, path in [("GET", "/api/github/status"), ("GET", f"/api/agents/{aid}/delivery-preview"), ("POST", f"/api/agents/{aid}/deliver"),
                         ("GET", "/api/customers"), ("GET", f"/api/customers/{cid}"), ("POST", f"/api/customers/{cid}/notes"),
                         ("POST", f"/api/customers/{cid}/onboarding"), ("POST", f"/api/customers/{cid}/refresh"),
                         ("POST", f"/api/customers/{cid}/sync"), ("POST", f"/api/customers/{cid}/reinvite"), ("POST", f"/api/customers/{cid}/collaborators")]:
        assert env.client.request(method, path, json={}).status_code == 401, (method, path)


# ------------------------------------------------------------------ deliver
def test_deliver_creates_private_repo_commits_and_invites(env):
    _, aid, cid = env.mk()
    r = env.deliver(aid)
    assert r.status_code == 201, r.text
    d = r.json()
    name = "nexara-acme-corp"
    assert d["repo"]["url"] == f"https://github.com/{OWNER}/{name}" and d["invite"]["status"] == "pending" and d["invite"]["username"] == "acme-dev"
    assert env.fake.repos[name]["private"] is True
    files = env.fake.files(name)
    slug = slug_of(SPEC)
    assert f"plugins/{slug}/agents/{slug}.md" in files and ".claude-plugin/marketplace.json" in files and "scripts/setup.sh" in files
    assert "Initial delivery" in files["CHANGELOG.md"] and files["README.md"].startswith("# Acme Corp")   # replaced the auto-init README
    assert env.fake.modes(name)["scripts/setup.sh"] == "100755" and env.fake.modes(name)["README.md"] == "100644"
    inv = env.fake.repos[name]["invites"]
    assert [i["login"] for i in inv.values()] == ["acme-dev"]
    # nothing sensitive in the repo
    assert not any("ghp_" in c or "t" == c for c in files.values())
    view = env.client.get(f"/api/customers/{cid}").json()
    assert view["repo"]["state"] == "ready" and view["repo"]["invite_status"] == "pending" and view["repo"]["private"] if "private" in view["repo"] else True
    assert [(a["plugin_slug"], a["behind"]) for a in view["agents"]] == [(slug, False)]
    kinds = {e["kind"] for e in view["events"]}
    assert {"repo_created", "agent_added", "invited"} <= kinds


def test_second_agent_for_same_customer_goes_into_the_same_repo(env):
    _, a1, c1 = env.mk()
    env.deliver(a1)
    _, a2, c2 = env.mk(spec=spec2())
    assert c1 == c2                                                       # same company + same GitHub user -> same customer
    p = env.client.get(f"/api/agents/{a2}/delivery-preview").json()
    assert p["mode"] == "add" and p["repo"]["exists"] and p["invite"]["will_invite"] is False
    assert env.deliver(a2).status_code == 409                              # adding to an existing repo needs an explicit acknowledgement
    r = env.deliver(a2, existing_repo_ack=True)
    assert r.status_code == 201 and len(env.fake.repos) == 1
    files = env.fake.files("nexara-acme-corp")
    mk = json.loads(files[".claude-plugin/marketplace.json"])
    assert [p["name"] for p in mk["plugins"]] == [slug_of(SPEC), slug_of(spec2())]
    assert slug_of(spec2()) in files["README.md"] and files["CHANGELOG.md"].count("## ") == 2
    assert files["CHANGELOG.md"].index("Acme Sales Analyst") < files["CHANGELOG.md"].index("Initial delivery")
    assert len(env.fake.repos["nexara-acme-corp"]["invites"]) == 1        # no second invitation


def test_same_agent_name_twice_gets_a_unique_plugin_slug(env):
    _, a1, _ = env.mk()
    env.deliver(a1)
    _, a2, _ = env.mk()                                                   # identical spec/name
    d = env.deliver(a2, existing_repo_ack=True).json()
    assert d["plugin_slug"] == slug_of(SPEC) + "-2"
    mk = json.loads(env.fake.files("nexara-acme-corp")[".claude-plugin/marketplace.json"])
    assert len({p["name"] for p in mk["plugins"]}) == 2


def test_customers_with_similar_names_never_share_a_repo(env):
    _, a1, c1 = env.mk(company="Acme Corp", gh="alice", email="a@one.example")
    _, a2, c2 = env.mk(company="ACME corp!", gh="bob", email="b@two.example")
    assert c1 != c2
    env.deliver(a1)
    env.deliver(a2)
    assert set(env.fake.repos) == {"nexara-acme-corp", "nexara-acme-corp-2"}
    assert all(slug_of(SPEC) in f for f in [env.fake.files("nexara-acme-corp")["README.md"], env.fake.files("nexara-acme-corp-2")["README.md"]])
    assert [i["login"] for i in env.fake.repos["nexara-acme-corp"]["invites"].values()] == ["alice"]
    assert [i["login"] for i in env.fake.repos["nexara-acme-corp-2"]["invites"].values()] == ["bob"]
    # same company + same email but a different GitHub user is NOT merged (the form is public: email alone proves nothing)
    _, _, c3 = env.mk(company="Acme Corp", gh="carol", email="A@one.example")
    assert c3 not in (c1, c2)


def test_repo_name_collision_picks_a_free_name(env):
    env.fake.client().create_repo("nexara-acme-corp", "someone else's")
    _, aid, _ = env.mk()
    assert env.client.get(f"/api/agents/{aid}/delivery-preview").json()["repo"]["name"] == "nexara-acme-corp-2"
    assert env.deliver(aid).json()["repo"]["name"] == "nexara-acme-corp-2"


def test_invite_failure_does_not_fail_the_delivery(env):
    _, aid, cid = env.mk(gh="no-such-user")
    env.fake.faults.append(("PUT", "/collaborators/", 422, {}))
    r = env.deliver(aid)
    assert r.status_code == 201 and r.json()["invite"]["status"] == "failed"
    repo = repo_of(env, cid)
    assert repo["state"] == "ready" and "invite failed" in repo["last_error"] and repo["invite_status"] == "none"
    assert "invite_failed" in {e["kind"] for e in dbm.list_events(cid)}


def test_legacy_request_without_github_username_still_delivers(env):
    rid = dbm.create_request({"company_name": "Legacy Ltd", "industry": "x", "agent_type": "Sales", "business_problem": "long enough text",
                              "tasks": "t", "existing_tools": "t", "required_output": "o", "contact_email": "old@legacy.example"})["id"]
    dbm.attach_customer(rid, "")           # simulates a request that predates customers
    dbm.mark_email_verified(rid, "staff", "legacy test request")
    aid = dbm.create_agent(rid, SPEC, "anthropic", "m")["id"]
    p = env.client.get(f"/api/agents/{aid}/delivery-preview").json()
    assert p["invite"]["will_invite"] is False and any("No GitHub username" in w for w in p["warnings"])
    d = env.deliver(aid).json()
    assert d["invite"] is None and d["repo"]["name"] == "nexara-legacy-ltd" and env.fake.repos["nexara-legacy-ltd"]["invites"] == {}


def test_repo_that_is_not_private_is_refused_and_not_recorded(env):
    _, aid, cid = env.mk()
    env.fake.force_public = True
    r = env.deliver(aid)
    assert r.status_code == 502 and r.json()["code"] == "not_private"
    assert repo_of(env, cid) is None


def test_github_not_configured_is_a_clear_503(env, monkeypatch):
    monkeypatch.setattr(main, "get_github", lambda: GitHubClient(token="", owner=""))
    _, aid, cid = env.mk()
    for resp in (env.client.get(f"/api/agents/{aid}/delivery-preview"), env.deliver(aid)):
        assert resp.status_code == 503 and resp.json()["code"] == "not_configured" and "GITHUB_TOKEN" in resp.json()["detail"]
    assert repo_of(env, cid) is None
    assert env.client.get("/api/github/status").json()["configured"] is False
    assert env.client.get("/api/health").json()["github_configured"] is False


def test_partial_failure_leaves_needs_retry_and_retry_does_not_duplicate(env):
    _, aid, cid = env.mk()
    env.fake.faults.append(("POST", "/git/trees", 500, {}))
    r = env.deliver(aid)
    assert r.status_code == 502
    repo = repo_of(env, cid)
    assert repo["state"] == "needs_retry" and repo["last_error"] and len(env.fake.repos) == 1
    r2 = env.deliver(aid)
    assert r2.status_code == 201
    assert len(env.fake.repos) == 1 and repo_of(env, cid)["state"] == "ready"
    assert "deliver_failed" in {e["kind"] for e in dbm.list_events(cid)}
    assert "scripts/setup.sh" in env.fake.files("nexara-acme-corp")


def test_concurrent_delivers_create_one_repo(env):
    _, aid, cid = env.mk()
    out = []

    def go():
        try:
            out.append(delivery_service.deliver(env.fake.client(), aid))
        except Exception as e:      # noqa: BLE001
            out.append(e)
    ts = [threading.Thread(target=go) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(env.fake.repos) == 1 and all(isinstance(o, dict) for o in out), out
    assert sorted(o["status"] for o in out).count("pushed") == 1


# ------------------------------------------------------------------ sync / conflicts
def edit_agent(env, aid, **changes):
    spec = {**env.client.get(f"/api/agents/{aid}").json()["spec"], **changes}
    assert env.client.put(f"/api/agents/{aid}", json=spec).status_code == 200


def test_sync_pushes_updates_with_changelog_and_marks_agent_current(env):
    _, aid, cid = env.mk()
    env.deliver(aid)
    slug = slug_of(SPEC)
    head1 = env.fake.repos["nexara-acme-corp"]["branches"]["main"]
    assert env.client.post(f"/api/customers/{cid}/sync", json={"confirm": True}).json()["status"] == "up_to_date"
    assert env.fake.repos["nexara-acme-corp"]["branches"]["main"] == head1               # nothing to push, no empty commit
    edit_agent(env, aid, purpose="Reviews code and flags secrets too.")
    assert env.client.get(f"/api/customers/{cid}").json()["agents"][0]["behind"] is True
    assert env.client.post(f"/api/customers/{cid}/sync", json={}).status_code == 400
    r = env.client.post(f"/api/customers/{cid}/sync", json={"confirm": True})
    assert r.status_code == 200 and r.json()["status"] == "pushed" and f"plugins/{slug}/agents/{slug}.md" in r.json()["changed"]
    files = env.fake.files("nexara-acme-corp")
    assert "flags secrets" in files[f"plugins/{slug}/agents/{slug}.md"] and "Updated agent" in files["CHANGELOG.md"]
    assert files["CHANGELOG.md"].count("## ") == 2
    assert env.client.get(f"/api/customers/{cid}").json()["agents"][0]["behind"] is False
    assert env.client.post(f"/api/customers/{cid}/sync", json={"confirm": True}).json()["status"] == "up_to_date"


def test_hand_edits_are_never_silently_overwritten(env):
    _, aid, cid = env.mk()
    env.deliver(aid)
    name, slug = "nexara-acme-corp", slug_of(SPEC)
    agent_md = f"plugins/{slug}/agents/{slug}.md"
    env.fake.push_file(name, agent_md, "customer rewrote this agent")
    env.fake.push_file(name, "custom/notes.md", "my own notes")
    env.fake.push_file(name, "START-HERE.md", "edited start page")
    edit_agent(env, aid, purpose="A brand new purpose.")
    r = env.client.post(f"/api/customers/{cid}/sync", json={"confirm": True})
    assert r.status_code == 409 and r.json()["code"] == "conflict"
    assert {c["path"] for c in r.json()["conflicts"]} == {agent_md, "START-HERE.md"}   # custom/ is never a conflict
    assert env.fake.files(name)[agent_md] == "customer rewrote this agent"            # nothing was pushed
    assert "sync_conflict" in {e["kind"] for e in dbm.list_events(cid)}
    r = env.client.post(f"/api/customers/{cid}/sync", json={"confirm": True, "overwrite_conflicts": True})
    assert r.status_code == 200 and r.json()["status"] == "pushed"
    files = env.fake.files(name)
    assert "A brand new purpose" in files[agent_md] and files["START-HERE.md"].startswith("# Start here")
    assert files["custom/notes.md"] == "my own notes"                                 # customer area untouched even on overwrite


def test_deleted_managed_file_is_a_conflict_not_silently_restored(env):
    _, aid, cid = env.mk()
    env.deliver(aid)
    env.fake.push_file("nexara-acme-corp", "scripts/doctor.sh", None)
    edit_agent(env, aid, purpose="Changed purpose.")
    r = env.client.post(f"/api/customers/{cid}/sync", json={"confirm": True})
    assert r.status_code == 409 and r.json()["conflicts"] == [{"path": "scripts/doctor.sh", "why": "deleted from the repository since the last sync"}]
    assert "scripts/doctor.sh" not in env.fake.files("nexara-acme-corp")
    assert env.client.post(f"/api/customers/{cid}/sync", json={"confirm": True, "overwrite_conflicts": True}).status_code == 200
    assert "scripts/doctor.sh" in env.fake.files("nexara-acme-corp")


def test_changelog_edits_do_not_conflict_and_survive(env):
    _, aid, cid = env.mk()
    env.deliver(aid)
    name = "nexara-acme-corp"
    env.fake.push_file(name, "CHANGELOG.md", env.fake.files(name)["CHANGELOG.md"] + "\n- customer note\n")
    edit_agent(env, aid, purpose="Changed purpose.")
    assert env.client.post(f"/api/customers/{cid}/sync", json={"confirm": True}).status_code == 200
    log = env.fake.files(name)["CHANGELOG.md"]
    assert "customer note" in log and "Updated agent" in log


def test_sync_without_repo_is_a_clear_409(env):
    _, aid, cid = env.mk()
    r = env.client.post(f"/api/customers/{cid}/sync", json={"confirm": True})
    assert r.status_code == 409 and "no repository" in r.json()["detail"]
    assert env.client.post("/api/customers/CUS-NOPE/sync", json={"confirm": True}).status_code == 404


def test_push_race_is_reported_and_loses_nothing(env):
    _, aid, cid = env.mk()
    env.deliver(aid)
    edit_agent(env, aid, purpose="Changed purpose.")
    real = env.fake.client().commit_files

    def raced(self, name, base, *a, **k):
        env.fake.push_file(name, "custom/late.md", "someone pushed in between")
        return real(name, base, *a, **k)
    GitHubClient.commit_files, orig = raced, GitHubClient.commit_files
    try:
        r = env.client.post(f"/api/customers/{cid}/sync", json={"confirm": True})
    finally:
        GitHubClient.commit_files = orig
    assert r.status_code == 409 and r.json()["code"] == "branch_moved"
    assert env.fake.files("nexara-acme-corp")["custom/late.md"] == "someone pushed in between"
    assert env.client.post(f"/api/customers/{cid}/sync", json={"confirm": True}).status_code == 200   # retry works


# ------------------------------------------------------------------ refresh / invites / support
def test_refresh_tracks_invite_acceptance_issues_and_manual_changes(env):
    _, aid, cid = env.mk()
    env.deliver(aid)
    name, slug = "nexara-acme-corp", slug_of(SPEC)
    r = env.client.post(f"/api/customers/{cid}/refresh").json()
    assert r["invite_status"] == "pending" and r["issues"] == [] and r["manual_changes"] == [] and r["errors"] == []
    env.fake.accept_invite(name, "acme-dev")
    env.fake.add_issue(name, "Agent missed a bug")
    env.fake.add_issue(name, "a pull request", pr=True)
    env.fake.push_file(name, "SUPPORT.md", "changed")
    r = env.client.post(f"/api/customers/{cid}/refresh").json()
    assert r["invite_status"] == "accepted" and [i["title"] for i in r["issues"]] == ["Agent missed a bug"] and r["manual_changes"] == ["SUPPORT.md"]
    env.client.post(f"/api/customers/{cid}/refresh")
    assert [e["kind"] for e in dbm.list_events(cid)].count("invite_accepted") == 1       # transition recorded once
    assert env.client.get(f"/api/customers/{cid}").json()["repo"]["invite_status"] == "accepted"


def test_refresh_survives_github_errors(env):
    _, aid, cid = env.mk()
    env.deliver(aid)
    env.fake.repos.pop("nexara-acme-corp")                  # repo vanished on GitHub
    r = env.client.post(f"/api/customers/{cid}/refresh")
    assert r.status_code == 200 and r.json()["errors"] and r.json()["invite_status"] == "pending"


def test_expired_invitation_is_detected_and_reinvite_works(env):
    _, aid, cid = env.mk()
    env.deliver(aid)
    name = "nexara-acme-corp"
    env.fake.repos[name]["invites"].clear()                 # invitation expired / was cancelled
    assert env.client.post(f"/api/customers/{cid}/refresh").json()["invite_status"] == "expired"
    assert env.client.post(f"/api/customers/{cid}/reinvite", json={}).status_code == 400
    r = env.client.post(f"/api/customers/{cid}/reinvite", json={"confirm": True})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    assert [i["login"] for i in env.fake.repos[name]["invites"].values()] == ["acme-dev"]
    env.fake.accept_invite(name, "acme-dev")
    assert env.client.post(f"/api/customers/{cid}/reinvite", json={"confirm": True}).json()["status"] == "accepted"


def test_extra_collaborator_needs_confirm_and_valid_username(env):
    _, aid, cid = env.mk()
    env.deliver(aid)
    assert env.client.post(f"/api/customers/{cid}/collaborators", json={"username": "teammate"}).status_code == 400
    assert env.client.post(f"/api/customers/{cid}/collaborators", json={"username": "bad name", "confirm": True}).status_code == 422
    r = env.client.post(f"/api/customers/{cid}/collaborators", json={"username": "@teammate", "confirm": True})
    assert r.status_code == 201 and {i["login"] for i in env.fake.repos["nexara-acme-corp"]["invites"].values()} == {"acme-dev", "teammate"}


def test_notes_onboarding_and_customer_list(env):
    _, aid, cid = env.mk()
    env.deliver(aid)
    assert env.client.post(f"/api/customers/{cid}/notes", json={"text": "Asked for Slack integration"}).status_code == 201
    assert env.client.post(f"/api/customers/{cid}/notes", json={"text": ""}).status_code == 422
    assert env.client.post("/api/customers/CUS-NOPE/notes", json={"text": "x"}).status_code == 404
    assert env.client.post(f"/api/customers/{cid}/onboarding", json={"step": "cloned"}).json()["onboarding"] == {"cloned": True}
    assert env.client.post(f"/api/customers/{cid}/onboarding", json={"step": "bogus"}).status_code == 422
    view = env.client.get(f"/api/customers/{cid}").json()
    assert any(e["kind"] == "note" and e["detail"] == "Asked for Slack integration" for e in view["events"])
    row = env.client.get("/api/customers").json()[0]
    assert row["company_name"] == "Acme Corp" and row["delivered_agents"] == 1 and row["requests"] == 1 and row["repo"]["name"] == "nexara-acme-corp"
    assert "files" not in view["repo"]


def test_stats_and_requests_reflect_delivery(env):
    _, aid, cid = env.mk()
    s = env.client.get("/api/stats").json()
    assert (s["customers"], s["agents_delivered"]) == (1, 0)
    env.deliver(aid)
    s = env.client.get("/api/stats").json()
    assert s["agents_delivered"] == 1 and any(a["type"] == "delivery" for a in s["recent_activity"])
    assert env.client.get("/api/requests").json()[0]["customer_id"] == cid


def test_old_delivery_endpoints_are_gone(env):
    _, aid, _ = env.mk()
    for method, path in [("POST", f"/api/agents/{aid}/dispatch"), ("GET", f"/api/agents/{aid}/export"), ("POST", "/v1/run"),
                         ("GET", "/v1/agent"), ("GET", "/api/dispatches/DSP-X"), ("POST", "/api/keys/KEY-X/revoke")]:
        assert env.client.request(method, path, json={}).status_code in (404, 405), path


def test_init_db_drops_the_retired_tables():
    with dbm.db() as c:
        c.execute("CREATE TABLE dispatches (id TEXT)")
        c.execute("CREATE TABLE api_keys (id TEXT)")
    dbm.init_db()
    for t in ("dispatches", "api_keys"):
        with pytest.raises(Exception):
            with dbm.db() as c:
                c.execute(f"SELECT 1 FROM {t}")


def test_startup_backfill_links_old_requests_to_customers(env):
    old = {"company_name": "Old Co", "industry": "x", "agent_type": "Sales", "business_problem": "long enough text", "tasks": "t",
           "existing_tools": "t", "required_output": "o", "contact_email": "o@old.example"}
    r1, r2 = dbm.create_request(old)["id"], dbm.create_request({**old, "contact_email": "o@old.example"})["id"]   # same person
    dbm.attach_customer(r1, ""), dbm.attach_customer(r2, "")      # simulate rows from before customers existed
    assert dbm.backfill_customers() == 2 and dbm.backfill_customers() == 0       # idempotent
    custs = [c for c in env.client.get("/api/customers").json() if c["company_name"] == "Old Co"]
    assert len(custs) == 1 and custs[0]["requests"] == 2                          # both requests share one customer


# ------------------------------------------------------------------ cross-tenant protection (public, unauthenticated form)
def test_spoofed_request_cannot_join_another_customers_repo(env):
    _, a1, c1 = env.mk(company="Acme Corp", gh="victim-dev", email="ceo@acme.example")
    env.deliver(a1)
    # an attacker who knows the company name and contact email submits with their own GitHub account
    _, a2, c2 = env.mk(company="Acme Corp", gh="attacker", email="ceo@acme.example", spec=spec2("Evil Agent"))
    assert c2 != c1
    p = env.client.get(f"/api/agents/{a2}/delivery-preview").json()
    assert p["mode"] == "create" and p["repo"]["name"] == "nexara-acme-corp-2"
    env.deliver(a2)
    victim_invites = [i["login"] for i in env.fake.repos["nexara-acme-corp"]["invites"].values()]
    assert victim_invites == ["victim-dev"]                                              # attacker never invited to the victim's repo
    mk = json.loads(env.fake.files("nexara-acme-corp")[".claude-plugin/marketplace.json"])
    assert [x["name"] for x in mk["plugins"]] == [slug_of(SPEC)]                          # no attacker plugin in the victim's repo
    assert [i["login"] for i in env.fake.repos["nexara-acme-corp-2"]["invites"].values()] == ["attacker"]
    # neither attribute alone is enough to merge
    _, _, c3 = env.mk(company="Acme Corp", gh="victim-dev", email="other@x.example")
    _, _, c4 = env.mk(company="Acme Corp", gh="someone-else", email="ceo@acme.example")
    assert len({c1, c2, c3, c4}) == 4


def test_manual_attach_blocks_unverified_invites_and_needs_acknowledgement(env):
    _, a1, c1 = env.mk(company="Acme Corp", gh="victim-dev", email="ceo@acme.example")
    env.deliver(a1)
    rid2, a2, c2 = env.mk(company="Acme Corp", gh="colleague", email="eng@acme.example", spec=spec2())
    assert c2 != c1
    assert env.client.post(f"/api/requests/{rid2}/attach-customer", json={"customer_id": c1}).status_code == 400          # needs confirm
    assert env.client.post(f"/api/requests/{rid2}/attach-customer", json={"customer_id": "CUS-NOPE", "confirm": True}).status_code == 404
    assert env.client.post("/api/requests/REQ-NOPE/attach-customer", json={"customer_id": c1, "confirm": True}).status_code == 404
    assert env.client.post(f"/api/requests/{rid2}/attach-customer", json={"customer_id": c1, "confirm": True}).status_code == 200
    p = env.client.get(f"/api/agents/{a2}/delivery-preview").json()
    assert p["mode"] == "add" and p["invite"]["will_invite"] is False and "@colleague" in p["invite"]["blocked_reason"]
    assert p["request"]["contact_email"] == "eng@acme.example" and p["request"]["github_username"] == "colleague"
    assert any("EXISTING" in w for w in p["warnings"]) and any("contact email" in w for w in p["warnings"])
    before = env.fake.files("nexara-acme-corp")
    r = env.deliver(a2, invite=True, existing_repo_ack=True)                                # unverified username -> blocked BEFORE any change
    assert r.status_code == 409 and "Nothing was created" in r.json()["detail"] and env.fake.files("nexara-acme-corp") == before
    r = env.deliver(a2, invite=False)                                                       # no acknowledgement -> blocked
    assert r.status_code == 409 and "existing customer repository" in r.json()["detail"] and env.fake.files("nexara-acme-corp") == before
    r = env.deliver(a2, invite=False, existing_repo_ack=True)                               # explicit, without inviting anyone
    assert r.status_code == 201 and r.json()["invite"] is None
    assert [i["login"] for i in env.fake.repos["nexara-acme-corp"]["invites"].values()] == ["victim-dev"]
    assert env.client.post(f"/api/customers/{c1}/collaborators", json={"username": "colleague", "confirm": True}).status_code == 201   # the explicit route
    assert "request_attached" in {e["kind"] for e in dbm.list_events(c1)}


def test_no_established_github_user_never_auto_invites_into_an_existing_repo(env):
    rid = dbm.create_request({"company_name": "Legacy Ltd", "industry": "x", "agent_type": "Sales", "business_problem": "long enough text",
                              "tasks": "t", "existing_tools": "t", "required_output": "o", "contact_email": "old@legacy.example"})["id"]
    dbm.mark_email_verified(rid, "staff", "legacy test request")
    a1 = dbm.create_agent(rid, SPEC, "anthropic", "m")["id"]
    env.deliver(a1)                                                                          # repo exists, no GitHub user was ever established
    cust = dbm.get_repo_for_customer(delivery_service_customer(rid))["customer_id"]
    # a later request from an unverified person is attached by staff
    rid2, a2, c2 = env.mk(company="Legacy Ltd", gh="newperson", email="new@legacy.example", spec=spec2())
    env.client.post(f"/api/requests/{rid2}/attach-customer", json={"customer_id": cust, "confirm": True})
    p = env.client.get(f"/api/agents/{a2}/delivery-preview").json()
    assert p["invite"]["will_invite"] is False and "add a collaborator explicitly" in p["invite"]["blocked_reason"]
    assert env.deliver(a2, invite=True, existing_repo_ack=True).status_code == 409


def delivery_service_customer(request_id):
    return dbm.get_request(request_id)["customer_id"] or delivery_service.ensure_customer(dbm.get_request(request_id))["id"]
