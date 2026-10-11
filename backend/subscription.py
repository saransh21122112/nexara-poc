"""claude_subscription provider: drives the local `claude` CLI (your own `claude login`) via claude-agent-sdk.
Built-in tools are disabled; only our two registered tools are exposed. Local single-user demo only."""
import json
import os

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, ToolResultBlock,
                              ToolUseBlock, UserMessage, create_sdk_mcp_server, query, tool)

from . import providers
from .tools import REGISTRY, run_tool

PREFIX = "mcp__nexara__"


def _options(system: str, allowed: list[str], max_turns: int) -> ClaudeAgentOptions:
    tools = []
    for name in allowed:
        desc, schema, _ = REGISTRY[name]

        @tool(name, desc, schema)
        async def _t(args, _n=name):
            r = run_tool(_n, args)
            return {"content": [{"type": "text", "text": json.dumps(r)}], "is_error": not r["ok"]}
        tools.append(_t)
    model = os.environ.get("CLAUDE_SUBSCRIPTION_MODEL") or None
    return ClaudeAgentOptions(
        system_prompt=system, model=model, max_turns=max_turns,
        tools=[],  # no built-in tools (Bash/Read/Write/...)
        mcp_servers={"nexara": create_sdk_mcp_server("nexara", tools=tools)} if tools else {},
        allowed_tools=[PREFIX + n for n in allowed], setting_sources=[],
        strict_mcp_config=True,  # only our MCP server: hides the account's claude.ai connectors  # ignore user CLAUDE.md/hooks/plugins
        permission_mode="dontAsk")


async def _drive(system: str, prompt: str, allowed: list[str], max_turns: int):
    text, calls, by_id, usage, model = "", [], {}, {}, None
    async for m in query(prompt=prompt, options=_options(system, allowed, max_turns)):
        if isinstance(m, AssistantMessage):
            model = m.model or model
            for b in m.content:
                if isinstance(b, ToolUseBlock):
                    by_id[b.id] = {"tool": b.name.removeprefix(PREFIX), "args": b.input}
                    calls.append(by_id[b.id])
        elif isinstance(m, UserMessage) and isinstance(m.content, list):
            for b in m.content:
                if isinstance(b, ToolResultBlock) and b.tool_use_id in by_id:
                    raw = b.content if isinstance(b.content, str) else "".join(x.get("text", "") for x in b.content or [])
                    try:
                        r = json.loads(raw)
                        by_id[b.tool_use_id].update(ok=r["ok"], result=r.get("output"), error=r.get("error"))
                    except Exception:
                        by_id[b.tool_use_id].update(ok=not b.is_error, result=raw)
        elif isinstance(m, ResultMessage):
            if m.is_error:
                raise providers.UpstreamError(f"claude CLI error: {m.result or m.subtype}")
            text, usage = m.result or "", m.usage or {}
    return text, calls, usage, model


async def run(system: str, task: str, allowed: list[str]) -> dict:
    text, calls, usage, model = await _drive(system, task, allowed, max_turns=8)
    # input = uncached + cache-written + cache-read, so the figure is the real prompt size
    in_tok = sum(usage.get(k, 0) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return {"response": text, "tool_calls": calls, "input_tokens": in_tok if usage else None,
            "output_tokens": usage.get("output_tokens"), "model": model}


async def complete_json(system: str, prompt: str) -> str:
    text, _, _, _ = await _drive(system, prompt, [], max_turns=1)
    return text
