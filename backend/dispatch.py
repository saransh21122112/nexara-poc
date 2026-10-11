"""Customer handover: a runnable package (HTTP API + MCP server) and a DRAFT email. Nothing is ever sent."""
import json
import re
from pathlib import Path

from .export import _slug, _zip

TOOLS_SRC = (Path(__file__).parent / "tools.py").read_text()
SAMPLE_DATA = (Path(__file__).resolve().parent.parent / "data" / "sample_business_data.json").read_text()

RUNTIME = '''"""Agent runtime: runs spec.json against the customer's own LLM key, with the tools in tools.py only."""
import json
import os
from pathlib import Path

from tools import REGISTRY, run_tool

SPEC = json.loads((Path(__file__).parent / "spec.json").read_text())
MAX_TURNS = 6


def system_prompt() -> str:
    return (SPEC["instructions"] + "\\n\\nSafety restrictions you must follow:\\n"
            + "\\n".join("- " + s for s in SPEC["safety_restrictions"])
            + "\\n\\nUse the provided tools when they help; never invent tool results.")


def enabled_tools() -> list[str]:
    return [t for t in SPEC.get("required_tools", []) if t in REGISTRY]


def provider() -> str:
    p = os.environ.get("AGENT_PROVIDER") or ("anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai")
    if p == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    if p == "openai" and not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Set ANTHROPIC_API_KEY or OPENAI_API_KEY")
    return p


def run(task: str) -> dict:
    allowed, calls = enabled_tools(), []
    if provider() == "anthropic":
        import anthropic
        client = anthropic.Anthropic()
        tools = [{"name": n, "description": REGISTRY[n][0], "input_schema": REGISTRY[n][1]} for n in allowed]
        msgs = [{"role": "user", "content": task}]
        for _ in range(MAX_TURNS):
            kw = {"tools": tools} if tools else {}
            r = client.messages.create(model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"), max_tokens=2048,
                                       system=system_prompt(), messages=msgs, **kw)
            uses = [b for b in r.content if b.type == "tool_use"]
            if not uses:
                return {"response": "".join(b.text for b in r.content if b.type == "text"), "tool_calls": calls}
            msgs.append({"role": "assistant", "content": r.content})
            results = []
            for u in uses:
                out = run_tool(u.name, u.input) if u.name in allowed else {"ok": False, "error": "tool not enabled"}
                calls.append({"tool": u.name, "args": u.input, "ok": out["ok"]})
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(out)})
            msgs.append({"role": "user", "content": results})
    else:
        import openai
        client = openai.OpenAI()
        tools = [{"type": "function", "function": {"name": n, "description": REGISTRY[n][0], "parameters": REGISTRY[n][1]}}
                 for n in allowed]
        msgs = [{"role": "system", "content": system_prompt()}, {"role": "user", "content": task}]
        for _ in range(MAX_TURNS):
            kw = {"tools": tools} if tools else {}
            m = client.chat.completions.create(model=os.environ.get("OPENAI_MODEL", "gpt-4o"), messages=msgs, **kw).choices[0].message
            if not m.tool_calls:
                return {"response": m.content or "", "tool_calls": calls}
            msgs.append(m)
            for tc in m.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except ValueError:
                    args = {}
                out = run_tool(tc.function.name, args) if tc.function.name in allowed else {"ok": False, "error": "tool not enabled"}
                calls.append({"tool": tc.function.name, "args": args, "ok": out["ok"]})
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(out)})
    return {"response": "Stopped: too many tool-use turns.", "tool_calls": calls}
'''

SERVER = '''"""HTTP API. Start: uvicorn server:app --host 0.0.0.0 --port 8000 (AGENT_API_KEY is required)."""
import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

import runtime

API_KEY = os.environ.get("AGENT_API_KEY", "")
if len(API_KEY) < 16:
    raise SystemExit("Set AGENT_API_KEY (at least 16 characters) - callers must send it as 'Authorization: Bearer <key>'.")

app = FastAPI(title=runtime.SPEC["agent_name"])


def auth(authorization: str = Header(default="")):
    if not hmac.compare_digest(authorization, "Bearer " + API_KEY):
        raise HTTPException(401, "Invalid or missing API key")


class RunIn(BaseModel):
    task: str = Field(min_length=1, max_length=8000)


@app.get("/health")
def health():
    return {"status": "ok", "agent": runtime.SPEC["agent_name"]}


@app.get("/spec", dependencies=[Depends(auth)])
def spec():
    return runtime.SPEC


@app.post("/run", dependencies=[Depends(auth)])
def run(body: RunIn):
    try:
        return runtime.run(body.task)
    except RuntimeError as e:  # missing LLM key etc.
        raise HTTPException(503, str(e))
'''

