"""Builds the complete file tree of a customer's private repo: a Claude Code plugin marketplace (one plugin per agent) plus
customer docs and setup scripts. Pure functions: (customer, repo, agents) -> {path: text}. Nothing here talks to GitHub."""
import hashlib
import json
import re
import textwrap
from datetime import datetime, timezone

from . import repo_templates as T

# Paths Nexara owns and tracks for conflict detection. CHANGELOG.md is append-only (handled separately) and custom/ is never touched.
UNTRACKED_PREFIXES = ("custom/",)
UNTRACKED_FILES = ("CHANGELOG.md",)


def is_tracked(path: str) -> bool:
    return path not in UNTRACKED_FILES and not path.startswith(UNTRACKED_PREFIXES)


def git_blob_sha(text: str) -> str:
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


def plugin_slug(spec: dict) -> str:
    return re.sub(r"[^a-z0-9]+", "-", spec["agent_name"].lower()).strip("-")[:40].strip("-") or "agent"


def marketplace_name(customer_slug: str) -> str:
    return ("nexara-" + re.sub(r"[^a-z0-9]+", "-", customer_slug.lower()).strip("-"))[:60].strip("-")


def _oneline(text: str, limit: int = 600) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _fold(text: str) -> str:
    """YAML folded block scalar body (safe for any characters, including ones that would break a plain scalar)."""
    return "\n".join("  " + ln for ln in textwrap.wrap(_oneline(text), 88)) or "  agent"


def _fill(template: str, **tokens) -> str:
    """Single pass: substituted values are never rescanned, so customer-supplied text can't inject another token."""
    return re.sub(r"@@([A-Z_]+)@@", lambda m: tokens.get(m.group(1), m.group(0)), template)


