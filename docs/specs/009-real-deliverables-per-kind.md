---
id: 009
title: Deliver real skills, tools, MCP servers and plugins, verified by running them
status: approved
owner: saransh21122112
---

# 009 - Real deliverables per kind

## Problem
Every request, whatever its kind, was delivered as one read-only subagent plus a forwarding skill. An "MCP server" had no server, a "tool" no script, a "plugin" was a wrapper; `input_schema`, `output_schema`, `required_tools` and `data_sources` never reached the delivered files; nothing was ever executed. The HRS "Aurora MCP server" request produced prose that tells Claude to call tools that do not exist.

## Goals / Non-goals
- Goal: each kind is delivered as what it is (see contract), verified by running it, and proven by 14 local end-to-end builds.
- Goal: model-written code is limited to small handler functions inside vetted Nexara templates (blocks); protocol, SQL safety, config, audit, HTTP and file access are Nexara code with their own tests.
- Non-goal (v1): write-capable tools or MCP tools, non-Python runtimes, hooks, hosting servers for customers, automatic delivery.

## Contract per kind
- **Skill**: `skills/<name>/SKILL.md` that does the job itself: description with trigger cues, `argument-hint`, numbered workflow, output format, edge cases and refusal rules, optional `references/*.md`. `allowed-tools` read-only by default.
- **Tool**: `tools/<name>/<name>.py`, Python 3 stdlib only, JSON in (stdin) / JSON out, exit codes, `--selftest`, unit tests; a skill with `allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/tools/<name>/<name>.py:*)` tells Claude how to call it.
- **MCP server**: `servers/<name>/server.py` + `tools.py` + `config.example.json` + README + tests, shared `lib/` blocks, and plugin-root `.mcp.json` using `${CLAUDE_PLUGIN_ROOT}`; read-only; a skill explains when to use the tools.
- **Plugin**: several of the above, one README.
Every delivered repo's README is written for its kind (install, configure, permissions, test, remove). Secrets only via env variable names in config, never values in files.

## Verified facts from the spike (real Claude Code 2.1, not assumed)
1. A plugin-root `.mcp.json` with `{"mcpServers": {"echo": {"command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/servers/echo/server.py"]}}}` is picked up; the server shows as `plugin:<plugin>:<server>`, status `connected`, with a hand-written stdlib server.
2. Tool names are `mcp__plugin_<plugin-name>_<server>__<tool>` and hyphens in the plugin name are preserved.
3. In non-interactive runs (`claude -p`), MCP tool calls need `--allowedTools mcp__plugin_<plugin>_<server>__<tool>`; without it the call is refused.
4. A skill with `allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/tools/x/x.py:*)` runs the script, and `${CLAUDE_PLUGIN_ROOT}` expands.
5. `claude plugin validate` passes; `--strict` only objects to the missing `version`, which we omit on purpose (versioned by commit). Validation therefore uses `--json` and fails on any error or any warning other than `version`.
6. Skills are namespaced `<plugin>:<skill>`.

## Behaviour
1. Specs carry optional structure (`kind`, `skills`, `subagents`, `scripts`, `mcp_servers`); specs without it generate exactly the old layout (plus the version bump).
2. Handler source is statically checked (import allowlist, banned calls and attributes, fixed signature, size caps) before anything is written; failures get one automatic repair round.
3. Generated code runs only in a sandbox (isolated interpreter, empty environment, temp directory, CPU/memory/file/process limits, wall-clock timeout, no non-loopback network), only when `NEXARA_RUN_GENERATED_CODE=1`; a build that could not run its checks says so and is never shown as verified.
4. The materialized plugin must pass `claude plugin validate` (rule in fact 5) when the CLI exists.
5. `GENERATOR_VERSION` becomes 4; existing repos show "behind" and receive the new layout as a pull request.
6. Staff see the generated files, each check's result and a verified / not verified badge; delivering an unverified code artifact needs an extra confirmation.

## Acceptance criteria
| # | Criterion | Test |
|---|-----------|------|
| 2 | static gate accepts good handlers, rejects every escape attempt | `tests/artifacts/test_validate.py` |
| 3 | sandbox limits and isolation | `tests/artifacts/test_sandbox.py` |
| blocks | MCP runtime, SQL guard, db, audit, redact, http, fs, config, cli | `tests/artifacts/test_nx_*.py` |
| 1,4,5 | materialized layout per kind, legacy output unchanged, real CLI validation | `tests/artifacts/test_materialize.py` |
| 6 | verified badge and extra confirm | `tests/test_deliverables_api.py` |
| e2e | 14 real builds graded | `docs/evals/REPORT.md` (local, model-dependent) |

## Edge cases considered
Handlers that import by string or via `__builtins__`; infinite loops, memory bombs, fork bombs; env leakage; huge outputs; SQL comment/CTE/subquery/semicolon tricks; path traversal and symlinks; redirects to non-allowlisted hosts; protocol garbage, oversize lines, unknown methods; a server restarted mid-session; secrets in config; very long or hostile names.

## Security / privacy / public-content check
Model-written code never runs inside the Nexara process. The public site still reveals no mechanics. Delivered artifacts are read-only by construction in v1.

## Rollout
`NEXARA_RUN_GENERATED_CODE` stays unset on Render. Existing delivered repos change only through a reviewed pull request.
