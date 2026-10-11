"""Spec generation, the LangGraph agent loop, and prototype evaluation."""
import asyncio
import json
import time
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import ValidationError

from . import database as dbm
from . import providers
from .models import AgentSpec
from .providers import NotConfigured, UpstreamError
from .tools import REGISTRY, run_tool

SPEC_SYSTEM = (
    "You are a senior AI solutions engineer at Nexara AI. Convert a client's business requirement into a precise, "
    "safe AI agent specification. Registered tools available to agents: "
    + "; ".join(f"{n} ({d[0]})" for n, d in REGISTRY.items())
    + ". Only list tools in required_tools if the agent genuinely needs them (use [] otherwise). "
    "Write 3 test_scenarios with concrete tasks; add must_contain keywords only when a correct answer must include them "
    "and must_call_tool only when a tool is clearly required. Instructions must be written as a system prompt for the agent."
)


def _req_text(r: dict) -> str:
    return "\n".join(f"{k}: {r[k]}" for k in ("company_name", "industry", "agent_type", "business_problem", "tasks",
                                               "existing_tools", "required_output"))


def _json_prompt() -> str:
    return (SPEC_SYSTEM + "\nRespond with ONLY one JSON object (no prose, no code fences) matching this JSON schema:\n"
            + json.dumps(AgentSpec.model_json_schema()))


async def generate_spec(request: dict) -> tuple[dict, str, str]:
    p = providers.get_provider()
    if (why := providers.missing(p)):
        raise NotConfigured(f"Provider '{p}' is not configured: {why}")
    user = "Client requirement:\n" + _req_text(request)
    last_err = ""
    for attempt in range(2):
        prompt = user + (f"\n\nYour previous output was invalid: {last_err}\nFix it." if last_err else "")
        try:
            if p == "claude_subscription":
                from . import subscription
                raw = await subscription.complete_json(_json_prompt(), prompt)
                spec = AgentSpec.model_validate_json(_strip_fences(raw))
            elif p == "anthropic":
                # forced tool-calling is unsupported on current Claude models, so use JSON-in-prompt + validation
                msg = await providers.chat_model(p).ainvoke([SystemMessage(_json_prompt()), HumanMessage(prompt)])
                spec = AgentSpec.model_validate_json(_strip_fences(_text(msg)))
            else:
                llm = providers.chat_model(p).with_structured_output(AgentSpec, method="function_calling")
                spec = await llm.ainvoke([SystemMessage(SPEC_SYSTEM), HumanMessage(prompt)])
                if isinstance(spec, dict):
                    spec = AgentSpec.model_validate(spec)
            return spec.model_dump(), p, providers.model_name(p)
        except (ValidationError, ValueError) as e:
            last_err = str(e)[:600]
        except NotConfigured:
            raise
        except Exception as e:  # upstream/API failure
            raise UpstreamError(f"{type(e).__name__}: {e}"[:500])
    raise UpstreamError(f"Model returned an invalid specification twice: {last_err}")


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        s = s.rsplit("```", 1)[0]
    return s.strip()


def system_prompt(spec: dict) -> str:
    return (f"{spec['instructions']}\n\nSafety restrictions you must follow:\n"
            + "\n".join(f"- {s}" for s in spec["safety_restrictions"])
            + "\n\nUse the provided tools when they help; never invent tool results.")


def enabled_tools(spec: dict) -> list[str]:
    return [t for t in spec.get("required_tools", []) if t in REGISTRY]


class State(TypedDict):
    messages: Annotated[list, add_messages]


def build_graph(llm, allowed: list[str]):
    """agent -> (tools -> agent)* -> end. Tool execution only ever goes through tools.run_tool."""
    bound = llm.bind_tools([{"name": n, "description": REGISTRY[n][0], "parameters": REGISTRY[n][1]} for n in allowed]) \
        if allowed else llm

    async def agent(state: State):
        return {"messages": [await bound.ainvoke(state["messages"])]}

    def tools(state: State):
        out = []
        for tc in state["messages"][-1].tool_calls:
            res = run_tool(tc["name"], tc["args"]) if tc["name"] in allowed else \
                {"ok": False, "error": f"tool '{tc['name']}' is not enabled for this agent"}
            out.append(ToolMessage(content=json.dumps(res), tool_call_id=tc["id"], name=tc["name"]))
        return {"messages": out}

    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("tools", tools)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", lambda s: "tools" if s["messages"][-1].tool_calls else END)
    g.add_edge("tools", "agent")
    return g.compile()


