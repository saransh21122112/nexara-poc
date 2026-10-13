"""Turns a structured AgentSpec into the files of a real Claude Code plugin: skills, subagents, tool scripts, MCP servers.

Paths are relative to the plugin root (= the customer repo root). The vetted blocks are copied verbatim into `lib/`. Nothing here runs
generated code; see sandbox.py for that."""
import ast
import json
import re
from pathlib import Path

from . import validate

BLOCK_DIR = Path(__file__).parent / "blocks"
SKILL_BASE_TOOLS = ("Read", "Grep", "Glob")


def is_structured(spec: dict) -> bool:
    return bool(spec.get("skills") or spec.get("subagents") or spec.get("scripts") or spec.get("mcp_servers"))


# ------------------------------------------------------------------ small helpers
def _repo():
    from .. import repo_gen          # lazy: repo_gen imports this module lazily too
    return repo_gen


def _front(**kv) -> str:
    return "---\n" + "\n".join(f"{k}: {v}" for k, v in kv.items() if v not in (None, "")) + "\n---\n\n"


def _desc(text: str, limit: int = 1000) -> str:
    rg = _repo()
    return "\n" + rg._fold(rg._cut(rg._oneline(text, limit), limit))


def _bullets(items) -> str:
    rg = _repo()
    return "\n".join(f"- {rg._oneline(i, 300)}" for i in items)


def _blocks_in(*sources) -> set[str]:
    """Blocks a piece of source imports (nx_x), plus the blocks those blocks import."""
    found = set()
    for src in sources:
        try:
            tree = ast.parse(src or "")
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            found |= {m for m in mods if m in validate.NX_MODULES}
    todo = list(found)
    while todo:
        m = todo.pop()
        for dep in _blocks_in((BLOCK_DIR / f"{m}.py").read_text(encoding="utf-8")) - found:
            found.add(dep)
            todo.append(dep)
    return found


def _nargs(src: str, entry: str) -> int:
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == entry)
    return len(fn.args.posonlyargs) + len(fn.args.args)


def _nested(keys_values: list[tuple[str, object]]) -> dict:
    out: dict = {}
    for key, val in keys_values:
        d = out
        parts = key.split(".")
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = val
    return out


HEADER = '''import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[2] / "lib")]   # Nexara building blocks live in <plugin>/lib

'''


def tool_ref(plugin: str, server: str, tool: str) -> str:
    return f"mcp__plugin_{plugin}_{server}__{tool}"


def script_pattern(name: str) -> str:
    return f"Bash(python3 ${{CLAUDE_PLUGIN_ROOT}}/tools/{name}/{name}.py:*)"


# ------------------------------------------------------------------ skills and subagents
def _uses_section(spec: dict, plugin: str, uses: list[str]) -> tuple[str, list[str]]:
    scripts = {s["name"]: s for s in spec.get("scripts", [])}
    servers = {s["name"]: s for s in spec.get("mcp_servers", [])}
    tools, lines = [], []
    for u in uses:
        if u in scripts:
            s = scripts[u]
            tools.append(script_pattern(u))
            lines.append(f"- **{u}** - {s['description'].strip()}\n  Run: `python3 ${{CLAUDE_PLUGIN_ROOT}}/tools/{u}/{u}.py --json '<JSON input>'` "
                         f"(add `--text-file <path>` to pass a text/CSV file as `text`). It prints one JSON line: `{{\"ok\": true, \"result\": ...}}` or `{{\"ok\": false, \"error\": ...}}`."
                         + (f"\n  Input: `{json.dumps(s['inputs'], ensure_ascii=False)}`" if s.get("inputs") else ""))
            continue
        server, _, tool = u.partition("/")
        chosen = [t for t in servers[server]["tools"] if not tool or t["name"] == tool]
        for t in chosen:
            tools.append(tool_ref(plugin, server, t["name"]))
            lines.append(f"- **{t['name']}** (server `{server}`, read-only) - {t['description'].strip()}")
    section = "## Tools you can use\n" + "\n".join(lines) + "\n\nIf a tool reports that it is not configured, tell the user to follow the setup steps in its README. Never work around a refusal.\n" if lines else ""
    return section, tools


