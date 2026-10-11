"""The build pipeline behind the live view: request -> analyse -> specify -> test -> package -> ready.
Every event is a real thing that happened (a model call finished, a scenario passed or failed, files were laid out). Nothing is pushed to
GitHub here; delivery stays a separate, human-confirmed action and shows up later through the repo events."""
import asyncio
import os

from . import agent_service, database as dbm, repo_gen
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


def _will_ask(r: dict) -> list[str]:
    return [f"Write a {r['agent_type']} specification for {r['company_name']}",
            "Name it, state its purpose and list what it can do",
            "Decide which tools it may use (read-only by default)",
            "Add safety limits and three test scenarios"]


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