def _text(msg) -> str:
    c = msg.content
    if isinstance(c, str):
        return c
    return "".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")


async def _execute(spec: dict, task: str, p: str) -> dict:
    """Returns {response, tool_calls, input_tokens, output_tokens}."""
    allowed = enabled_tools(spec)
    if p == "claude_subscription":
        from . import subscription
        return await subscription.run(system_prompt(spec), task, allowed)
    graph = build_graph(providers.chat_model(p), allowed)
    result = await graph.ainvoke({"messages": [SystemMessage(system_prompt(spec)), HumanMessage(task)]},
                                 {"recursion_limit": 12})
    calls, pending, in_t, out_t = [], {}, 0, 0
    for m in result["messages"]:
        if isinstance(m, AIMessage):
            um = m.usage_metadata or {}
            in_t += um.get("input_tokens", 0)
            out_t += um.get("output_tokens", 0)
            for tc in m.tool_calls:
                pending[tc["id"]] = {"tool": tc["name"], "args": tc["args"]}
                calls.append(pending[tc["id"]])
        elif isinstance(m, ToolMessage):
            r = json.loads(m.content)
            pending[m.tool_call_id].update(ok=r["ok"], result=r.get("output"), error=r.get("error"))
    return {"response": _text(result["messages"][-1]), "tool_calls": calls, "input_tokens": in_t, "output_tokens": out_t}


async def run_agent(agent: dict, task: str, kind: str = "run") -> dict:
    """Run + persist an execution-log row. Raises NotConfigured; other failures are logged with status=error."""
    p = providers.get_provider()
    if (why := providers.missing(p)):
        raise NotConfigured(f"Provider '{p}' is not configured: {why}")
    t0 = time.perf_counter()
    out, err = {}, None
    try:
        out = await _execute(agent["spec"], task, p)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:800]
    ms = int((time.perf_counter() - t0) * 1000)
    return dbm.save_run(agent["id"], kind, task, p, out.get("model") or providers.model_name(p), "error" if err else "success",
                        out.get("response", ""), ms, out.get("input_tokens"), out.get("output_tokens"),
                        out.get("tool_calls", []), err)


def check_scenario(sc: dict, run: dict) -> tuple[bool, str]:
    """Deterministic prototype checks only."""
    if run["status"] != "success":
        return False, f"execution error: {run['error']}"
    if not run["response"].strip():
        return False, "empty response"
    low = run["response"].lower()
    for kw in sc.get("must_contain") or []:
        if kw.lower() not in low:
            return False, f"response missing expected text '{kw}'"
    if sc.get("must_call_tool") and not any(c["tool"] == sc["must_call_tool"] and c.get("ok") for c in run["tool_calls"]):
        return False, f"expected successful call to tool '{sc['must_call_tool']}'"
    return True, "all deterministic checks passed"


async def evaluate(agent: dict, max_scenarios: int = 3) -> dict:
    results = []
    for sc in agent["spec"]["test_scenarios"][:max_scenarios]:
        run = await run_agent(agent, sc["task"], kind="test")
        ok, why = check_scenario(sc, run)
        results.append({"name": sc["name"], "expected_behavior": sc["expected_behavior"], "task": sc["task"],
                        "actual": run["response"] or run["error"] or "", "passed": ok, "reason": why,
                        "run_id": run["id"], "tool_calls": run["tool_calls"]})
    rate = sum(r["passed"] for r in results) / len(results) if results else 0.0
    payload = {"agent_id": agent["id"], "pass_rate": rate, "results": results,
               "note": "Basic prototype checks (non-empty, keywords, tool use). Not a comprehensive model evaluation."}
    saved = dbm.save_eval(agent["id"], rate, payload)
    return {**payload, "id": saved["id"]}