def skill_files(spec: dict, plugin: str) -> dict[str, str]:
    out = {}
    guard = _bullets(spec.get("safety_restrictions") or [])
    for s in spec.get("skills", []):
        section, used = _uses_section(spec, plugin, s.get("uses") or [])
        base = s.get("allowed_tools") or (SKILL_BASE_TOOLS if not used else ())
        tools = list(dict.fromkeys([*base, *used]))
        fm = _front(name=s["name"], description=">-" + _desc(s["description"], 1000), **{"argument-hint": json.dumps(s["argument_hint"]) if s.get("argument_hint") else None},
                    **{"allowed-tools": ", ".join(tools) if tools else None})
        body = s["body"].strip() + "\n\n" + (section + "\n" if section else "") + (f"## Guardrails\n{guard}\n" if guard else "")
        out[f"skills/{s['name']}/SKILL.md"] = fm + body
        for r in s.get("references", []):
            out[f"skills/{s['name']}/references/{r['name']}.md"] = r["content"].strip() + "\n"
    return out


def wrapper_skills(spec: dict, plugin: str) -> dict[str, str]:
    """A skill for every script/server no explicit skill uses, so each component is reachable as /<plugin>:<name>."""
    covered = {u.partition("/")[0] for s in spec.get("skills", []) for u in s.get("uses") or []}
    out = {}
    guard = _bullets(spec.get("safety_restrictions") or [])
    for kind, items in (("script", spec.get("scripts", [])), ("server", spec.get("mcp_servers", []))):
        for c in items:
            if c["name"] in covered:
                continue
            section, used = _uses_section(spec, plugin, [c["name"]])
            what = "Run the tool below with the user's input, then explain the result plainly." if kind == "script" else "Use the read-only tools below to answer from the connected system. Quote what they return; never guess."
            fm = _front(name=c["name"], description=">-" + _desc(f"{c['description'].strip()} Use when the user asks for this.", 400),
                        **{"argument-hint": json.dumps("what you need, or a file path")}, **{"allowed-tools": ", ".join(used)})
            out[f"skills/{c['name']}/SKILL.md"] = fm + f"{what}\n\nRequest: $ARGUMENTS\n\nIf the request is empty, ask what to run it on instead of guessing.\n\n{section}\n" + (f"## Guardrails\n{guard}\n" if guard else "")
    return out


def subagent_files(spec: dict) -> dict[str, str]:
    out = {}
    rules = _bullets(spec.get("safety_restrictions") or [])
    for a in spec.get("subagents", []):
        fm = _front(name=a["name"], description=">-" + _desc(a["description"], 600), tools=", ".join(a.get("tools") or SKILL_BASE_TOOLS), model="inherit")
        out[f"agents/{a['name']}.md"] = fm + a["instructions"].strip() + (f"\n\n## Safety restrictions\n{rules}\n" if rules else "\n") + (
            "\n## Working in Claude Code\n- You can read files with Read, Grep and Glob. You cannot edit files or run commands: describe changes, never claim to have made them.\n"
            "- Never invent file contents or results. If something is missing, say what you need.\n"
            "- Do not open secret files (`.env*`, keys, credential or token files); report that they exist without quoting them.\n")
    return out


# ------------------------------------------------------------------ tool scripts
def _example_rows(examples):
    return [{k: v for k, v in e.items() if v not in (None, [], "")} for e in examples]


