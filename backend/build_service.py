"""The build pipeline behind the live view: request -> analyse -> specify -> test -> package -> ready.
Every event is a real thing that happened (a model call finished, a scenario passed or failed, files were laid out). Nothing is pushed to
GitHub here; delivery stays a separate, human-confirmed action and shows up later through the repo events."""
import asyncio
import os

from . import agent_service, database as dbm, repo_gen
from .artifacts import checks as code_checks
from .providers import NotConfigured, UpstreamError

_active: set[str] = set()


def is_running(request_id: str) -> bool:
    return request_id in _active


def start(request_id: str) -> None:
    """Fire and forget; the caller has already checked is_running()."""
    _active.add(request_id)
    asyncio.get_running_loop().create_task(_guarded(request_id))


async def _guarded(rid: str):
    try:
        await run_build(rid)
    finally:
        _active.discard(rid)


def _ev(rid, stage, state, title, detail=None):
    dbm.add_build_event(rid, stage, state, title, detail)


def is_structured(spec: dict) -> bool:
    from .artifacts import materialize
    return materialize.is_structured(spec)


def _will_ask(r: dict) -> list[str]:
    return [f"Write a {r['agent_type']} specification for {r['company_name']}",
            "Name it, state its purpose and list what it can do",
            "Decide which tools it may use (read-only by default)",
            "Add safety limits and three test scenarios"]


MAX_REPAIRS = 2


async def check_code(agent: dict, request: dict, plugin: str, on_progress=None) -> tuple[dict, dict]:
    """Run the code checks on a structured spec; when they fail, ask the model for a repair (at most MAX_REPAIRS times) and re-check.
    Returns (agent, report) with the agent's stored spec/report updated. Plain specs (nothing to run) come back untouched."""
    spec = agent["spec"]
    report = await asyncio.to_thread(code_checks.run_checks, spec, plugin)
    for attempt in range(MAX_REPAIRS + 1):
        failed = [c for c in report["checks"] if c["ok"] is False]
        if on_progress:
            await on_progress(report, attempt)
        if not failed or attempt == MAX_REPAIRS:
            break
        instruction = ("The automatic checks on the generated files found these problems. Fix the code, fixtures or examples so that every check passes, "
                       "changing as little else as possible:\n" + code_checks.failure_text(report))
        spec, _, _ = await agent_service.revise_spec(spec, instruction, request)
        agent = dbm.update_agent_spec(agent["id"], spec)
        report = await asyncio.to_thread(code_checks.run_checks, spec, plugin)
    dbm.save_checks(agent["id"], report if report.get("applicable") else None)
    return dbm.get_agent(agent["id"]), report


async def run_build(rid: str) -> None:
    r = dbm.get_request(rid)
    stage = "received"
    try:
        _ev(rid, "received", "running", "Reading your request")
        _ev(rid, "received", "done", "Request received", {"kind": r["agent_type"], "tools": r["existing_tools"], "company": r["company_name"]})
        stage = "analyse"
        _ev(rid, "analyse", "running", "Working out what to build")
        _ev(rid, "analyse", "done", "Plan ready", {"will_ask": _will_ask(r)})

        stage = "specify"
        _ev(rid, "specify", "running", "The model is writing the specification (usually about a minute)")
        spec, provider, model = await agent_service.generate_spec(r)
        agent = dbm.create_agent(rid, spec, provider, model)
        _ev(rid, "specify", "done", f"Specification written: {spec['agent_name']}",
            {"agent_id": agent["id"], "name": spec["agent_name"], "purpose": spec["purpose"], "capabilities": spec["capabilities"],
             "tools": spec.get("required_tools", []), "safety": spec["safety_restrictions"], "model": model})

        stage = "test"
        total = len(spec.get("test_scenarios") or [])
        _ev(rid, "test", "running", f"Running {min(total, 3)} test scenario(s)", {"total": min(total, 3)})

        if is_structured(spec):
            cust0 = dbm.get_customer(r["customer_id"]) if r.get("customer_id") else None
            plugin = repo_gen.company_slug((cust0 or {}).get("slug") or dbm.slugify(r["company_name"]))

            async def on_progress(rep, attempt):
                bad = [c for c in rep["checks"] if c["ok"] is False]
                ran = rep["ran_code"]
                _ev(rid, "test", "progress", "Code checks" + (f" (attempt {attempt + 1})" if attempt else ""),
                    {"name": "Code checks", "passed": not bad, "ms": None, "skipped": not ran and bool(rep.get("reason")),
                     "reason": (rep["reason"] if not ran and rep.get("reason") else f"{len(rep['checks']) - len(bad)} of {len(rep['checks'])} checks passed")
                     + ("; failing: " + "; ".join(c["name"] for c in bad[:4]) if bad else "")})
            agent, report = await check_code(agent, r, plugin, on_progress)
            spec = agent["spec"]
            if any(c["ok"] is False for c in report["checks"]):
                _ev(rid, "test", "failed", "The generated code did not pass its own checks. Review the specification before delivery.", {"passed": 0, "total": len(report["checks"])})
                return

        async def one(res, i, n, ms):
            _ev(rid, "test", "progress", f"Scenario {i + 1} of {n}: {res['name']}",
                {"name": res["name"], "passed": res["passed"], "reason": res["reason"], "ms": ms})
        ev = await agent_service.evaluate(agent, on_result=one)
        passed = sum(x["passed"] for x in ev["results"])
        n = len(ev["results"])
        if n == 0 or passed < n:
            _ev(rid, "test", "failed", f"{passed} of {n} checks passed. Review the specification before delivery.", {"passed": passed, "total": n})
            return
        _ev(rid, "test", "done", f"All {n} checks passed", {"passed": passed, "total": n})

        stage = "package"
        _ev(rid, "package", "running", "Laying out the files that would ship")
        cust = dbm.get_customer(r["customer_id"]) if r.get("customer_id") else None
        slug = (cust or {}).get("slug") or dbm.slugify(r["company_name"])
        owner = os.environ.get("GITHUB_OWNER") or "owner"
        files = repo_gen.repo_files({"company_name": r["company_name"], "slug": slug}, {"owner": owner, "name": f"nexara-{slug}"},
                                    [{"slug": repo_gen.plugin_slug(spec), "spec": spec}])
        listing = [{"path": p, "bytes": len(c.encode())} for p, c in sorted(files.items())]
        _ev(rid, "package", "done", f"{len(listing)} files ready", {"files": listing})
        _ev(rid, "ready", "done", "Ready for review. Nothing has been sent anywhere yet.", {"agent_id": agent["id"]})
    except NotConfigured as e:
        _ev(rid, stage, "failed", str(e)[:300])
    except UpstreamError as e:
        _ev(rid, stage, "failed", f"The model call failed: {str(e)[:300]}")
    except Exception as e:                                                   # never leave a build spinning
        _ev(rid, stage, "failed", f"Unexpected error: {type(e).__name__}: {str(e)[:200]}")
