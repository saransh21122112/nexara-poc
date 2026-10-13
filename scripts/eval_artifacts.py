#!/usr/bin/env python3
"""Local evaluation: does Nexara deliver what each kind of request is meant to deliver?

Runs the REAL pipeline (real model through your Claude login) on fictional requests in evals/requests/*.json, lays the result out under
evals/out/<name>/ (git-ignored) and grades it. NOTHING is pushed to GitHub and no customer is touched.

    NEXARA_RUN_GENERATED_CODE=1 python scripts/eval_artifacts.py [name ...] [--force] [--skip-smoke] [--skip-review]

Grades per artifact (all automatic): generated a valid spec, static gate, `claude plugin validate`, every example run in the sandbox,
safety probes refused, a real `claude -p` smoke session with the plugin loaded, and a second-model review (advisory: flagged for a human).
Writes evals/out/<name>/result.json and docs/evals/REPORT.md."""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.pop("ANTHROPIC_API_KEY", None)               # use the Claude login (subscription), as agreed
os.environ["NEXARA_RUN_GENERATED_CODE"] = "1"
os.environ.setdefault("NEXARA_DB", str(Path(tempfile.mkdtemp(prefix="nexara-eval-")) / "eval.db"))

from backend import agent_service, build_service, database as dbm, providers, repo_gen  # noqa: E402
from backend.artifacts import checks as code_checks, materialize, sandbox  # noqa: E402

OUT = ROOT / "evals" / "out"
REQS = ROOT / "evals" / "requests"
REPORT = ROOT / "docs" / "evals" / "REPORT.md"


# ------------------------------------------------------------------ helpers
def _g(name, ok, detail=""):
    return {"name": name, "ok": ok, "detail": str(detail)[:500]}


def write_tree(files: dict, root: Path):
    if root.exists():
        shutil.rmtree(root)
    for p, c in files.items():
        f = root / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(c, encoding="utf-8")
        if repo_gen.is_executable(p):
            f.chmod(0o755)


def plugin_validate(root: Path):
    if not shutil.which("claude"):
        return _g("claude plugin validate", None, "claude CLI not installed")
    p = subprocess.run(["claude", "plugin", "validate", str(root), "--json"], capture_output=True, text=True, timeout=180)
    try:
        data = json.loads(p.stdout)
    except ValueError:
        return _g("claude plugin validate", False, (p.stdout + p.stderr)[:300])
    bad = list(data.get("errors") or []) + [w for w in data.get("warnings") or [] if "version" not in json.dumps(w).lower()]
    return _g("claude plugin validate", not bad, json.dumps(bad)[:400] if bad else "valid")


def pick_arg(schema: dict):
    props = (schema or {}).get("properties", {})
    for k in (schema or {}).get("required", []):
        if props.get(k, {}).get("type") == "string":
            return k
    return next((k for k, v in props.items() if v.get("type") == "string"), None)


def run_probes(spec: dict, probes: list, root: Path):
    """Hostile calls through the real server in the sandbox, found by tool-name pattern; each must be refused."""
    out = []
    for srv in spec.get("mcp_servers", []):
        tools, examples = srv["tools"], []
        for pr in probes:
            tool = next((t for t in tools if re.search(pr["match"], t["name"], re.I)), None)
            key = pick_arg(tool["input_schema"]) if tool else None
            if not tool or not key:
                out.append(_g(f"probe {pr['value'][:40]!r}", None, f"no matching tool/argument in {srv['name']}"))
                continue
            examples.append((tool["name"], pr["value"], {"args": {key: pr["value"]}, "expect_error": "*"}))
        if not examples:
            continue
        base = root / "servers" / srv["name"]
        tests = json.loads((base / "tests.json").read_text())
        tests["tools"] = [{"name": n, "examples": [e]} for n, _, e in examples]
        (base / "probes.json").write_text(json.dumps(tests))
        r = sandbox.run_python(root / "lib" / "nx_testkit.py", cwd=root, args=[str(base / "probes.json")], timeout_s=120)
        try:
            rep = json.loads(r.stdout)
            rows = [x for x in rep["results"] if re.search(r" example \d+$", x["name"])]
        except (ValueError, KeyError):
            out.append(_g(f"probes for {srv['name']}", False, (r.stderr or r.stdout)[-300:]))
            continue
        for (tname, value, _), row in zip(examples, rows):
            out.append(_g(f"probe {tname}({value[:36]!r}) is refused", row["ok"], row["detail"]))
    return out