def script_files(sc: dict) -> dict[str, str]:
    n = sc["name"]
    examples = _example_rows(sc["examples"])
    fixtures = sc.get("fixtures") or {}
    footer = f'''

EXAMPLES = json.loads({json.dumps(json.dumps(examples, ensure_ascii=False))})


def _selftest():
    """Runs the documented examples through handle() and checks what they promise."""
    results = []
    for i, ex in enumerate(EXAMPLES, 1):
        try:
            text, failed = json.dumps(handle(dict(ex["args"])), ensure_ascii=False, default=str).lower(), False
        except nx_cli.UserError as e:
            text, failed = str(e).lower(), True
        want_err = (ex.get("expect_error") or "").lower()
        ok = (failed and want_err in text) if want_err else not failed
        ok = ok and all(s.lower() in text for s in ex.get("expect_contains", [])) and not any(s.lower() in text for s in ex.get("expect_not_contains", []))
        results.append((f"example {{i}}", ok))
    return results


if __name__ == "__main__":
    nx_cli.run(handle, selftest=_selftest)
'''
    code = '#!/usr/bin/env python3\n"""' + n + ": generated by Nexara AI. Reads one JSON object (stdin, --json or --file) and prints one JSON line. --selftest runs the examples.\"\"\"\n" \
        + "import json\n" + HEADER + "import nx_cli  # noqa: E402\n\n" + sc["handler_source"].strip() + "\n" + footer
    tests = {"kind": "script", "name": n, "entry": f"{n}.py", "fixtures": fixtures, "config_example": {}, "examples": examples}
    readme = f"""# {n}

{sc['description'].strip()}

A small Python 3 program (standard library only). Nothing is installed and nothing leaves your machine.

## Use
```
python3 tools/{n}/{n}.py --json '{json.dumps(examples[0]['args'], ensure_ascii=False)}'
```
Input is one JSON object (stdin, `--json '<object>'` or `--file <path>`); `--text-file <path>` passes a text/CSV file as the `text` field.
Output is one JSON line: `{{"ok": true, "result": ...}}`, or `{{"ok": false, "error": "..."}}` with exit code 2 for bad input.
{("Input fields: `" + json.dumps(sc['inputs'], ensure_ascii=False) + "`") if sc.get('inputs') else ""}

## Check it
```
python3 tools/{n}/{n}.py --selftest
python3 -m unittest discover -s tools/{n}
```

## Remove it
Delete the `tools/{n}` folder (and the skill that uses it).
"""
    unit = _unittest("script", n)
    base = f"tools/{n}"
    return {f"{base}/{n}.py": code, f"{base}/tests.json": json.dumps(tests, indent=2, ensure_ascii=False) + "\n", f"{base}/test_{n.replace('-', '_')}.py": unit, f"{base}/README.md": readme}


def _unittest(kind: str, n: str) -> str:
    return f'''"""Runs the examples for {n} against the real {kind} (Nexara test kit)."""
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class Examples(unittest.TestCase):
    def test_examples_pass(self):
        kit = HERE.parents[1] / "lib" / "nx_testkit.py"
        p = subprocess.run([sys.executable, str(kit), str(HERE / "tests.json")], capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
'''