# ---------------------------------------------------------------- one plugin (one agent)
def plugin_files(spec: dict, slug: str, company: str) -> dict[str, str]:
    """Files of one plugin, paths relative to the plugin root. No `version` anywhere: Claude Code then versions by commit SHA,
    so every push to the repo is picked up as an update."""
    purpose = _oneline(spec["purpose"])
    caps = "; ".join(_oneline(c, 120) for c in spec["capabilities"][:6])
    rules = "\n".join(f"- {_oneline(s, 300)}" for s in spec["safety_restrictions"])
    agent = f"""---
name: {slug}
description: >-
{_fold(f'{purpose} Capabilities: {caps} Use this agent when the user asks for this kind of work, for example: "{_oneline(spec["capabilities"][0], 100)}".')}
tools: Read, Grep, Glob
model: inherit
---

{spec['instructions'].strip()}

## Safety restrictions
{rules}

## Working in Claude Code
- You can read the user's project with Read, Grep and Glob. You cannot edit files or run commands: describe changes, never claim to have made them.
- Explore before judging: list the structure, then read the files that matter. For a whole-repository task, cover the important entry points first and say plainly which files you did and did not read.
- Never invent file contents, line numbers or results. If something is missing, say what you need.
- Do not open secret files (`.env*`, `*.pem`, `*.key`, SSH keys, credential or token files, password stores). If one exists in the repo, report that it is present and how to handle it, without reading or quoting its contents.
"""
    skill = f"""---
name: {slug}
description: >-
{_fold(f"{purpose} Use when the user asks for this kind of help, or mentions: {caps}.")}
---

# {_oneline(spec['agent_name'], 100)}

{purpose}

When this applies, hand the request to the `{slug}` subagent (Task tool) together with the relevant paths or the whole-repo
scope, then present its findings clearly, ordered by importance. Do not do the agent's work yourself and do not add
conclusions it did not reach.

Success looks like:
{chr(10).join('- ' + _oneline(c, 300) for c in spec['success_criteria'])}
"""
    command = f"""---
description: >-
{_fold(_oneline(purpose, 140))}
argument-hint: What to apply it to (files, folders, a PR, or "whole repo")
---

Use the `{slug}` subagent for the following request. Pass its findings back to the user as they are.

Request: $ARGUMENTS

If the request is empty, ask the user what to apply it to instead of guessing.
"""
    manifest = json.dumps({"name": slug, "description": _oneline(purpose, 280), "author": {"name": "Nexara AI"},
                           "keywords": ["nexara", "agent"]}, indent=2) + "\n"
    hook_script = f"""#!/usr/bin/env bash
# OPTIONAL hook (inactive until you rename hooks.optional.json to hooks.json): after a git commit, nudge Claude to offer a run
# of /{slug}. Non-blocking; it only adds a hint to the conversation.
input="$(cat)"
case "$input" in
  *"git commit"*) printf '%s' '{{"hookSpecificOutput":{{"hookEventName":"PostToolUse","additionalContext":"A commit was just made. If it would help the user, offer to run /{slug} on the committed changes."}}}}' ;;
esac
exit 0
"""
    hooks = json.dumps({"description": f"OPTIONAL - rename this file to hooks.json to enable. After a git commit, Claude is nudged to offer /{slug}. Non-blocking.",
                        "hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [
                            {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/remind-after-commit.sh", "timeout": 5}]}]}}, indent=2) + "\n"
    readme = f"""# {_oneline(spec['agent_name'], 100)} (plugin for {_oneline(company, 100)})

{purpose}

Managed by Nexara AI: this folder is regenerated when your agent is improved, so put your own changes in `../../custom/`.

## Use
`/{slug} <what to review or do>` - or just ask in plain words; the skill hands the work to the `{slug}` subagent.

## What it can and can't do
- The subagent has **read-only** tools (Read, Grep, Glob): it can read your project, never edit files or run commands.
- Claude Code has file tools, so whole-project work needs no pasting; big projects still cost more and the agent should say which files it read.
- To connect your own systems, add an MCP server (`.mcp.json`) and list its tools in the agent's `tools:` line, or ask us to.

## Safety restrictions built in
{rules}

## Keep secrets out of reach (recommended)
The agent is told not to open secret files, but an instruction is not a lock. For a hard guarantee add deny rules to your
project's `.claude/settings.json` (best effort; applied to Claude Code's built-in file tools):
```json
{{"permissions": {{"deny": ["Read(./.env)", "Read(./.env.*)", "Read(**/*.pem)", "Read(**/*.key)", "Read(**/id_rsa*)", "Read(**/credentials*)"]}}}}
```

## Optional hook
`hooks/hooks.optional.json` is **not active**. Rename it to `hooks.json` to enable a non-blocking nudge after `git commit`. Review hook scripts before enabling them.
"""
    return {".claude-plugin/plugin.json": manifest, f"agents/{slug}.md": agent, f"skills/{slug}/SKILL.md": skill,
            f"commands/{slug}.md": command, "hooks/hooks.optional.json": hooks, "hooks/remind-after-commit.sh": hook_script,
            "README.md": readme}


# ---------------------------------------------------------------- the whole repo
def repo_files(customer: dict, repo: dict, agents: list[dict], support_email: str = "") -> dict[str, str]:
    """customer: {company_name, slug}; repo: {owner, name}; agents: [{slug, spec}] in display order.
    Returns every Nexara-managed file (CHANGELOG.md excluded)."""
    company = _oneline(customer["company_name"], 100)
    mkt = marketplace_name(customer["slug"])
    owner, rname = repo["owner"], repo["name"]
    slugs = [a["slug"] for a in agents]
    commands = ", ".join(f"/{s}" for s in slugs) or "(none yet)"
    agent_list = "\n".join(f"- `/{a['slug']} <what to review or do>` - {_oneline(a['spec']['purpose'], 160)}" for a in agents) or "- (agents will appear here)"

    files: dict[str, str] = {}
    for a in agents:
        for rel, content in plugin_files(a["spec"], a["slug"], company).items():
            files[f"plugins/{a['slug']}/{rel}"] = content

    files[".claude-plugin/marketplace.json"] = json.dumps({
        "name": mkt, "description": f"Nexara AI agents for {company}", "owner": {"name": "Nexara AI"},
        "plugins": [{"name": a["slug"], "source": f"./plugins/{a['slug']}", "description": _oneline(a["spec"]["purpose"], 200)} for a in agents],
    }, indent=2) + "\n"

    enabled = {f"{s}@{mkt}": True for s in slugs}
    # clone mode: working inside this repo registers it as a local marketplace (plugins load in place at every session start)
    # and a background-safe `git pull` keeps the clone current
    files[".claude/settings.json"] = json.dumps({
        "extraKnownMarketplaces": {mkt: {"source": {"source": "directory", "path": "."}}},
        "enabledPlugins": enabled,
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "git pull --ff-only --quiet", "timeout": 15}]}]},
    }, indent=2) + "\n"
    # the customer's own project: github source + autoUpdate (the only way to turn auto-update on without the /plugin UI)
    files["snippets/project-settings.json"] = json.dumps({
        "extraKnownMarketplaces": {mkt: {"source": {"source": "github", "repo": f"{owner}/{rname}"}, "autoUpdate": True}},
        "enabledPlugins": enabled,
    }, indent=2) + "\n"

    agent_bullets = "\n".join(f"- /{a['slug']}: {_oneline(a['spec']['purpose'], 200)}" for a in agents) or "- (none yet)"
    tokens = dict(COMPANY=company, OWNER=owner, REPO=rname, MKT=mkt, PLUGINS=" ".join(slugs), COMMANDS=commands, AGENT_LIST=agent_list,
                  AGENT_BULLETS=agent_bullets, SUPPORT_EMAIL=f"- **Email:** {support_email}\n" if support_email else "")
    files["README.md"] = _fill(T.README_TMPL, **tokens)
    files["START-HERE.md"] = _fill(T.START_HERE, **tokens)
    files["SUPPORT.md"] = _fill(T.SUPPORT, **tokens)
    files["CLAUDE.md"] = _fill(T.CLAUDE_TMPL, **tokens)
    files["custom/README.md"] = T.CUSTOM_README
    files[".gitignore"] = T.GITIGNORE
    files[".github/ISSUE_TEMPLATE/support.md"] = T.ISSUE_TEMPLATE
    files["scripts/setup.sh"] = _fill(T.SETUP_SH, **tokens)
    files["scripts/doctor.sh"] = _fill(T.DOCTOR_SH, **tokens)
    files["scripts/setup.ps1"] = _fill(T.SETUP_PS1, **tokens)
    files["scripts/doctor.ps1"] = _fill(T.DOCTOR_PS1, **tokens)
    return files


def is_executable(path: str) -> bool:
    return path.endswith(".sh")


# ---------------------------------------------------------------- changelog
def changelog_entry(lines: list[str], when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    return f"## {when.strftime('%Y-%m-%d %H:%M UTC')}\n" + "\n".join(f"- {_oneline(l, 300)}" for l in lines) + "\n"


CHANGELOG_HEADER = "# Changelog\n\nEvery change Nexara AI makes to this repository, newest first.\n\n"


def new_changelog(entry: str) -> str:
    return CHANGELOG_HEADER + entry


def prepend_changelog(existing: str, entry: str) -> str:
    if existing.startswith(CHANGELOG_HEADER):
        return CHANGELOG_HEADER + entry + "\n" + existing[len(CHANGELOG_HEADER):]
    return CHANGELOG_HEADER + entry + "\n" + existing  # file was edited by hand: keep their text below our entry
