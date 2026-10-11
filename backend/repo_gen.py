"""Builds the complete file tree of a customer's private repo: ONE Claude Code plugin at the repo root (all of the company's agents:
agents/ + skills/), usable by a plain clone into ~/.claude/skills/, plus a marketplace entry, customer docs and helper scripts. Pure functions: (customer, repo, agents) -> {path: text}. Nothing here talks to GitHub."""
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


GENERATOR_VERSION = 3   # bump when the generated files change meaningfully: every delivered agent then shows "repo behind" until synced


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(json.dumps([GENERATOR_VERSION, spec], sort_keys=True).encode()).hexdigest()[:16]


def kebab(name: str, limit: int = 40) -> str:
    """Readable slug: splits camelCase and acronyms ('TokenSpendAnalyst' -> 'token-spend-analyst', 'PRBot' -> 'pr-bot')."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:limit].strip("-")


def plugin_slug(spec: dict) -> str:
    """Slug of ONE agent (its agent file and skill name)."""
    return kebab(spec["agent_name"]) or "agent"


def company_slug(customer_slug: str) -> str:
    """Name of the company's plugin (the repo root). It prefixes every skill: /<company>:<agent>."""
    return kebab(customer_slug, 50) or "company"


def marketplace_name(customer_slug: str) -> str:
    return ("nexara-" + company_slug(customer_slug))[:60].strip("-")


def _oneline(text: str, limit: int = 600) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _cut(text: str, limit: int) -> str:
    """Shorten at a word boundary."""
    t = _oneline(text, 10_000)
    return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0].rstrip(",;:.") + "..."


def _fold(text: str) -> str:
    """YAML folded block scalar body (safe for any characters, including ones that would break a plain scalar)."""
    return "\n".join("  " + ln for ln in textwrap.wrap(_oneline(text), 88)) or "  agent"


def _first_sentence(text: str, limit: int = 170) -> str:
    t = _oneline(text, 10_000)
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    return _cut(m.group(1) if m else t, limit)


def short_description(spec: dict) -> str:
    """What the agent is for + when to use it, in about 300 characters. The full capability list lives in the agent body: the
    description is always in Claude's context and drives routing, so it must stay short."""
    cues = "; ".join(_cut(_adapt(c), 60) for c in spec["capabilities"][:3])
    return _cut(f"{_first_sentence(_adapt(spec['purpose']))} Use when: {cues}.", 320)


FACTORY_TOOLS = {
    "lookup_business_data": "the data files the user points you to",
    "calculator": "careful step-by-step arithmetic",
}
ENV_NOTE = """## Tools in this environment (read first)
You run inside Claude Code with Read, Grep and Glob only. The instructions below were written for a test harness that offered
extra tools named `calculator` and `lookup_business_data`; **those tools do not exist here, and you must not refuse or stall because of that.**
- Instead of `calculator`: do the arithmetic yourself, carefully. Show the formula, the inputs and each intermediate result, keep full precision until the final rounding, and re-check totals by recomputing them a second way. Say once, briefly, that figures were computed by you rather than by a tool, so important numbers deserve a spot check.
- Instead of `lookup_business_data`: use only data the user points you to (Read/Grep/Glob). Never invent records.
Any rule below that says to "use the calculator" or "use lookup_business_data" means the substitutes above.
"""


def _adapt(text: str) -> str:
    """Replace factory-only tool names in spec text with what is actually available in Claude Code."""
    for name, repl in FACTORY_TOOLS.items():
        text = re.sub(rf"`?\b{name}\b`?", repl, text)
    return text


def uses_factory_tools(spec: dict) -> bool:
    text = " ".join([spec["instructions"], *spec["safety_restrictions"], *spec["success_criteria"], *spec["capabilities"]])
    return any(t in FACTORY_TOOLS for t in spec.get("required_tools", [])) or any(re.search(rf"\b{n}\b", text) for n in FACTORY_TOOLS)


def _fill(template: str, **tokens) -> str:
    """Single pass: substituted values are never rescanned, so customer-supplied text can't inject another token."""
    return re.sub(r"@@([A-Z_]+)@@", lambda m: tokens.get(m.group(1), m.group(0)), template)


# ---------------------------------------------------------------- one agent = one subagent + one skill at the plugin root
def agent_files(spec: dict, slug: str, plugin: str) -> dict[str, str]:
    """Files for one agent. Paths are relative to the repo root, which IS the plugin."""
    rules = "\n".join(f"- {_oneline(_adapt(s), 300)}" for s in spec["safety_restrictions"])
    caps = "\n".join(f"- {_oneline(_adapt(c), 200)}" for c in spec["capabilities"])
    factory = uses_factory_tools(spec)
    note = ENV_NOTE + "\n" if factory else ""
    desc = short_description(spec)
    agent = f"""---
name: {slug}
description: >-
{_fold(desc)}
tools: Read, Grep, Glob
model: inherit
---

{note}{_adapt(spec['instructions'].strip())}

## What you cover
{caps}

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
{_fold(desc)}
argument-hint: what to apply it to (files, folders, a pull request, or "whole repo")
---

Use the `{plugin}:{slug}` subagent for this request and give the user its findings as they are, most important first.
Pass along the user's words and any paths they mention.

Request: $ARGUMENTS

If the request is empty, ask the user what to apply it to instead of guessing. Do not do the agent's work yourself and do not add
conclusions it did not reach.
{(chr(10) + "The agent has no calculator or database tool in Claude Code: it computes carefully by hand and says so. Do not refuse, do not ask the user to enable a tool, and do not redo its arithmetic with tools you lack." + chr(10)) if factory else ""}"""
    return {f"agents/{slug}.md": agent, f"skills/{slug}/SKILL.md": skill}