# ------------------------------------------------------------------ MCP servers
def server_files(srv: dict, plugin: str) -> dict[str, str]:
    n = srv["name"]
    base = f"servers/{n}"
    files = {}
    mods = []
    for t in srv["tools"]:
        mod = f"tool_{t['name']}"
        mods.append((t, mod, _nargs(t["handler_source"], "handle")))
        files[f"{base}/{mod}.py"] = HEADER + t["handler_source"].strip() + "\n"
    files[f"{base}/setup_ctx.py"] = HEADER + srv.get("setup_source", "def setup(config):\n    return {}").strip() + "\n"
    tool_lines = "\n".join(
        f"    nx_mcp.Tool({t['name']!r}, {t['description']!r}, {json.dumps(t['input_schema'])}, "
        + (f"lambda args: {mod}.handle(args, _ctx())" if k == 2 else f"lambda args: {mod}.handle(args)") + ", read_only=True)," for t, mod, k in mods)
    imports = "\n".join(f"import {mod}  # noqa: E402" for _, mod, _ in mods)
    files[f"{base}/server.py"] = f'''#!/usr/bin/env python3
"""{n}: read-only MCP server over stdio, generated by Nexara AI. Setup: see README.md in this folder."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "lib")]

import nx_config  # noqa: E402
import nx_mcp  # noqa: E402
import setup_ctx  # noqa: E402
{imports}

_STATE = {{}}


def _ctx():
    """Loads the configuration on first use, so a missing config produces a helpful tool error instead of a server that will not start."""
    if "ctx" not in _STATE:
        try:
            cfg = nx_config.load(default=str(HERE / "config.json"))
            ctx = dict(setup_ctx.setup(cfg) or {{}})
        except nx_config.ConfigError as e:
            raise nx_mcp.ToolError(f"{{e}} Setup steps: servers/{n}/README.md") from None
        except nx_mcp.ToolError:
            raise
        except Exception as e:                       # noqa: BLE001  details to the log, a short message to the model
            print(f"[{n}] setup failed: {{type(e).__name__}}: {{e}}", file=sys.stderr)
            raise nx_mcp.ToolError(f"The server could not start with this configuration ({{type(e).__name__}}). Check servers/{n}/README.md and the log.") from None
        ctx["config"] = cfg
        _STATE["ctx"] = ctx
    return _STATE["ctx"]


TOOLS = [
{tool_lines}
]

if __name__ == "__main__":
    nx_mcp.Server({n!r}, "1.0.0", TOOLS, instructions={(srv.get('instructions') or srv['description']).strip()!r}).serve()
'''
    cfg_example = _nested([(c["key"], c.get("example", "")) for c in srv.get("config_schema", [])])
    fx = srv.get("fixtures") or {}
    files[f"{base}/config.example.json"] = json.dumps(cfg_example, indent=2, ensure_ascii=False) + "\n"
    tests = {"kind": "mcp", "name": n, "entry": "server.py", "fixtures": fx, "config_example": cfg_example,
             "tools": [{"name": t["name"], "examples": _example_rows(t["examples"])} for t in srv["tools"]]}
    files[f"{base}/tests.json"] = json.dumps(tests, indent=2, ensure_ascii=False) + "\n"
    files[f"{base}/test_server.py"] = _unittest("server", n)
    pk = srv.get("packages") or []
    if pk:
        files[f"{base}/requirements.txt"] = "\n".join(pk) + "\n"
    cfg_doc = "\n".join(f"- `{c['key']}`{' (secret: the NAME of an environment variable)' if c.get('secret') or c['key'].endswith('_env') else ''} - {c['description'].strip()}" for c in srv.get("config_schema", [])) or "- (no settings)"
    tool_doc = "\n".join(f"- `{t['name']}` - {t['description'].strip()}" for t in srv["tools"])
    files[f"{base}/README.md"] = f"""# {n} (MCP server)

{srv['description'].strip()}

Read-only: it can look things up, never change anything. Every call goes through the checks in `lib/` (argument validation, size limits{', SQL guard' if 'sqlguard' in srv.get('blocks', []) else ''}).

## Tools
{tool_doc}

## Set it up
1. Copy `config.example.json` to `config.json` in this folder (or keep it anywhere and set the `NX_CONFIG` environment variable to its path). Plugin updates can replace this folder, so a copy outside it survives updates.
2. Edit the settings:
{cfg_doc}
3. Secrets never go in the file. Settings that end in `_env` hold the NAME of an environment variable; set that variable in your shell or Claude Code environment.
{("4. Install the extra package(s): `python3 -m pip install -r servers/" + n + "/requirements.txt`" + chr(10)) if pk else ""}
Claude Code starts the server by itself (declared in the plugin's `.mcp.json`) as `plugin:{plugin}:{n}`. Check with `/mcp`.

## Check it
```
python3 -m unittest discover -s servers/{n}
```
This starts the server on throw-away test data and calls every documented example.

## Remove it
Delete the `servers/{n}` folder and its entry in `.mcp.json`.
"""
    return files


