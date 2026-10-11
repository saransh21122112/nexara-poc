import httpx
import pytest

from backend.github_client import BranchMoved, GitHubClient, GitHubError, NotConfigured, RepoExists
from tests.fake_github import FakeGitHub


@pytest.fixture
def gh():
    fake = FakeGitHub(token="ghp_SECRET_TOKEN_123", owner="saransh21122112")
    return fake, fake.client()


def test_not_configured_is_explicit(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_OWNER", raising=False)
    c = GitHubClient()
    assert not c.configured and c.status()["configured"] is False
    with pytest.raises(NotConfigured):
        c.repo_exists("x")


def test_status_ok_and_bad_token(gh):
    fake, c = gh
    s = c.status()
    assert s["ok"] and s["owner"] == "saransh21122112" and s["owner_type"] == "user" and "token" not in str(s).lower().replace("token_user", "")
    bad = GitHubClient(token="nope", owner="saransh21122112", http=c.http)
    s = bad.status()
    assert not s["ok"] and "invalid or expired" in s["issue"]


def test_create_repo_private_and_idempotency_guard(gh):
    fake, c = gh
    r = c.create_repo("nexara-acme", "Acme agents")
    assert r["url"].endswith("/saransh21122112/nexara-acme") and fake.repos["nexara-acme"]["private"] is True
    assert c.repo_exists("nexara-acme") and not c.repo_exists("nexara-other")
    with pytest.raises(RepoExists):
        c.create_repo("nexara-acme", "again")


def test_create_repo_org_path_and_owner_mismatch():
    org = FakeGitHub(token="t", owner="nexara-org", owner_type="Organization", token_user="alice")
    c = org.client()
    c.create_repo("r1", "d")
    assert ("POST", "/orgs/nexara-org/repos") in org.calls
    mismatch = FakeGitHub(token="t", owner="saransh21122112", token_user="someone-else")
    with pytest.raises(GitHubError) as e:
        mismatch.client().create_repo("r1", "d")
    assert e.value.code == "owner_mismatch" and "r1" not in mismatch.repos


def test_refuses_repo_that_is_not_private(gh):
    fake, c = gh
    fake.force_public = True
    with pytest.raises(GitHubError) as e:
        c.create_repo("oops", "d")
    assert e.value.code == "not_private"


def test_commit_add_modify_delete_and_modes(gh):
    fake, c = gh
    c.create_repo("r", "d")
    base = c.head("r")
    c1 = c.commit_files("r", base, {"a.txt": "one", "dir/b.txt": "two", "scripts/x.sh": "#!/bin/sh\n"}, "add", executable=frozenset({"scripts/x.sh"}))
    assert fake.files("r")["a.txt"] == "one" and fake.files("r")["README.md"].startswith("# r")
    assert fake.modes("r")["scripts/x.sh"] == "100755" and fake.modes("r")["a.txt"] == "100644"
    tree = c.tree("r", c1)
    assert set(tree) == {"README.md", "a.txt", "dir/b.txt", "scripts/x.sh"}
    assert c.blob_text("r", tree["dir/b.txt"]) == "two"
    c2 = c.commit_files("r", c1, {"a.txt": "ONE", "dir/b.txt": None}, "edit+delete")
    assert fake.files("r")["a.txt"] == "ONE" and "dir/b.txt" not in fake.files("r") and c.head("r") == c2


@pytest.mark.parametrize("bad", ["../x", "/abs", "a/../b", "a\\b"])
def test_unsafe_paths_refused(gh, bad):
    fake, c = gh
    c.create_repo("r", "d")
    with pytest.raises(GitHubError) as e:
        c.commit_files("r", c.head("r"), {bad: "x"}, "m")
    assert e.value.code == "bad_path"


def test_branch_moved_is_reported_not_overwritten(gh):
    fake, c = gh
    c.create_repo("r", "d")
    stale = c.head("r")
    fake.push_file("r", "customer.txt", "their change")
    with pytest.raises(BranchMoved):
        c.commit_files("r", stale, {"ours.txt": "x"}, "m")
    assert fake.files("r")["customer.txt"] == "their change" and "ours.txt" not in fake.files("r")


def test_invitation_lifecycle(gh):
    fake, c = gh
    c.create_repo("r", "d")
    first = c.add_collaborator("r", "customer1")
    assert first["status"] == "invited" and first["invitation_id"]
    assert c.collaborator_status("r", "customer1") == "not_collaborator"       # pending until accepted
    assert [i["login"] for i in c.invitations("r")] == ["customer1"]
    assert c.add_collaborator("r", "customer1")["invitation_id"] == first["invitation_id"]   # no duplicate invite
    c.cancel_invitation("r", first["invitation_id"])
    assert c.invitations("r") == []
    c.add_collaborator("r", "customer1")
    fake.accept_invite("r", "customer1")
    assert c.collaborator_status("r", "customer1") == "collaborator"
    assert c.add_collaborator("r", "customer1")["status"] == "already_collaborator"


def test_issues_exclude_pull_requests(gh):
    fake, c = gh
    c.create_repo("r", "d")
    fake.add_issue("r", "Agent misses SQL injection")
    fake.add_issue("r", "a PR", pr=True)
    assert [i["title"] for i in c.open_issues("r")] == ["Agent misses SQL injection"]


def test_errors_are_mapped_and_never_leak_the_token(gh):
    fake, c = gh
    c.create_repo("r", "d")
    fake.faults.append(("GET", "/issues", 403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1999999999"}))
    with pytest.raises(GitHubError) as e:
        c.open_issues("r")
    assert "rate limit" in str(e.value)
    fake.faults.append(("GET", "/issues", 403, {}))
    with pytest.raises(GitHubError) as e:
        c.open_issues("r")
    assert "lacks permission" in str(e.value)
    with pytest.raises(GitHubError) as e:
        c.head("missing-repo")
    assert e.value.status == 404

    def boom(request):
        raise httpx.ConnectError("boom", request=request)
    dead = GitHubClient(token="ghp_SECRET_TOKEN_123", owner="saransh21122112", http=httpx.Client(transport=httpx.MockTransport(boom), base_url="https://x"))
    with pytest.raises(GitHubError) as e:
        dead.repo_exists("r")
    assert e.value.code == "unreachable"
    for err in (e.value,):
        assert "SECRET" not in str(err) and "SECRET" not in repr(err)


@pytest.mark.parametrize("name", ["", "a/b", "../x", "a b", "x" * 101, ".."])
def test_repo_name_validation(gh, name):
    fake, c = gh
    with pytest.raises(GitHubError) as e:
        c.repo_exists(name)
    assert e.value.code == "bad_name"