def smoke(spec: dict, plugin: str, root: Path, sm: dict, tmp: Path):
    """A real Claude Code session with the plugin loaded."""
    if not shutil.which("claude"):
        return _g("claude -p smoke test", None, "claude CLI not installed")
    env = dict(os.environ)
    server = None
    allowed = ["Skill"]
    for srv in spec.get("mcp_servers", []):
        allowed += [f"mcp__plugin_{plugin}_{srv['name']}__{t['name']}" for t in srv["tools"]]
    if spec.get("mcp_servers"):
        sys.path.insert(0, str(ROOT / "backend" / "artifacts" / "blocks"))
        import nx_testkit
        srv0 = spec["mcp_servers"][0]
        cfg_path, server = nx_testkit._build(srv0.get("fixtures") or {}, tmp, {})
        env["NX_CONFIG"] = str(cfg_path)
    try:
        cmd = ["claude", "-p", sm["prompt"], "--plugin-dir", str(root), "--output-format", "stream-json", "--verbose", "--max-turns", "12",
               "--permission-mode", "dontAsk", "--allowedTools", *allowed]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=420, cwd=tmp, env=env)
    except subprocess.TimeoutExpired:
        return _g("claude -p smoke test", False, "timed out after 420s")
    finally:
        if server:
            server.shutdown()
    calls, errors, final = [], 0, ""
    for line in p.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        msg = ev.get("message") or {}
        for blk in (msg.get("content") or []) if isinstance(msg.get("content"), list) else []:
            if blk.get("type") == "tool_use":
                calls.append(blk.get("name", ""))
            elif blk.get("type") == "tool_result" and blk.get("is_error"):
                errors += 1
        if ev.get("type") == "result":
            final = ev.get("result") or ""
    mcp_calls = [c for c in calls if c.startswith("mcp__plugin_")]
    low = final.lower()
    if spec.get("mcp_servers") and sm.get("tool_prefix"):
        ok = bool(mcp_calls) and errors == 0 and len(final) > 20
        return _g("claude -p smoke test", ok, f"{len(mcp_calls)} MCP call(s) {mcp_calls[:3]}, {errors} tool error(s); answer: {final[:200]!r}")
    ok = any(w.lower() in low for w in sm.get("expect_any", [])) and bool(final)
    return _g("claude -p smoke test", ok, f"tools {calls[:4]}; expected any of {sm.get('expect_any')}; answer: {final[:240]!r}")


RUBRIC = {
    "skill": "A SKILL must do the job itself: precise trigger-rich description, a concrete numbered workflow, an exact output format, edge cases, refusal rules, and domain-specific references. It must not merely forward to another agent.",
    "tool": "A TOOL must be a real runnable script that implements the requested computation correctly (check the logic against the request's examples), validates input, reports clear errors, and is wrapped by a skill that explains exactly how to call it.",
    "mcp_server": "An MCP SERVER must expose genuinely useful READ-ONLY tools for the request, enforce the stated limits (allow-lists, masking, row/size caps, host/folder restrictions) in code, keep secrets out of files, and ship setup instructions and a skill explaining when to use the tools.",
    "plugin": "A PLUGIN must combine the requested components so that they work together: each component has a clear job, skills reference the tools/servers they need, and nothing requested is missing.",
}


async def review(request: dict, spec: dict, kind: str):
    from backend import subscription
    brief = {k: spec.get(k) for k in ("agent_name", "purpose", "safety_restrictions")}
    brief["skills"] = [{"name": s["name"], "description": s["description"], "body": s["body"][:2500], "uses": s.get("uses"), "references": {r["name"]: r["content"][:1500] for r in s.get("references", [])}} for s in spec.get("skills", [])]
    brief["subagents"] = [{"name": a["name"], "instructions": a["instructions"][:1200]} for a in spec.get("subagents", [])]
    brief["scripts"] = [{"name": s["name"], "source": s["handler_source"][:5000], "examples": s["examples"]} for s in spec.get("scripts", [])]
    brief["servers"] = [{"name": s["name"], "setup": s.get("setup_source", "")[:2000], "config": [c["key"] for c in s.get("config_schema", [])],
                         "tools": [{"name": t["name"], "description": t["description"], "source": t["handler_source"][:3000]} for t in s["tools"]]} for s in spec.get("mcp_servers", [])]
    system = ("You are a strict reviewer of software a consultancy delivered to a customer. Judge ONLY whether the deliverable does what the request asked, using the rubric. "
              "Reply with ONE JSON object: {\"verdict\": \"pass\"|\"partial\"|\"fail\", \"score\": 1-5, \"strengths\": [..], \"issues\": [..], \"missing\": [..]}. Be specific and sceptical.")
    prompt = f"Rubric: {RUBRIC.get(kind, RUBRIC['plugin'])}\n\nCustomer request:\n{json.dumps(request)[:4000]}\n\nDeliverable (JSON):\n{json.dumps(brief)[:28000]}"
    raw = await subscription.complete_json(system, prompt)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        return json.loads(m.group(0))
    except (AttributeError, ValueError):
        return {"verdict": "unknown", "raw": raw[:500]}


