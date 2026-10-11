"""Changes to delivered work go to the customer repo's dev branch and a pull request; main only changes when a person merges."""
import pytest

from backend import agent_service, providers
from backend import database as dbm
from backend.github_client import GitHubClient
from tests.test_api import SPEC
from tests.test_delivery import env  # noqa: F401  (fixture)

NAME = "nexara-acme-corp"


def edit(env, aid, **kw):
    a = env.client.get(f"/api/agents/{aid}").json()
    assert env.client.put(f"/api/agents/{aid}", json={**a["spec"], **kw}).status_code == 200


def propose(env, cid, **body):
    return env.sync_raw(cid, {"confirm": True, **body})


def delivered(env):
    _, aid, cid = env.mk()
    assert env.deliver_raw(aid).json()["status"] == "pushed"          # the first delivery creates main directly
    return aid, cid


def test_first_delivery_goes_to_main_with_no_branch_or_pr(env):
    aid, cid = delivered(env)
    r = env.fake.repos[NAME]
    assert "dev" not in r["branches"] and r["pulls"] == {} and "skills" in " ".join(env.fake.files(NAME))


def test_a_change_lands_on_dev_with_one_nexara_commit_and_an_open_pr_and_main_is_untouched(env):
    aid, cid = delivered(env)
    main_before, files_before = env.fake.repos[NAME]["branches"]["main"], env.fake.files(NAME)
    edit(env, aid, purpose="A new purpose for the agent.")
    r = propose(env, cid)
    j = r.json()
    assert r.status_code == 200 and j["status"] == "proposed" and j["pull_request"]["state"] == "open"
    repo = env.fake.repos[NAME]
    assert repo["branches"]["main"] == main_before and env.fake.files(NAME) == files_before            # main untouched
    commits = env.fake.client().commits_ahead(NAME, "main", "dev")
    assert len(commits) == 1 and commits[0]["message"].startswith("Nexara:")
    assert "A new purpose" in "".join(env.fake.files(NAME, "dev").values())
    pr = repo["pulls"][j["pull_request"]["number"]]
    assert pr["head"] == "dev" and pr["base"] == "main" and "Nothing is merged automatically" in pr["body"]
    assert dbm.get_repo_agent(dbm.get_repo_for_customer(cid)["id"], aid)["spec_hash"] != ""         # still describes main until merged


def test_a_second_proposal_updates_the_same_pr_and_dev(env):
    aid, cid = delivered(env)
    edit(env, aid, purpose="First change.")
    n1 = propose(env, cid).json()["pull_request"]["number"]
    edit(env, aid, purpose="Second change.")
    j = propose(env, cid).json()
    assert j["pull_request"]["number"] == n1 and len(env.fake.repos[NAME]["pulls"]) == 1
    assert len(env.fake.client().commits_ahead(NAME, "main", "dev")) == 1
    assert "Second change" in "".join(env.fake.files(NAME, "dev").values()) and "First change" not in "".join(env.fake.files(NAME, "dev").values())
    kinds = [e["kind"] for e in dbm.list_events(cid)]
    assert kinds.count("pr_opened") == 1 and kinds.count("pr_updated") == 1


def test_main_is_unchanged_until_a_person_merges_then_refresh_records_it(env):
    aid, cid = delivered(env)
    edit(env, aid, purpose="Merged purpose.")
    view = env.client.get(f"/api/customers/{cid}").json()
    assert view["agents"][0]["behind"] is True
    propose(env, cid)
    view = env.client.get(f"/api/customers/{cid}").json()
    assert view["agents"][0]["behind"] is True and view["change"]["state"] == "open"                  # a PR does not make it current
    env.merge_open_pr(cid)
    view = env.client.get(f"/api/customers/{cid}").json()
    assert view["agents"][0]["behind"] is False and view["change"]["state"] == "merged"
    assert "Merged purpose" in "".join(env.fake.files(NAME).values())
    assert "pr_merged" in [e["kind"] for e in dbm.list_events(cid)]
    assert propose(env, cid).json()["status"] == "up_to_date"


def test_closed_without_merging_keeps_the_agent_behind(env):
    aid, cid = delivered(env)
    edit(env, aid, purpose="Rejected purpose.")
    n = propose(env, cid).json()["pull_request"]["number"]
    env.fake.close_pr(NAME, n)
    env.client.post(f"/api/customers/{cid}/refresh")
    view = env.client.get(f"/api/customers/{cid}").json()
    assert view["change"]["state"] == "closed" and view["agents"][0]["behind"] is True
    assert "Rejected purpose" not in "".join(env.fake.files(NAME).values())
    assert propose(env, cid).json()["status"] == "proposed"                                           # can propose again; new PR


def test_dev_with_someone_elses_commit_is_refused_until_overwrite_dev(env):
    aid, cid = delivered(env)
    edit(env, aid, purpose="Change one.")
    propose(env, cid)
    env.fake.commit_on(NAME, "dev", "notes.md", "a colleague's work", "wip: colleague experiment")
    edit(env, aid, purpose="Change two.")
    r = propose(env, cid)
    assert r.status_code == 409 and "dev" in r.json()["detail"] and "colleague" in r.json()["detail"]
    assert "notes.md" in env.fake.files(NAME, "dev")                                                  # nothing was thrown away
    r = propose(env, cid, overwrite_dev=True)
    assert r.status_code == 200 and "notes.md" not in env.fake.files(NAME, "dev")