# ---------------------------------------------------------------- the whole repo (= one plugin for the company)
def repo_files(customer: dict, repo: dict, agents: list[dict], support_email: str = "", legacy: list[str] | None = None) -> dict[str, str]:
    """customer: {company_name, slug}; repo: {owner, name}; agents: [{slug, spec}] in display order; legacy: plugin names used by older
    layouts (migrated through the marketplace `renames` map). Returns every Nexara-managed file (CHANGELOG.md excluded)."""
    company = _oneline(customer["company_name"], 100)
    plugin = company_slug(customer["slug"])
    mkt = marketplace_name(customer["slug"])
    owner, rname = repo["owner"], repo["name"]
    slugs = [a["slug"] for a in agents]
    commands = ", ".join(f"/{plugin}:{s}" for s in slugs) or "(none yet)"
    agent_list = "\n".join(f"- `/{plugin}:{a['slug']} <what to review or do>` - {_first_sentence(a['spec']['purpose'], 160)}" for a in agents) or "- (agents will appear here)"

    files: dict[str, str] = {}
    for a in agents:
        files.update(agent_files(a["spec"], a["slug"], plugin))

    blurb = _cut(f"Nexara AI agents for {company}: " + "; ".join(a["spec"]["agent_name"] for a in agents), 250) if agents else f"Nexara AI agents for {company}"
    files[".claude-plugin/plugin.json"] = json.dumps(
        {"name": plugin, "description": blurb, "author": {"name": "Nexara AI"}, "keywords": ["nexara", "agents"]}, indent=2) + "\n"
    market = {"name": mkt, "description": f"Nexara AI agents for {company}", "owner": {"name": "Nexara AI"},
              "plugins": [{"name": plugin, "source": "./", "description": blurb}]}
    old = sorted({x for x in (legacy or []) if x and x != plugin})
    if old:
        market["renames"] = {x: plugin for x in old}      # customers who installed an older per-agent plugin migrate instead of breaking
    files[".claude-plugin/marketplace.json"] = json.dumps(market, indent=2) + "\n"
    # for sharing with a whole project and for auto-update (the only way to turn it on without the /plugin toggle)
    files["snippets/project-settings.json"] = json.dumps({
        "extraKnownMarketplaces": {mkt: {"source": {"source": "github", "repo": f"{owner}/{rname}"}, "autoUpdate": True}},
        "enabledPlugins": {f"{plugin}@{mkt}": True},
    }, indent=2) + "\n"

    tokens = dict(COMPANY=company, OWNER=owner, REPO=rname, MKT=mkt, PLUGIN=plugin, COMMANDS=commands, AGENT_LIST=agent_list,
                  SUPPORT_EMAIL=f"- **Email:** {support_email}\n" if support_email else "")
    for path, tmpl in (("README.md", T.README_TMPL), ("START-HERE.md", T.START_HERE), ("SUPPORT.md", T.SUPPORT),
                       ("scripts/setup.sh", T.SETUP_SH), ("scripts/doctor.sh", T.DOCTOR_SH), ("scripts/update.sh", T.UPDATE_SH),
                       ("scripts/setup.ps1", T.SETUP_PS1), ("scripts/doctor.ps1", T.DOCTOR_PS1), ("scripts/update.ps1", T.UPDATE_PS1)):
        files[path] = _fill(tmpl, **tokens)
    files["custom/README.md"] = T.CUSTOM_README
    files[".gitignore"] = T.GITIGNORE
    files[".github/ISSUE_TEMPLATE/support.md"] = T.ISSUE_TEMPLATE
    return files


def is_executable(path: str) -> bool:
    return path.endswith(".sh")


def plugin_zip(files: dict[str, str], plugin: str) -> bytes:
    """The plugin as a zip whose single top-level folder is the plugin name (unzip into ~/.claude/skills/). For customers without
    GitHub: no repo-only files, and nothing here updates itself."""
    import io
    import zipfile
    skip = (".github/", "snippets/")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, content in sorted(files.items()):
            if path.startswith(skip) or path.startswith("scripts/setup"):
                continue
            info = zipfile.ZipInfo(f"{plugin}/{path}", date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = (0o755 if is_executable(path) else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, content)
    return buf.getvalue()


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