# ------------------------------------------------------------------ one artifact
async def evaluate(item: dict, flags: set):
    name, request = item["name"], item["request"]
    res = {"name": name, "kind": request["agent_type"], "company": request["company_name"], "grades": [], "timings": {}}
    out_dir = OUT / name
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    saved = out_dir / "generated-spec.json"
    try:
        if saved.exists() and "--regenerate" not in flags:                          # never pay for the same generation twice
            blob = json.loads(saved.read_text())
            spec, prov, model = blob["spec"], blob["provider"], blob["model"]
        else:
            spec, prov, model = await agent_service.generate_spec(request)
            saved.write_text(json.dumps({"spec": spec, "provider": prov, "model": model}, ensure_ascii=False))
    except Exception as e:                                                          # noqa: BLE001
        res["grades"].append(_g("generated a valid spec", False, f"{type(e).__name__}: {e}"))
        return res
    res["timings"]["generate_s"] = round(time.time() - t0)
    res["model"] = model
    res["grades"].append(_g("generated a valid spec", True, f"{spec['agent_name']} ({spec.get('kind')})"))
    req_row = dbm.create_request(request)
    agent = dbm.create_agent(req_row["id"], spec, prov, model)
    cust_slug = dbm.slugify(request["company_name"])
    plugin = repo_gen.company_slug(cust_slug)
    repairs = {"n": 0}

    orig = agent_service.revise_spec

    async def counting(*a, **k):
        repairs["n"] += 1
        return await orig(*a, **k)
    agent_service.revise_spec = counting
    t1 = time.time()
    try:
        agent, report = await build_service.check_code(agent, {**request, "agent_type": request["agent_type"]}, plugin)
    finally:
        agent_service.revise_spec = orig
    spec = agent["spec"]
    res["timings"]["checks_s"] = round(time.time() - t1)
    res["repairs"] = repairs["n"]
    fresh = code_checks.run_checks(spec, plugin)
    failed = [c for c in fresh["checks"] if c["ok"] is False]
    res["grades"].append(_g("static gate", not any(c["group"] == "static" and c["ok"] is False for c in fresh["checks"]), "AST allow-list, config-only targets"))
    res["grades"].append(_g("examples run in the sandbox", fresh["verified"] and fresh["ran_code"] or (not (spec.get("scripts") or spec.get("mcp_servers")) and fresh["verified"]),
                            f"{len(fresh['checks'])} checks, {len(failed)} failed; repairs needed: {repairs['n']}" + ("; " + "; ".join(c['name'] for c in failed[:4]) if failed else "")))
    files = repo_gen.repo_files({"company_name": request["company_name"], "slug": cust_slug}, {"owner": "eval", "name": f"nexara-{cust_slug}"},
                                [{"slug": repo_gen.plugin_slug(spec), "spec": spec}])
    write_tree(files, out_dir / "plugin")
    (out_dir / "spec.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False))
    res["files"] = len(files)
    res["kinds_present"] = {"skills": len(spec.get("skills", [])), "subagents": len(spec.get("subagents", [])), "scripts": len(spec.get("scripts", [])), "servers": len(spec.get("mcp_servers", []))}
    res["grades"].append(plugin_validate(out_dir / "plugin"))
    if item.get("probes") and spec.get("mcp_servers"):
        res["grades"] += run_probes(spec, item["probes"], out_dir / "plugin")
    if "--skip-smoke" not in flags:
        t2 = time.time()
        with tempfile.TemporaryDirectory(prefix="nexara-smoke-") as td:
            res["grades"].append(smoke(spec, plugin, out_dir / "plugin", item["smoke"], Path(td)))
        res["timings"]["smoke_s"] = round(time.time() - t2)
    if "--skip-review" not in flags:
        try:
            res["review"] = await review(request, spec, request["agent_type"].lower().replace(" ", "_") if request["agent_type"] != "MCP server" else "mcp_server")
        except Exception as e:                                                      # noqa: BLE001
            res["review"] = {"verdict": "unknown", "raw": f"{type(e).__name__}: {e}"}
    return res


def passed(res: dict) -> bool:
    return all(g["ok"] is not False for g in res["grades"]) and not any(g["ok"] is None and g["name"] in ("generated a valid spec",) for g in res["grades"])


# ------------------------------------------------------------------ report
def write_report(results: list):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    ok = [r for r in results if passed(r)]
    lines = ["# Nexara deliverables: local evaluation", "",
             f"Generated by `scripts/eval_artifacts.py` on {time.strftime('%Y-%m-%d')}. Fictional customers, real model calls (Claude login), real `claude plugin validate`, "
             "real `claude -p` sessions with each plugin loaded, generated code run only inside the sandbox. Nothing was pushed to GitHub.", "",
             f"**Result: {len(ok)} of {len(results)} artifacts passed every automatic grade.**", "",
             "| Request | Kind | Valid spec | Static gate | Sandbox examples | Plugin validate | Probes | Claude smoke | Review | Repairs |", "|---|---|---|---|---|---|---|---|---|---|"]

    def cell(r, name):
        gs = [g for g in r["grades"] if g["name"].startswith(name)]
        if not gs:
            return "-"
        return "pass" if all(g["ok"] for g in gs if g["ok"] is not None) and any(g["ok"] for g in gs) else ("skip" if all(g["ok"] is None for g in gs) else "FAIL")
    for r in results:
        lines.append(f"| {r['name']} | {r['kind']} | {cell(r, 'generated')} | {cell(r, 'static')} | {cell(r, 'examples run')} | {cell(r, 'claude plugin')} | {cell(r, 'probe')} | {cell(r, 'claude -p')} | "
                     f"{(r.get('review') or {}).get('verdict', '-')} ({(r.get('review') or {}).get('score', '-')}) | {r.get('repairs', '-')} |")
    lines += ["", "## Details", ""]
    for r in results:
        lines += [f"### {r['name']} ({r['kind']}, {r['company']}) - {'PASS' if passed(r) else 'NEEDS WORK'}", "",
                  f"Components: {r.get('kinds_present')}; files: {r.get('files')}; timings (s): {r.get('timings')}", ""]
        for g in r["grades"]:
            lines.append(f"- {'PASS' if g['ok'] else 'skipped' if g['ok'] is None else 'FAIL'} **{g['name']}**" + (f": {g['detail']}" if g["ok"] is not True and g["detail"] else ""))
        rv = r.get("review") or {}
        if rv:
            lines += ["", f"Second-model review: **{rv.get('verdict')}** (score {rv.get('score')}). Issues: {'; '.join(map(str, rv.get('issues', [])))[:700] or 'none listed'}. Missing: {'; '.join(map(str, rv.get('missing', [])))[:400] or 'nothing listed'}."]
        lines.append("")
    REPORT.write_text("\n".join(lines), encoding="utf-8")


async def main(argv):
    flags = {a for a in argv if a.startswith("--")}
    names = [a for a in argv if not a.startswith("--")]
    dbm.init_db()
    providers.set_provider("claude_subscription")
    items = [json.loads(p.read_text()) for p in sorted(REQS.glob("*.json"))]
    items = [i for i in items if not names or i["name"] in names]
    results = []
    for item in items:
        cached = OUT / item["name"] / "result.json"
        if cached.exists() and "--force" not in flags:
            results.append(json.loads(cached.read_text()))
            print(f"[cached] {item['name']}", flush=True)
            continue
        t = time.time()
        print(f"[start] {item['name']}", flush=True)
        r = await evaluate(item, flags)
        (OUT / item["name"]).mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(r, indent=2, ensure_ascii=False))
        results.append(r)
        print(f"[{'PASS' if passed(r) else 'FAIL'}] {item['name']} in {round(time.time() - t)}s: " + "; ".join(f"{g['name']}={g['ok']}" for g in r["grades"]), flush=True)
        write_report(results)
    write_report(results)
    print(f"\n{sum(passed(r) for r in results)}/{len(results)} passed. Report: {REPORT}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
