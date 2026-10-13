"""The checks that decide whether a structured deliverable is verified: static gate, real plugin validation, and the artifact's own examples run
in the sandbox. Returns a plain report dict that is stored with the agent and shown to staff."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from . import materialize, sandbox, validate


def spec_digest(spec: dict) -> str:
    """A report describes exactly one spec: delivery compares this digest, so a report that finishes after an edit can never verify the new content."""
    return hashlib.sha256(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _c(group, name, ok, detail=""):
    return {"group": group, "name": name, "ok": ok, "detail": str(detail)[:600]}


def _write(files: dict[str, str], root: Path):
    for path, content in files.items():
        f = root / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")


def _plugin_validate(root: Path) -> list[dict]:
    if not shutil.which("claude"):
        return [_c("plugin", "claude plugin validate", None, "skipped: the claude CLI is not installed on this host")]
    (root / ".claude-plugin").mkdir(exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "nexara-check", "description": "check", "author": {"name": "Nexara AI"}}))
    try:
        p = subprocess.run(["claude", "plugin", "validate", str(root), "--json"], capture_output=True, text=True, timeout=120)
        data = json.loads(p.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return [_c("plugin", "claude plugin validate", None, "skipped: the validator did not answer")]
    errors = list(data.get("errors") or [])
    warnings = [w for w in data.get("warnings") or [] if "version" not in json.dumps(w).lower()]      # we omit `version` on purpose
    return [_c("plugin", "claude plugin validate", not errors and not warnings, json.dumps(errors + warnings)[:500] if errors or warnings else "valid")]


def _kit(root: Path, sub: str, group: str, label: str) -> list[dict]:
    r = sandbox.run_python(root / "lib" / "nx_testkit.py", cwd=root, args=[str(root / sub / "tests.json")], timeout_s=90)
    try:
        report = json.loads(r.stdout)
    except ValueError:
        why = "timed out" if r.timed_out else r.reason or "crashed"
        return [_c(group, f"{label}: run", False, f"{why}: {(r.stderr or r.stdout)[-400:]}")]
    return [_c(group, f"{label}: {x['name']}", x["ok"], x.get("detail", "")) for x in report.get("results", [])]


def run_checks(spec: dict, plugin: str = "plugin") -> dict:
    """Never raises for bad specs: problems become failed checks. The report carries the digest of the spec it was run on."""
    rep = _run_checks(spec, plugin)
    rep["spec_digest"] = spec_digest(spec)
    return rep


def _run_checks(spec: dict, plugin: str) -> dict:
    if not materialize.is_structured(spec):
        return {"applicable": False, "ran_code": False, "verified": False, "reason": "This specification has no code to check.", "checks": [], "isolation": ""}
    checks = []
    problems = validate.check_spec(spec)
    checks.append(_c("static", "static code gate", not problems, "; ".join(problems[:8]) if problems else "no banned constructs, targets come from config"))
    if problems:
        return {"applicable": True, "ran_code": False, "verified": False, "reason": "The static gate rejected the code.", "checks": checks, "isolation": ""}
    try:
        files = materialize.assemble([spec], plugin)
    except (ValueError, KeyError) as e:
        checks.append(_c("static", "materialize", False, f"{type(e).__name__}: {e}"))
        return {"applicable": True, "ran_code": False, "verified": False, "reason": "The files could not be laid out.", "checks": checks, "isolation": ""}
    checks.append(_c("static", "materialize", True, f"{len(files)} files"))
    run = sandbox.enabled()
    isolation = ""
    with sandbox.temp_dir() as td:
        root = Path(td)
        _write(files, root)
        checks += _plugin_validate(root)
        if run:
            for s in spec.get("mcp_servers", []):
                checks += _kit(root, f"servers/{s['name']}", "code", f"server {s['name']}")
            for s in spec.get("scripts", []):
                checks += _kit(root, f"tools/{s['name']}", "code", f"tool {s['name']}")
            isolation = "os-level" if sandbox.os_isolation_available() else "python-guard-only"
    failed = [c for c in checks if c["ok"] is False]
    has_code = bool(spec.get("mcp_servers") or spec.get("scripts"))
    ran = run and has_code
    reason = "" if (ran or not has_code) else "Generated code was not run: NEXARA_RUN_GENERATED_CODE is not set on this host."
    return {"applicable": True, "ran_code": ran, "verified": not failed and (ran or not has_code), "reason": reason, "checks": checks, "isolation": isolation}


def failure_text(report: dict, limit: int = 1500) -> str:
    """What the model is shown when asked to repair a spec."""
    lines = [f"- {c['name']}: {c['detail']}" for c in report.get("checks", []) if c["ok"] is False]
    return "\n".join(lines)[:limit]
