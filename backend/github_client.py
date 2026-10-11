"""Minimal GitHub REST client for customer-repo delivery (no git binary needed). Config: GITHUB_TOKEN, GITHUB_OWNER
(user or org login, type auto-detected), GITHUB_API_URL (tests / local fake). The token is only ever sent in the
Authorization header and never appears in errors or logs."""
import base64
import os
import re

import httpx

API = "https://api.github.com"
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None, code: str = "github_error"):
        super().__init__(message)
        self.status, self.code = status, code


class NotConfigured(GitHubError):
    def __init__(self, message: str = "GitHub is not configured: set GITHUB_TOKEN and GITHUB_OWNER."):
        super().__init__(message, None, "not_configured")


class RepoExists(GitHubError):
    def __init__(self, name: str):
        super().__init__(f"A repository named '{name}' already exists under this owner.", 422, "repo_exists")


class BranchMoved(GitHubError):
    def __init__(self):
        super().__init__("The repository changed while we were pushing (branch moved). Refresh and try again.", 422, "branch_moved")


def _explain(r: httpx.Response) -> str:
    try:
        msg = r.json().get("message") or ""
    except Exception:
        msg = ""
    if r.status_code == 401:
        return "GitHub rejected the token (GITHUB_TOKEN is invalid or expired)."
    if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
        return f"GitHub rate limit reached; retry after {r.headers.get('x-ratelimit-reset', 'a few minutes')} (epoch seconds)."
    if r.status_code == 403:
        return f"GitHub refused: the token lacks permission for this action ({msg or 'forbidden'})."
    if r.status_code == 404:
        return f"GitHub: not found, or the token cannot see it ({msg or 'not found'})."
    return f"GitHub error {r.status_code}: {msg or r.text[:200]}"