def test_hand_edit_on_main_is_still_a_conflict_and_custom_is_untouched(env):
    aid, cid = delivered(env)
    env.fake.push_file(NAME, "README.md", "edited by the customer")
    env.fake.push_file(NAME, "custom/mine.md", "my own notes")
    edit(env, aid, purpose="Another purpose.")
    r = propose(env, cid)
    assert r.status_code == 409 and r.json()["code"] == "conflict" and "dev" not in env.fake.repos[NAME]["branches"]
    assert propose(env, cid, overwrite_conflicts=True).status_code == 200
    assert env.fake.files(NAME, "dev")["custom/mine.md"] == "my own notes"


def test_a_pr_permission_error_is_explained(env):
    aid, cid = delivered(env)
    edit(env, aid, purpose="Needs a PR.")
    env.fake.faults.append(("POST", "/pulls", 403, {}))
    r = propose(env, cid)
    assert r.status_code >= 400 and "Pull requests" in r.json()["detail"]


def test_adding_another_agent_to_an_existing_repo_also_goes_through_a_pr(env):
    aid, cid = delivered(env)
    _, aid2, cid2 = env.mk(spec=SPEC | {"agent_name": "Second Analyst"})
    assert cid2 == cid
    r = env.deliver_raw(aid2, existing_repo_ack=True)
    assert r.status_code == 201 and r.json()["status"] == "proposed" and r.json()["pull_request"]["number"] == 1
    assert "second-analyst" not in " ".join(env.fake.files(NAME))
    assert "second-analyst" in " ".join(env.fake.files(NAME, "dev"))


def test_diff_shows_what_would_change_per_file(env):
    aid, cid = delivered(env)
    edit(env, aid, purpose="Diff me please.")
    d = env.client.get(f"/api/customers/{cid}/diff").json()
    changed = {f["path"]: f for f in d["files"]}
    assert d["agents_behind"] and any(f["status"] == "modified" and "+" in f["diff"] and "Diff me please" in f["diff"] for f in changed.values())
    assert "CHANGELOG.md" not in changed
    assert env.fake.repos[NAME]["branches"]["main"] == dbm.get_repo_for_customer(cid)["head_sha"]       # read-only


def test_changes_endpoints_need_auth_confirm_and_a_known_customer(env):
    aid, cid = delivered(env)
    assert env.client.post(f"/api/customers/{cid}/sync", json={}).status_code == 400
    assert env.client.get("/api/customers/CUS-NOPE/diff").status_code == 404
    env.client.headers.pop("X-Factory-Token")
    assert env.client.get(f"/api/customers/{cid}/diff").status_code == 401
    assert env.client.post(f"/api/agents/{aid}/revise", json={"instruction": "do more"}).status_code == 401


def test_two_customers_never_touch_each_others_repos(env):
    a1, c1 = delivered(env)
    _, a2, c2 = env.mk(company="Other Co", gh="other-dev", email="x@other.example")
    env.deliver_raw(a2)
    edit(env, a1, purpose="Only for Acme.")
    propose(env, c1)
    assert "dev" in env.fake.repos[NAME]["branches"] and "dev" not in env.fake.repos["nexara-other-co"]["branches"]


# ------------------------------------------------------------------ revise (AI proposes a changed spec; nothing is saved)
def _llm(monkeypatch, replies):
    from backend import subscription
    seen = []

    async def fake(system, prompt):
        seen.append(prompt)
        return replies.pop(0)
    monkeypatch.setattr(subscription, "complete_json", fake)
    monkeypatch.setattr(providers, "missing", lambda p: None)
    monkeypatch.setattr(providers, "get_provider", lambda: "claude_subscription")
    return seen


def test_revise_returns_a_proposed_spec_and_saves_nothing(env, monkeypatch):
    aid, cid = delivered(env)
    new = {**SPEC, "purpose": "Now also checks refund policy.", "capabilities": SPEC["capabilities"] + ["Check refund policy"]}
    seen = _llm(monkeypatch, [__import__("json").dumps(new)])
    r = env.client.post(f"/api/agents/{aid}/revise", json={"instruction": "also check the refund policy"})
    j = r.json()
    assert r.status_code == 200 and j["proposed"]["purpose"].startswith("Now also") and j["current"]["purpose"] == SPEC["purpose"]
    assert env.client.get(f"/api/agents/{aid}").json()["spec"]["purpose"] == SPEC["purpose"]           # not saved
    assert "also check the refund policy" in seen[0] and SPEC["agent_name"] in seen[0]


def test_revise_retries_once_then_reports_a_bad_answer(env, monkeypatch):
    aid, cid = delivered(env)
    _llm(monkeypatch, ["nope", __import__("json").dumps({**SPEC, "purpose": "Fixed on retry."})])
    assert env.client.post(f"/api/agents/{aid}/revise", json={"instruction": "change it"}).json()["proposed"]["purpose"] == "Fixed on retry."
    _llm(monkeypatch, ["nope", "still nope"])
    r = env.client.post(f"/api/agents/{aid}/revise", json={"instruction": "change it"})
    assert r.status_code == 502


def test_revise_input_and_provider_errors(env, monkeypatch):
    aid, cid = delivered(env)
    assert env.client.post(f"/api/agents/{aid}/revise", json={"instruction": "x"}).status_code == 422
    assert env.client.post("/api/agents/AGT-NOPE/revise", json={"instruction": "change it"}).status_code == 404
    assert env.client.post(f"/api/agents/{aid}/revise", json={"instruction": "change it"}).status_code == 503
