"""In-memory fake of the GitHub REST endpoints the factory uses (repos, git data, collaborators, invitations, issues).
Used by the unit tests (through starlette's TestClient, an httpx.Client) and, as a real HTTP server, for local end-to-end runs:
    python -m tests.fake_github --port 9912 --token fake-token --owner demo-owner
It models what matters for delivery: auth, private repos, fast-forward-only ref updates, tree merges and deletions,
invitations that become collaborators only when accepted. It does NOT prove behaviour of the real GitHub."""
import argparse
import base64
import hashlib
import itertools
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.testclient import TestClient


def blob_sha(text: str) -> str:
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class FakeGitHub:
    def __init__(self, token="fake-token", owner="demo-owner", owner_type="User", token_user=None):
        self.token, self.owner, self.owner_type = token, owner, owner_type
        self.token_user = token_user or owner
        self.repos: dict[str, dict] = {}
        self.faults: list[tuple[str, str, int, dict]] = []   # (method, path substring, status, headers) consumed once
        self.calls: list[tuple[str, str]] = []
        self.force_public = False
        self._ids = itertools.count(1000)
        self.app = self._build()

    # ------------------------------------------------------------ helpers for tests
    def client(self):
        from backend.github_client import GitHubClient
        return GitHubClient(token=self.token, owner=self.owner, http=TestClient(self.app, base_url="https://api.github.test"))

    def accept_invite(self, repo: str, login: str):
        r = self.repos[repo]
        for iid, inv in list(r["invites"].items()):
            if inv["login"].lower() == login.lower():
                del r["invites"][iid]
                r["collab"].add(login.lower())

    def add_issue(self, repo: str, title: str, user="customer1", pr=False):
        n = len(self.repos[repo]["issues"]) + 1
        issue = {"number": n, "title": title, "html_url": f"https://github.com/{self.owner}/{repo}/issues/{n}",
                 "user": {"login": user}, "created_at": "2026-10-11T10:00:00Z"}
        if pr:
            issue["pull_request"] = {}
        self.repos[repo]["issues"].append(issue)

    def push_file(self, repo: str, path: str, content: str | None) -> str:
        """Simulate someone committing straight to main (customer or engineer)."""
        r = self.repos[repo]
        head = r["branches"]["main"]
        tree = dict(r["trees"][r["commits"][head]["tree"]])
        if content is None:
            tree.pop(path, None)
        else:
            r["blobs"][blob_sha(content)] = content
            tree[path] = ("100644", blob_sha(content))
        return self._commit(r, tree, [head], f"manual edit of {path}")

    def commit_on(self, repo: str, branch: str, path: str, content: str, message: str) -> str:
        """Simulate someone committing to a non-main branch."""
        r = self.repos[repo]
        head = r["branches"][branch]
        tree = dict(r["trees"][r["commits"][head]["tree"]])
        r["blobs"][blob_sha(content)] = content
        tree[path] = ("100644", blob_sha(content))
        tsha = hashlib.sha1(json.dumps(sorted(tree.items())).encode()).hexdigest()
        r["trees"][tsha] = tree
        csha = hashlib.sha1(f"{tsha}{head}{message}{next(self._ids)}".encode()).hexdigest()
        r["commits"][csha] = {"tree": tsha, "parents": [head], "message": message}
        r["branches"][branch] = csha
        return csha

    def merge_pr(self, repo: str, number: int):
        """Simulate a person pressing Merge: main takes the PR branch's tree (a merge commit)."""
        r = self.repos[repo]
        pr = r["pulls"][number]
        head, base = r["branches"][pr["head"]], r["branches"][pr["base"]]
        csha = hashlib.sha1(f"merge{head}{base}{next(self._ids)}".encode()).hexdigest()
        r["commits"][csha] = {"tree": r["commits"][head]["tree"], "parents": [base, head], "message": f"Merge pull request #{number}"}
        r["branches"][pr["base"]] = csha
        pr.update(state="closed", merged=True)

    def close_pr(self, repo: str, number: int):
        self.repos[repo]["pulls"][number].update(state="closed", merged=False)

    def files(self, repo: str, ref="main") -> dict[str, str]:
        r = self.repos[repo]
        tree = r["trees"][r["commits"][r["branches"][ref]]["tree"]]
        return {p: r["blobs"][sha] for p, (mode, sha) in tree.items()}

    def modes(self, repo: str) -> dict[str, str]:
        r = self.repos[repo]
        return {p: m for p, (m, sha) in r["trees"][r["commits"][r["branches"]["main"]]["tree"]].items()}

    # ------------------------------------------------------------ git object store
    def _commit(self, r, tree: dict, parents: list, message: str) -> str:
        tsha = hashlib.sha1(json.dumps(sorted(tree.items())).encode()).hexdigest()
        r["trees"][tsha] = dict(tree)
        csha = hashlib.sha1(f"{tsha}{parents}{message}{next(self._ids)}".encode()).hexdigest()
        r["commits"][csha] = {"tree": tsha, "parents": parents, "message": message}
        r["branches"]["main"] = csha
        return csha

    # ------------------------------------------------------------ HTTP app
    def _build(self) -> FastAPI:
        app = FastAPI()
        fake = self

        @app.middleware("http")
        async def gate(request: Request, call_next):
            fake.calls.append((request.method, request.url.path))
            if request.url.path.startswith("/__"):      # test-only control plane (never part of the GitHub API)
                return await call_next(request)
            if request.headers.get("authorization") != f"Bearer {fake.token}":
                return JSONResponse({"message": "Bad credentials"}, status_code=401)
            for i, (m, frag, status, headers) in enumerate(fake.faults):
                if m == request.method and frag in request.url.path:
                    fake.faults.pop(i)
                    return JSONResponse({"message": "injected fault"}, status_code=status, headers=headers)
            return await call_next(request)

        def repo_or_404(owner, name):
            if owner.lower() != fake.owner.lower() or name not in fake.repos:
                return None
            return fake.repos[name]

        NF = lambda: JSONResponse({"message": "Not Found"}, status_code=404)

        @app.get("/__dump/{name}")
        def dump(name: str):
            if name not in fake.repos:
                return NF()
            r = fake.repos[name]
            return {"private": r["private"], "files": fake.files(name), "modes": fake.modes(name), "head": r["branches"]["main"],
                    "invites": [i["login"] for i in r["invites"].values()], "collaborators": sorted(r["collab"]), "commits": len(r["commits"])}

        @app.post("/__accept/{name}/{login}")
        def accept(name: str, login: str):
            fake.accept_invite(name, login)
            return {"ok": True}

        @app.post("/__issue/{name}")
        async def issue(name: str, request: Request):
            fake.add_issue(name, (await request.json())["title"])
            return {"ok": True}

        @app.post("/__push/{name}")
        async def push(name: str, request: Request):
            b = await request.json()
            return {"commit": fake.push_file(name, b["path"], b.get("content"))}

        @app.get("/user")
        def user():
            return {"login": fake.token_user}

        @app.get("/users/{u}")
        def users(u: str):
            return {"login": u, "type": "Organization" if (fake.owner_type == "Organization" and u == fake.owner) else "User"}

        def create(name: str, body: dict):
            if name in fake.repos:
                return JSONResponse({"message": "Repository creation failed.", "errors": [{"message": "name already exists on this account"}]}, status_code=422)
            r = {"private": False if fake.force_public else bool(body.get("private")), "branches": {}, "commits": {}, "trees": {}, "blobs": {},
                 "collab": set(), "invites": {}, "issues": [], "pulls": {}, "description": body.get("description", "")}
            fake.repos[name] = r
            r["blobs"][blob_sha("# " + name + "\n")] = "# " + name + "\n"
            fake._commit(r, {"README.md": ("100644", blob_sha("# " + name + "\n"))}, [], "Initial commit")
            return JSONResponse({"name": name, "html_url": f"https://github.com/{fake.owner}/{name}", "private": r["private"],
                                 "default_branch": "main"}, status_code=201)

        @app.post("/user/repos")
        async def create_user(request: Request):
            b = await request.json()
            return create(b["name"], b)

        @app.post("/orgs/{org}/repos")
        async def create_org(org: str, request: Request):
            b = await request.json()
            return create(b["name"], b)

        @app.get("/repos/{owner}/{name}")
        def get_repo(owner: str, name: str):
            r = repo_or_404(owner, name)
            return NF() if r is None else {"name": name, "html_url": f"https://github.com/{owner}/{name}", "private": r["private"], "default_branch": "main"}

        @app.get("/repos/{owner}/{name}/git/ref/heads/{branch}")
        def ref(owner: str, name: str, branch: str):
            r = repo_or_404(owner, name)
            return NF() if r is None or branch not in r["branches"] else {"object": {"sha": r["branches"][branch]}}

        @app.post("/repos/{owner}/{name}/git/refs")
        async def post_ref(owner: str, name: str, request: Request):
            r = repo_or_404(owner, name)
            if r is None:
                return NF()
            b = await request.json()
            branch = b["ref"].removeprefix("refs/heads/")
            if branch in r["branches"] or b["sha"] not in r["commits"]:
                return JSONResponse({"message": "Reference already exists"}, status_code=422)
            r["branches"][branch] = b["sha"]
            return JSONResponse({"ref": b["ref"], "object": {"sha": b["sha"]}}, status_code=201)

        def ancestors(r, sha):
            seen, stack = set(), [sha]
            while stack:
                c = stack.pop()
                if c not in seen:
                    seen.add(c)
                    stack.extend(r["commits"][c]["parents"])
            return seen

        @app.get("/repos/{owner}/{name}/compare/{spec}")
        def compare(owner: str, name: str, spec: str):
            r = repo_or_404(owner, name)
            if r is None:
                return NF()
            base, head = spec.split("...")
            if base not in r["branches"] or head not in r["branches"]:
                return NF()
            bs, hs = ancestors(r, r["branches"][base]), ancestors(r, r["branches"][head])
            only = [c for c in hs - bs]
            # oldest first: a commit comes after its parents
            order = sorted(only, key=lambda c: len(ancestors(r, c)))
            return {"ahead_by": len(order), "behind_by": len(bs - hs),
                    "commits": [{"sha": c, "commit": {"message": r["commits"][c]["message"]}} for c in order]}

        def pr_json(owner, name, p):
            return {"number": p["number"], "title": p["title"], "body": p["body"], "state": p["state"], "merged": p["merged"],
                    "html_url": f"https://github.com/{owner}/{name}/pull/{p['number']}"}

        @app.get("/repos/{owner}/{name}/pulls")
        def list_pulls(owner: str, name: str, state: str = "open", head: str = "", base: str = ""):
            r = repo_or_404(owner, name)
            if r is None:
                return NF()
            want = head.split(":")[-1] if head else None
            return [pr_json(owner, name, p) for p in r["pulls"].values()
                    if p["state"] == state and (want is None or p["head"] == want) and (not base or p["base"] == base)]

        @app.post("/repos/{owner}/{name}/pulls")
        async def create_pull(owner: str, name: str, request: Request):
            r = repo_or_404(owner, name)
            if r is None:
                return NF()
            b = await request.json()
            if b["head"] not in r["branches"] or b["base"] not in r["branches"]:
                return JSONResponse({"message": "Validation Failed"}, status_code=422)
            if any(p["state"] == "open" and p["head"] == b["head"] and p["base"] == b["base"] for p in r["pulls"].values()):
                return JSONResponse({"message": "A pull request already exists"}, status_code=422)
            n = len(r["pulls"]) + 1
            r["pulls"][n] = {"number": n, "title": b["title"], "body": b.get("body", ""), "head": b["head"], "base": b["base"], "state": "open", "merged": False}
            return JSONResponse(pr_json(owner, name, r["pulls"][n]), status_code=201)

        @app.get("/repos/{owner}/{name}/pulls/{n}")
        def get_pull(owner: str, name: str, n: int):
            r = repo_or_404(owner, name)
            return NF() if r is None or n not in r["pulls"] else pr_json(owner, name, r["pulls"][n])

        @app.patch("/repos/{owner}/{name}/pulls/{n}")
        async def patch_pull(owner: str, name: str, n: int, request: Request):
            r = repo_or_404(owner, name)
            if r is None or n not in r["pulls"]:
                return NF()
            b = await request.json()
            r["pulls"][n].update(title=b.get("title", r["pulls"][n]["title"]), body=b.get("body", r["pulls"][n]["body"]))
            return pr_json(owner, name, r["pulls"][n])

        @app.get("/repos/{owner}/{name}/git/commits/{sha}")
        def commit(owner: str, name: str, sha: str):
            r = repo_or_404(owner, name)
            c = r["commits"].get(sha) if r else None
            return NF() if c is None else {"sha": sha, "tree": {"sha": c["tree"]}, "parents": [{"sha": p} for p in c["parents"]], "message": c["message"]}

        @app.post("/repos/{owner}/{name}/git/trees")
        async def post_tree(owner: str, name: str, request: Request):
            r = repo_or_404(owner, name)
            if r is None:
                return NF()
            b = await request.json()
            tree = dict(r["trees"].get(b.get("base_tree"), {}))
            for e in b["tree"]:
                if "content" in e:
                    r["blobs"][blob_sha(e["content"])] = e["content"]
                    tree[e["path"]] = (e["mode"], blob_sha(e["content"]))
                elif e.get("sha") is None:
                    tree.pop(e["path"], None)
                else:
                    tree[e["path"]] = (e["mode"], e["sha"])
            tsha = hashlib.sha1(json.dumps(sorted(tree.items())).encode()).hexdigest()
            r["trees"][tsha] = tree
            return JSONResponse({"sha": tsha}, status_code=201)

        @app.post("/repos/{owner}/{name}/git/commits")
        async def post_commit(owner: str, name: str, request: Request):
            r = repo_or_404(owner, name)
            if r is None:
                return NF()
            b = await request.json()
            csha = hashlib.sha1(f"{b['tree']}{b['parents']}{b['message']}{next(fake._ids)}".encode()).hexdigest()
            r["commits"][csha] = {"tree": b["tree"], "parents": b["parents"], "message": b["message"]}
            return JSONResponse({"sha": csha}, status_code=201)

        @app.patch("/repos/{owner}/{name}/git/refs/heads/{branch}")
        async def patch_ref(owner: str, name: str, branch: str, request: Request):
            r = repo_or_404(owner, name)
            if r is None or branch not in r["branches"]:
                return NF()
            b = await request.json()
            if not b.get("force") and r["branches"][branch] not in r["commits"][b["sha"]]["parents"]:
                return JSONResponse({"message": "Update is not a fast forward"}, status_code=422)
            r["branches"][branch] = b["sha"]
            return {"object": {"sha": b["sha"]}}

        @app.get("/repos/{owner}/{name}/git/trees/{sha}")
        def get_tree(owner: str, name: str, sha: str):
            r = repo_or_404(owner, name)
            if r is None or sha not in r["trees"]:
                return NF()
            return {"sha": sha, "truncated": False,
                    "tree": [{"path": p, "mode": m, "type": "blob", "sha": s} for p, (m, s) in sorted(r["trees"][sha].items())]}

        @app.get("/repos/{owner}/{name}/git/blobs/{sha}")
        def get_blob(owner: str, name: str, sha: str):
            r = repo_or_404(owner, name)
            if r is None or sha not in r["blobs"]:
                return NF()
            return {"sha": sha, "encoding": "base64", "content": base64.b64encode(r["blobs"][sha].encode()).decode()}

        @app.put("/repos/{owner}/{name}/collaborators/{u}")
        async def put_collab(owner: str, name: str, u: str, request: Request):
            r = repo_or_404(owner, name)
            if r is None:
                return NF()
            body = await request.json()
            if u.lower() == fake.owner.lower() or u.lower() in r["collab"]:
                return Response(status_code=204)
            for inv in r["invites"].values():
                if inv["login"].lower() == u.lower():
                    return JSONResponse({"id": inv["id"], "invitee": {"login": u}}, status_code=201)
            iid = next(fake._ids)
            r["invites"][iid] = {"id": iid, "login": u, "permission": body.get("permission"), "created_at": "2026-10-11T10:00:00Z"}
            return JSONResponse({"id": iid, "invitee": {"login": u}}, status_code=201)

        @app.get("/repos/{owner}/{name}/collaborators/{u}")
        def get_collab(owner: str, name: str, u: str):
            r = repo_or_404(owner, name)
            if r is None or (u.lower() not in r["collab"] and u.lower() != fake.owner.lower()):
                return NF()
            return Response(status_code=204)

        @app.get("/repos/{owner}/{name}/invitations")
        def list_inv(owner: str, name: str):
            r = repo_or_404(owner, name)
            return NF() if r is None else [{"id": i["id"], "invitee": {"login": i["login"]}, "created_at": i["created_at"], "expired": False}
                                           for i in r["invites"].values()]

        @app.delete("/repos/{owner}/{name}/invitations/{iid}")
        def del_inv(owner: str, name: str, iid: int):
            r = repo_or_404(owner, name)
            if r is None or iid not in r["invites"]:
                return NF()
            del r["invites"][iid]
            return Response(status_code=204)

        @app.get("/repos/{owner}/{name}/issues")
        def issues(owner: str, name: str):
            r = repo_or_404(owner, name)
            return NF() if r is None else r["issues"]

        return app


if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9912)
    ap.add_argument("--token", default="fake-token")
    ap.add_argument("--owner", default="demo-owner")
    a = ap.parse_args()
    uvicorn.run(FakeGitHub(a.token, a.owner).app, host="127.0.0.1", port=a.port, log_level="warning")