class GitHubClient:
    def __init__(self, token: str | None = None, owner: str | None = None, api_url: str | None = None,
                 http: httpx.Client | None = None):
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        self.owner = owner if owner is not None else os.environ.get("GITHUB_OWNER", "")
        self.api_url = (api_url or os.environ.get("GITHUB_API_URL") or API).rstrip("/")
        self.http = http or httpx.Client(base_url=self.api_url, timeout=30)
        self._owner_type: str | None = None
        self._token_user: str | None = None

    # ---------------------------------------------------------------- plumbing
    @property
    def configured(self) -> bool:
        return bool(self.token and self.owner)

    def _require(self):
        if not self.configured:
            raise NotConfigured()

    def _req(self, method: str, path: str, ok: tuple = (200, 201, 204), **kw) -> httpx.Response:
        self._require()
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "nexara-factory"}
        try:
            r = self.http.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as e:  # never include request details (they carry the auth header)
            raise GitHubError(f"Could not reach GitHub ({type(e).__name__}).", None, "unreachable") from None
        if r.status_code not in ok:
            raise GitHubError(_explain(r), r.status_code)
        return r

    def _repo(self, name: str) -> str:
        if not REPO_RE.match(name) or name in (".", ".."):
            raise GitHubError(f"Invalid repository name '{name}'.", None, "bad_name")
        return f"/repos/{self.owner}/{name}"

    # ---------------------------------------------------------------- account / repo
    def status(self) -> dict:
        """Cheap health check for the dashboard banner (no secrets in the result)."""
        if not self.configured:
            return {"configured": False, "owner": self.owner or None, "ok": False, "issue": "GITHUB_TOKEN / GITHUB_OWNER not set"}
        try:
            return {"configured": True, "owner": self.owner, "ok": True, "token_user": self.token_user(), "owner_type": self.owner_type()}
        except GitHubError as e:
            return {"configured": True, "owner": self.owner, "ok": False, "issue": str(e)}

    def token_user(self) -> str:
        if self._token_user is None:
            self._token_user = self._req("GET", "/user").json()["login"]
        return self._token_user

    def owner_type(self) -> str:
        if self._owner_type is None:
            t = self._req("GET", f"/users/{self.owner}").json().get("type", "User")
            self._owner_type = "org" if t == "Organization" else "user"
        return self._owner_type

    def repo_exists(self, name: str) -> bool:
        return self._req("GET", self._repo(name), ok=(200, 404)).status_code == 200

    def create_repo(self, name: str, description: str) -> dict:
        """Always private. auto_init gives the repo a first commit so we can build on top of it with the Git Data API."""
        body = {"name": name, "description": description[:300], "private": True, "auto_init": True, "has_issues": True,
                "has_wiki": False, "has_projects": False}
        path = "/user/repos" if self.owner_type() == "user" else f"/orgs/{self.owner}/repos"
        if self.owner_type() == "user" and self.token_user().lower() != self.owner.lower():
            raise GitHubError(f"The token belongs to a different user than GITHUB_OWNER ({self.owner}).", 403, "owner_mismatch")
        r = self._req("POST", path, ok=(201, 422), json=body)
        if r.status_code == 422:
            raise RepoExists(name)
        d = r.json()
        if not d.get("private"):
            raise GitHubError("Refusing to continue: the repository was not created as private.", None, "not_private")
        return {"name": d["name"], "url": d["html_url"], "default_branch": d.get("default_branch", "main")}

    def get_repo(self, name: str) -> dict:
        d = self._req("GET", self._repo(name)).json()
        return {"name": d["name"], "url": d["html_url"], "private": d.get("private"), "default_branch": d.get("default_branch", "main")}

    # ---------------------------------------------------------------- git data
    def head(self, name: str, branch: str = "main") -> str:
        return self._req("GET", f"{self._repo(name)}/git/ref/heads/{branch}").json()["object"]["sha"]

    def tree(self, name: str, commit_sha: str) -> dict[str, str]:
        """{path: blob sha} of every file at a commit."""
        tree_sha = self._req("GET", f"{self._repo(name)}/git/commits/{commit_sha}").json()["tree"]["sha"]
        d = self._req("GET", f"{self._repo(name)}/git/trees/{tree_sha}", params={"recursive": "1"}).json()
        if d.get("truncated"):
            raise GitHubError("Repository tree is too large to read in one call.", None, "tree_truncated")
        return {e["path"]: e["sha"] for e in d["tree"] if e["type"] == "blob"}

    def blob_text(self, name: str, sha: str) -> str:
        d = self._req("GET", f"{self._repo(name)}/git/blobs/{sha}").json()
        return base64.b64decode(d["content"]).decode(errors="replace") if d.get("encoding") == "base64" else d["content"]

    def commit_files(self, name: str, base_commit: str, changes: dict[str, str | None], message: str,
                     executable: frozenset[str] = frozenset(), branch: str = "main") -> str:
        """One commit applying `changes` ({path: text, or None to delete}) on top of base_commit. Returns the new commit sha."""
        base_tree = self._req("GET", f"{self._repo(name)}/git/commits/{base_commit}").json()["tree"]["sha"]
        entries = []
        for path, content in sorted(changes.items()):
            if path.startswith("/") or ".." in path.split("/") or "\\" in path:
                raise GitHubError(f"Refusing unsafe path '{path}'.", None, "bad_path")
            if content is None:
                entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            else:
                entries.append({"path": path, "mode": "100755" if path in executable else "100644", "type": "blob", "content": content})
        tree = self._req("POST", f"{self._repo(name)}/git/trees", json={"base_tree": base_tree, "tree": entries}).json()["sha"]
        commit = self._req("POST", f"{self._repo(name)}/git/commits",
                           json={"message": message, "tree": tree, "parents": [base_commit]}).json()["sha"]
        r = self._req("PATCH", f"{self._repo(name)}/git/refs/heads/{branch}", ok=(200, 422), json={"sha": commit, "force": False})
        if r.status_code == 422:
            raise BranchMoved()
        return commit

    # ---------------------------------------------------------------- collaborators
    def add_collaborator(self, name: str, username: str, permission: str = "push") -> dict:
        """Sends a GitHub invitation (GitHub emails the invitee). On a personal-account repo GitHub always grants write
        access regardless of `permission`. Returns {'status': 'invited'|'already_collaborator', 'invitation_id'}."""
        r = self._req("PUT", f"{self._repo(name)}/collaborators/{username}", json={"permission": permission})
        if r.status_code == 204:
            return {"status": "already_collaborator", "invitation_id": None}
        return {"status": "invited", "invitation_id": str(r.json().get("id"))}

    def collaborator_status(self, name: str, username: str) -> str:
        r = self._req("GET", f"{self._repo(name)}/collaborators/{username}", ok=(204, 404))
        return "collaborator" if r.status_code == 204 else "not_collaborator"

    def invitations(self, name: str) -> list[dict]:
        return [{"id": str(i["id"]), "login": i["invitee"]["login"], "created_at": i.get("created_at"), "expired": bool(i.get("expired"))}
                for i in self._req("GET", f"{self._repo(name)}/invitations").json()]

    def cancel_invitation(self, name: str, invitation_id: str):
        self._req("DELETE", f"{self._repo(name)}/invitations/{invitation_id}")

    # ---------------------------------------------------------------- issues
    def open_issues(self, name: str, limit: int = 30) -> list[dict]:
        rows = self._req("GET", f"{self._repo(name)}/issues", params={"state": "open", "per_page": min(limit, 100)}).json()
        return [{"number": i["number"], "title": i["title"], "url": i["html_url"], "user": i["user"]["login"],
                 "created_at": i["created_at"]} for i in rows if "pull_request" not in i][:limit]