# ------------------------------------------------------------------ whole plugin
def component_names(spec: dict) -> dict[str, set[str]]:
    scripts = {s["name"] for s in spec.get("scripts", [])}
    servers = {s["name"] for s in spec.get("mcp_servers", [])}
    skills = {s["name"] for s in spec.get("skills", [])} | {c for c in scripts | servers if not any(c == u.partition("/")[0] for s in spec.get("skills", []) for u in s.get("uses") or [])}
    return {"skills": skills, "agents": {a["name"] for a in spec.get("subagents", [])}, "tools": scripts, "servers": servers}


def spec_files(spec: dict, plugin: str) -> tuple[dict[str, str], set[str]]:
    """Files for one structured spec (without lib/ and .mcp.json) and the blocks it needs."""
    files = {**skill_files(spec, plugin), **wrapper_skills(spec, plugin), **subagent_files(spec)}
    sources = []
    for sc in spec.get("scripts", []):
        files.update(script_files(sc))
        sources += [sc["handler_source"]]
    for srv in spec.get("mcp_servers", []):
        files.update(server_files(srv, plugin))
        sources += [t["handler_source"] for t in srv["tools"]] + [srv.get("setup_source", "")]
    blocks = _blocks_in(*sources)
    blocks |= {f"nx_{b}" for s in [*spec.get("scripts", []), *spec.get("mcp_servers", [])] for b in s.get("blocks", [])}
    if spec.get("scripts"):
        blocks |= {"nx_cli"}
    if spec.get("mcp_servers"):
        blocks |= {"nx_mcp", "nx_config"}
    if spec.get("scripts") or spec.get("mcp_servers"):
        blocks |= {"nx_testkit"}
    return files, _blocks_in(*[(BLOCK_DIR / f"{b}.py").read_text(encoding="utf-8") for b in blocks]) | blocks


def assemble(specs: list[dict], plugin: str) -> dict[str, str]:
    """All files for a plugin made of several structured specs. Component names must be unique across specs."""
    files: dict[str, str] = {}
    blocks: set[str] = set()
    servers: dict[str, dict] = {}
    for spec in specs:
        part, used = spec_files(spec, plugin)
        for path in part:
            if path in files:
                raise ValueError(f"two agents define the same file: {path}")
        files.update(part)
        blocks |= used
        for s in spec.get("mcp_servers", []):
            servers[s["name"]] = {"command": "python3", "args": [f"${{CLAUDE_PLUGIN_ROOT}}/servers/{s['name']}/server.py"]}
    for b in sorted(blocks):
        files[f"lib/{b}.py"] = (BLOCK_DIR / f"{b}.py").read_text(encoding="utf-8")
    if servers:
        files[".mcp.json"] = json.dumps({"mcpServers": servers}, indent=2) + "\n"
    return files


def listing(spec: dict, plugin: str) -> str:
    """Markdown bullets for the README's 'What's inside' section."""
    names = component_names(spec)
    rg = _repo()
    lines = [f"- `/{plugin}:{s['name']}` (skill) - {rg._first_sentence(s['description'], 150)}" for s in spec.get("skills", [])]
    lines += [f"- `/{plugin}:{n}` (skill for the {'tool' if n in names['tools'] else 'MCP server'} of the same name)" for n in sorted(names["skills"] - {s['name'] for s in spec.get("skills", [])})]
    lines += [f"- `{a['name']}` (subagent) - {rg._first_sentence(a['description'], 150)}" for a in spec.get("subagents", [])]
    lines += [f"- `tools/{s['name']}` (tool script) - {rg._first_sentence(s['description'], 150)}" for s in spec.get("scripts", [])]
    lines += [f"- `servers/{s['name']}` (MCP server, read-only, tools: {', '.join(t['name'] for t in s['tools'])}) - {rg._first_sentence(s['description'], 150)}" for s in spec.get("mcp_servers", [])]
    return "\n".join(lines)
