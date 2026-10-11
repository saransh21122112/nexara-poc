#!/usr/bin/env python3
"""PR gate for spec-driven development (docs/sdd.md): behaviour changes under backend/ or frontend/ need a spec under docs/specs/.

Used by CI (reads the PR body from the GitHub event and the changed files from git) and by tests (call check())."""
import json
import os
import re
import subprocess
import sys

CODE = ("backend/", "frontend/")
TEMPLATE = "docs/specs/000-template.md"
NO_SPEC = re.compile(r"^\s*no-spec:[ \t]*(\S.*)$", re.I | re.M)


def check(changed: list[str], body: str) -> tuple[bool, str]:
    if not any(p.startswith(CODE) for p in changed):
        return True, "no behaviour code changed"
    if any(p.startswith("docs/specs/") and p.endswith(".md") and p != TEMPLATE for p in changed):
        return True, "a spec changed with the code"
    m = NO_SPEC.search(body or "")
    if m:
        return True, f"No-spec accepted: {m.group(1).strip()}"
    return False, ("This PR changes backend/ or frontend/ but no spec under docs/specs/ (the template does not count). "
                   "Write or update a spec first (docs/sdd.md), or add a line 'No-spec: <reason>' to the PR description "
                   "for typos, dependency bumps or test-only changes.")


def main() -> int:
    base = os.environ.get("BASE_REF") or "origin/dev"
    body = ""
    if (path := os.environ.get("GITHUB_EVENT_PATH")) and os.path.exists(path):
        body = (json.load(open(path)).get("pull_request") or {}).get("body") or ""
    files = subprocess.check_output(["git", "diff", "--name-only", f"{base}...HEAD"], text=True).split()
    ok, why = check(files, body)
    print(("OK: " if ok else "FAIL: ") + why)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