MCP = '''"""MCP server (stdio) so Claude Desktop, Cursor or any MCP client can use this agent as a tool."""
from mcp.server.mcpserver import MCPServer

import runtime

mcp = MCPServer(runtime.SPEC["agent_name"], instructions=runtime.SPEC["purpose"])


@mcp.tool()
def ask_agent(task: str) -> str:
    """Give the agent a task; returns its answer. See the agent purpose in the server instructions."""
    return runtime.run(task)["response"]


if __name__ == "__main__":
    mcp.run()
'''


def readme(spec: dict, company: str, slug: str) -> str:
    tools = ", ".join(spec["required_tools"]) or "none"
    return f"""# {spec['agent_name']} - delivered to {company}

**Purpose:** {spec['purpose']}

This package was prepared by Nexara AI. It is a **starter deployment**: review the instructions in `spec.json`,
connect your real systems (step 4) and test it before relying on it in production.

## 1. Run it
```bash
cp .env.example .env     # set AGENT_API_KEY (>=16 chars, you choose it) and ONE LLM key
docker compose up --build -d          # API on http://localhost:8000
# or without Docker: pip install -r requirements.txt && uvicorn server:app --port 8000
```

## 2. Call it over HTTP (Zapier, n8n, Slack bots, your own agents ...)
```bash
curl -s localhost:8000/run -H "Authorization: Bearer $AGENT_API_KEY" -H "Content-Type: application/json" \\
  -d '{{"task": "your task here"}}'
```
Returns `{{"response": "...", "tool_calls": [...]}}`. `GET /spec` shows the agent definition, `GET /health` is unauthenticated.

## 3. Use it from an MCP client (Claude Desktop, Cursor, other agents)
Add to the client's MCP config (adjust the path; needs `pip install -r requirements.txt`):
```json
{{"mcpServers": {{"{slug}": {{"command": "python", "args": ["/path/to/mcp_server.py"],
  "env": {{"ANTHROPIC_API_KEY": "..."}}}}}}}}
```

## 4. Connect your own systems
The agent can only use the tools listed in `spec.json` -> `required_tools` ({tools}), implemented in `tools.py`.
`lookup_business_data` currently reads the **sample** file `data/business_data.json` - replace its body with a call to your
CRM/database/API. Add new tools by registering them in `REGISTRY` (and `required_tools` in `spec.json`). Keep arguments
validated and never use `eval` or shell execution in a tool.

## 5. Safety restrictions built into this agent
{chr(10).join('- ' + s for s in spec['safety_restrictions'])}

## 6. Success criteria we tested against
{chr(10).join('- ' + s for s in spec['success_criteria'])}

Nexara ran basic prototype checks (non-empty reply, expected keywords, expected tool use). That is not a comprehensive
evaluation - please test with your own data.
"""


def package(spec: dict, company: str) -> bytes:
    slug = _slug(spec["agent_name"])
    tools = TOOLS_SRC.replace('DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "sample_business_data.json"',
                              'DATA_FILE = Path(__file__).resolve().parent / "data" / "business_data.json"  # SAMPLE data: replace with your system')
    assert tools != TOOLS_SRC
    return _zip({
        "README.md": readme(spec, company, slug), "spec.json": json.dumps(spec, indent=2),
        "runtime.py": RUNTIME, "tools.py": tools, "server.py": SERVER, "mcp_server.py": MCP,
        "data/business_data.json": SAMPLE_DATA,
        "requirements.txt": "fastapi\nuvicorn\nanthropic\nopenai\nmcp>=2\n",
        ".env.example": "AGENT_API_KEY=\nANTHROPIC_API_KEY=\nOPENAI_API_KEY=\n# AGENT_PROVIDER=anthropic|openai\n# ANTHROPIC_MODEL=claude-sonnet-5-5\n# OPENAI_MODEL=gpt-4o\n",
        "Dockerfile": 'FROM python:3.12-slim\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\n'
                      'COPY . .\nRUN useradd -m app\nUSER app\nEXPOSE 8000\nCMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]\n',
        "docker-compose.yml": 'services:\n  agent:\n    build: .\n    ports: ["8000:8000"]\n    env_file: .env\n    restart: unless-stopped\n',
    })


def email_draft(spec: dict, req: dict) -> dict:
    """A DRAFT for a human to review and send. Contains no secrets."""
    name = re.sub(r"\s+", " ", spec["agent_name"])
    body = f"""Hi,

Thanks for working with Nexara AI. Your agent "{name}" is ready for you to review and connect.

What it does: {spec['purpose']}

What's attached: a package you run yourself (Docker or Python). It exposes
 - an HTTP endpoint you can call from your workspace tools or your own agents, and
 - an MCP server, so Claude Desktop, Cursor or other MCP clients can use it directly.
The README inside walks through setup in a few minutes.

To get started you'll need an API key for an AI provider (Anthropic or OpenAI) and to choose your own AGENT_API_KEY.
We never send keys in this email, and the agent only uses the tools listed in the README.

Before using it on real work, please connect it to your own systems (README step 4) and test it with your data.
We're happy to do a short call to walk through it.

Best regards,
Nexara AI team
"""
    return {"to": req["contact_email"], "subject": f"Your AI agent is ready: {name}", "body": body}
