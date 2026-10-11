import io
import json
import os
import re
import shutil
import subprocess
import zipfile

import pytest
import yaml

from backend import repo_gen

SPEC = {"agent_name": "Acme Code Reviewer", "purpose": "Reviews code for bugs, security issues and style.", "business_problem": "b",
        "instructions": "You are a careful code reviewer. Find real bugs and security issues, rank by severity, suggest fixes.",
        "capabilities": ["Find SQL injection", "Spot logic bugs"], "required_tools": [], "safety_restrictions": ["Read-only: never modify code", "Never invent issues"],
        "success_criteria": ["Finds real bugs", "No false claims"], "test_scenarios": [], "input_schema": {}, "output_schema": {}, "data_sources": []}
SPEC2 = {**SPEC, "agent_name": "Acme Sales Analyst", "purpose": "Ranks products by revenue.", "capabilities": ["Rank products"]}
CUST = {"company_name": "Acme Corp", "slug": "acme-corp"}
REPO = {"owner": "saransh21122112", "name": "nexara-acme-corp"}
PLUGIN, MKT = "acme-corp", "nexara-acme-corp"


def build(specs=(SPEC,), cust=CUST, repo=REPO, email="", legacy=None):
    agents = [{"slug": repo_gen.plugin_slug(s), "spec": s} for s in specs]
    return repo_gen.repo_files(cust, repo, agents, email, legacy), agents


def frontmatter(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "no frontmatter"
    return yaml.safe_load(m.group(1))


# ------------------------------------------------------------------ layout: the repo root IS the plugin
def test_layout_is_one_plugin_at_the_repo_root():
    files, agents = build((SPEC, SPEC2))
    for p in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", "agents/acme-code-reviewer.md", "skills/acme-code-reviewer/SKILL.md",
              "agents/acme-sales-analyst.md", "skills/acme-sales-analyst/SKILL.md", "README.md", "START-HERE.md", "SUPPORT.md", ".gitignore",
              "custom/README.md", "snippets/project-settings.json", ".github/ISSUE_TEMPLATE/support.md",
              "scripts/setup.sh", "scripts/doctor.sh", "scripts/update.sh", "scripts/setup.ps1", "scripts/doctor.ps1", "scripts/update.ps1"):
        assert p in files, p
    # nothing from the old per-agent layout survives
    assert not any(p.startswith("plugins/") or p.startswith("commands/") or p.startswith("hooks/") for p in files)
    for gone in (".claude/settings.json", "CLAUDE.md", "CHANGELOG.md"):
        assert gone not in files, gone
    assert not any("@@" in c for c in files.values())
    assert repo_gen.is_tracked("README.md") and repo_gen.is_tracked("agents/x.md") and not repo_gen.is_tracked("custom/x") and not repo_gen.is_tracked("CHANGELOG.md")
    assert repo_gen.is_executable("scripts/setup.sh") and not repo_gen.is_executable("scripts/setup.ps1")


def test_plugin_manifest_and_marketplace_point_at_the_root_with_no_pinned_version():
    files, _ = build((SPEC, SPEC2))
    pj = json.loads(files[".claude-plugin/plugin.json"])
    assert pj["name"] == PLUGIN and pj["author"]["name"] == "Nexara AI" and "version" not in pj
    mk = json.loads(files[".claude-plugin/marketplace.json"])
    assert mk["name"] == MKT and mk["owner"]["name"] == "Nexara AI"
    assert [(p["name"], p["source"]) for p in mk["plugins"]] == [(PLUGIN, "./")]       # ONE plugin for the company, sourced from the repo root
    assert "version" not in json.dumps(mk) and "renames" not in mk                     # a pinned version would block updates
    assert generated_names(files) == {"acme-code-reviewer", "acme-sales-analyst"}


def generated_names(files):
    return {p.split("/")[1][:-3] for p in files if p.startswith("agents/")}


def test_legacy_plugin_names_become_a_renames_map_so_old_installs_migrate():
    files, _ = build(legacy=["acmecodereviewer", "old-agent", PLUGIN, ""])
    mk = json.loads(files[".claude-plugin/marketplace.json"])
    assert mk["renames"] == {"acmecodereviewer": PLUGIN, "old-agent": PLUGIN}          # never maps the plugin onto itself


def test_customer_project_snippet_enables_auto_update_for_the_one_plugin():
    files, _ = build()
    snip = json.loads(files["snippets/project-settings.json"])
    entry = snip["extraKnownMarketplaces"][MKT]
    assert entry["source"] == {"source": "github", "repo": "saransh21122112/nexara-acme-corp"} and entry["autoUpdate"] is True
    assert snip["enabledPlugins"] == {f"{PLUGIN}@{MKT}": True} and "hooks" not in snip


# ------------------------------------------------------------------ one skill + one read-only agent per agent
def test_each_agent_has_one_skill_and_one_read_only_subagent():
    files, _ = build()
    agent = files["agents/acme-code-reviewer.md"]
    fm = frontmatter(agent)
    assert fm["name"] == "acme-code-reviewer" and fm["tools"] == "Read, Grep, Glob" and fm["model"] == "inherit"
    assert "Do not open secret files" in agent and "## What you cover" in agent and "- Find SQL injection" in agent
    skill = files["skills/acme-code-reviewer/SKILL.md"]
    sm = frontmatter(skill)
    assert sm["name"] == "acme-code-reviewer" and sm["argument-hint"] and "$ARGUMENTS" in skill
    assert f"`{PLUGIN}:acme-code-reviewer` subagent" in skill                           # the skill delegates to the namespaced subagent
    assert sm["description"] == fm["description"]


def test_descriptions_are_short_and_routing_focused():
    long_caps = [f"Capability number {i} " + "with a very long explanatory tail " * 4 for i in range(8)]
    spec = {**SPEC, "purpose": "Does a thing. " + "More words about the thing. " * 30, "capabilities": long_caps}
    files, _ = build((spec,))
    d = frontmatter(files["agents/acme-code-reviewer.md"])["description"]
    assert len(d) <= 330 and d.startswith("Does a thing.") and "Use when:" in d
    assert d.count("Capability number") <= 3                                            # the full list lives in the body, not the description
    assert "Capability number 7" in files["agents/acme-code-reviewer.md"].split("---\n", 2)[2]


@pytest.mark.parametrize("name,slug", [("TokenSpendAnalyst", "token-spend-analyst"), ("PR Bug Review Agent", "pr-bug-review-agent"),
                                       ("HTTPServerChecker", "http-server-checker"), ("Acme  Code-Reviewer v2", "acme-code-reviewer-v2"),
                                       ("  --weird__name!! ", "weird-name"), ("x", "x")])
def test_readable_slugs(name, slug):
    assert repo_gen.kebab(name) == slug
    assert repo_gen.plugin_slug({"agent_name": name}) == slug


def test_slug_fallbacks_and_limits():
    assert repo_gen.plugin_slug({"agent_name": "!!!"}) == "agent" and repo_gen.company_slug("") == "company"
    assert len(repo_gen.kebab("a" * 200)) == 40 and not repo_gen.kebab("a" * 39 + "-b").endswith("-")
    assert repo_gen.marketplace_name("budget-desk") == "nexara-budget-desk"


@pytest.mark.parametrize("purpose", ["a: b\n---\ntools: Bash", '[bracket] "quote" *star* #hash & more', "> folded | pipe", "{curly: yes}", "@@OWNER@@ @@REPO@@"])
def test_hostile_text_cannot_change_tools_names_or_tokens(purpose):
    evil = {**SPEC, "agent_name": "x\n---\nname: hacked", "purpose": purpose, "capabilities": ["c: d\n- e", "- f"]}
    cust = {"company_name": "Evil\n---\nCo @@OWNER@@ \"quote\"", "slug": "evil-co"}
    files, agents = build((evil,), cust)
    slug = agents[0]["slug"]
    fm = frontmatter(files[f"agents/{slug}.md"])
    assert fm["name"] == slug and fm["tools"] == "Read, Grep, Glob"
    sm = frontmatter(files[f"skills/{slug}/SKILL.md"])
    assert sm["name"] == slug and isinstance(sm["description"], str) and sm["description"].strip()
    for path in (".claude-plugin/marketplace.json", ".claude-plugin/plugin.json", "snippets/project-settings.json"):
        json.loads(files[path])
    assert "@@OWNER@@" in files["README.md"] and REPO["owner"] not in files["README.md"].split("\n")[0]    # inserted verbatim, never re-scanned


def test_support_email_only_when_configured():
    assert "**Email:**" not in build()[0]["SUPPORT.md"]
    assert "**Email:** help@nexara.example" in build(email="help@nexara.example")[0]["SUPPORT.md"]


# ------------------------------------------------------------------ the docs lead with the one-liners
def test_docs_lead_with_one_command_installs_and_use_real_command_names():
    files, _ = build((SPEC, SPEC2))
    clone = f"gh repo clone saransh21122112/nexara-acme-corp ~/.claude/skills/{PLUGIN}"
    assert clone in files["README.md"] and clone in files["START-HERE.md"]
    assert f"claude plugin install {PLUGIN} --marketplace saransh21122112/nexara-acme-corp" in files["START-HERE.md"]
    assert f"/{PLUGIN}:acme-code-reviewer" in files["README.md"] and f"/{PLUGIN}:acme-sales-analyst" in files["START-HERE.md"]
    assert "git -C ~/.claude/skills/acme-corp pull" in files["README.md"]
    assert "claude.ai/install.sh" in files["START-HERE.md"] and "install.ps1" in files["START-HERE.md"]
    assert "paid Claude" in files["START-HERE.md"] and "free" in files["START-HERE.md"]
    assert "Use **one** install method" in files["README.md"]                            # same plugin name loads only once
    assert "Read(./.env)" in files["README.md"] and "Ask us for the plugin zip" in files["START-HERE.md"]


def test_scripts_carry_the_right_names_and_parse():
    files, _ = build((SPEC, SPEC2))
    for name in ("scripts/setup.sh", "scripts/doctor.sh", "scripts/update.sh"):
        assert "saransh21122112/nexara-acme-corp" in files[name] and f'PLUGIN="{PLUGIN}"' in files[name] and f'MKT="{MKT}"' in files[name]
        if shutil.which("bash"):
            r = subprocess.run(["bash", "-n"], input=files[name], text=True, capture_output=True)
            assert r.returncode == 0, (name, r.stderr)
    assert "https://claude.ai/install.sh" in files["scripts/setup.sh"] and "install.sh | bash" not in files["scripts/setup.sh"]
    assert "https://claude.ai/install.ps1" in files["scripts/setup.ps1"] and "| iex" not in files["scripts/setup.ps1"]
    assert f"claude plugin install \"$PLUGIN\" --marketplace \"$REPO\"" in files["scripts/setup.sh"]
    for ps in ("scripts/setup.ps1", "scripts/doctor.ps1", "scripts/update.ps1"):
        t = files[ps]
        assert PLUGIN in t and MKT in t and t.count("{") == t.count("}")      # (parens are not checked: prose like "a)" is unbalanced)


# ------------------------------------------------------------------ factory-only tools must not leak into Claude Code plugins
TOOLY = {**SPEC, "agent_name": "Spend Analyst", "required_tools": ["calculator", "lookup_business_data"],
         "instructions": "You are a data analyst. Use the calculator for all arithmetic. Use `lookup_business_data` only if asked. Be precise.",
         "safety_restrictions": ["Use the calculator for all arithmetic", "Never present lookup_business_data samples as real usage"],
         "success_criteria": ["Costs are computed with calculator"], "capabilities": ["Compute costs with the calculator"]}


def test_agent_is_told_the_factory_tools_do_not_exist_in_claude_code():
    files, _ = build((TOOLY,))
    agent = files["agents/spend-analyst.md"]
    assert "Tools in this environment (read first)" in agent and "must not refuse or stall" in agent
    assert agent.index("Tools in this environment") < agent.index("You are a data analyst")        # the note comes BEFORE the instructions
    after_note = agent.split("means the substitutes above.")[1]            # everything after the note itself
    for stale in ("`calculator`", "lookup_business_data", "the calculator"):
        assert stale not in after_note, stale
    assert "careful step-by-step arithmetic" in after_note and "the data files the user points you to" in after_note
    skill = files["skills/spend-analyst/SKILL.md"]
    assert "no calculator or database tool" in skill and "calculator" not in skill.split("Request:")[0]
    assert "arithmetic by hand" in files["README.md"] or "arithmetic by hand" in files["README.md"].replace("do arithmetic by hand", "arithmetic by hand")
    assert agent.count("tools: Read, Grep, Glob") == 1                                           # still read-only: nothing was granted


def test_agents_without_factory_tools_get_no_note():
    files, _ = build()
    assert "Tools in this environment" not in files["agents/acme-code-reviewer.md"]
    assert "no calculator" not in files["skills/acme-code-reviewer/SKILL.md"]


def test_adapt_only_replaces_whole_tool_names():
    assert repo_gen._adapt("Use the calculator; `calculator`, calculators and calculator_mode") == \
        "Use the careful step-by-step arithmetic; careful step-by-step arithmetic, calculators and calculator_mode"


# ------------------------------------------------------------------ hashing / helpers
def test_generator_version_marks_existing_agents_as_behind_so_they_can_be_resynced(monkeypatch):
    assert repo_gen.GENERATOR_VERSION >= 3
    old = repo_gen.spec_hash(SPEC)
    monkeypatch.setattr(repo_gen, "GENERATOR_VERSION", repo_gen.GENERATOR_VERSION + 1)
    assert repo_gen.spec_hash(SPEC) != old
    assert repo_gen.spec_hash(SPEC) == repo_gen.spec_hash(dict(reversed(list(SPEC.items()))))
    assert repo_gen.spec_hash(SPEC) != repo_gen.spec_hash({**SPEC, "purpose": "other"})


def test_git_blob_sha_matches_git(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git not installed")
    for text in ("", "hello\n", "unicode: café ✓\nline2"):
        f = tmp_path / "f.txt"
        f.write_text(text)
        assert subprocess.check_output(["git", "hash-object", str(f)], text=True).strip() == repo_gen.git_blob_sha(text)


def test_changelog_helpers():
    e1 = repo_gen.changelog_entry(["Initial delivery: A"])
    c = repo_gen.new_changelog(e1)
    assert c.startswith("# Changelog") and "Initial delivery: A" in c
    c2 = repo_gen.prepend_changelog(c, repo_gen.changelog_entry(["Updated agent: A"]))
    assert c2.index("Updated agent") < c2.index("Initial delivery") and c2.count("# Changelog") == 1
    assert "hand text" in repo_gen.prepend_changelog("hand text", e1)


# ------------------------------------------------------------------ zip fallback (no GitHub)
def test_plugin_zip_unzips_into_skills_folder_and_leaves_out_repo_only_files():
    files, _ = build((SPEC, SPEC2))
    z = zipfile.ZipFile(io.BytesIO(repo_gen.plugin_zip(files, PLUGIN)))
    names = z.namelist()
    assert all(n.startswith(f"{PLUGIN}/") for n in names)                                # one top-level folder = the plugin name
    assert f"{PLUGIN}/.claude-plugin/plugin.json" in names and f"{PLUGIN}/agents/acme-code-reviewer.md" in names and f"{PLUGIN}/skills/acme-sales-analyst/SKILL.md" in names
    assert f"{PLUGIN}/START-HERE.md" in names and f"{PLUGIN}/scripts/doctor.sh" in names and f"{PLUGIN}/scripts/update.sh" in names
    assert not any("/.github/" in n or "/snippets/" in n or "/scripts/setup" in n for n in names)
    assert (z.getinfo(f"{PLUGIN}/scripts/doctor.sh").external_attr >> 16) & 0o111 and (z.getinfo(f"{PLUGIN}/README.md").external_attr >> 16) & 0o444 == 0o444


# ------------------------------------------------------------------ the REAL Claude CLI
needs_claude = pytest.mark.skipif(not shutil.which("claude"), reason="claude CLI not installed")


def write_repo(tmp_path, files):
    for p, c in files.items():
        f = tmp_path / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(c)
    return tmp_path


@needs_claude
def test_real_cli_validates_the_root_plugin_and_marketplace(tmp_path):
    files, _ = build((SPEC, SPEC2), legacy=["old-name"])
    root = write_repo(tmp_path / "repo", files)
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg")}
    r = subprocess.run(["claude", "plugin", "validate", str(root)], capture_output=True, text=True, env=env, timeout=90)
    assert r.returncode == 0 and "Validation passed" in r.stdout, (r.stdout, r.stderr)
    assert "Failed" not in r.stdout and "✘" not in r.stdout


@needs_claude
def test_real_cli_loads_a_plain_folder_copy_with_no_settings_and_no_duplicates(tmp_path):
    """The headline promise: copy/clone the folder into ~/.claude/skills and it loads. One skill + one agent per agent."""
    files, _ = build((SPEC, SPEC2))
    cfg = tmp_path / "cfg"
    write_repo(cfg / "skills" / PLUGIN, files)
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(cfg)}
    lst = subprocess.run(["claude", "plugin", "list"], capture_output=True, text=True, env=env, timeout=90).stdout
    assert f"{PLUGIN}@skills-dir" in lst and "loaded" in lst, lst
    det = subprocess.run(["claude", "plugin", "details", f"{PLUGIN}@skills-dir"], capture_output=True, text=True, env=env, timeout=90).stdout
    assert re.search(r"Skills \(2\)\s+(acme-code-reviewer, acme-sales-analyst|acme-sales-analyst, acme-code-reviewer)", det), det
    assert re.search(r"Agents \(2\)", det) and "Hooks (0)" in det, det


# ------------------------------------------------------------------ the scripts customers run must never execute customer-supplied text
HOSTILE = 'Evil"; rm -rf ~ #$(touch /tmp/pwned_company) `id` \u2028 $env:USERPROFILE\nline2 \\'


def test_customer_text_only_ever_lands_in_comments_of_the_shipped_scripts():
    spec = {**SPEC, "agent_name": "Agent `id`", "capabilities": ["c $(touch /tmp/pwned_cap)"], "purpose": "p; rm -rf /"}
    files, _ = build((spec,), {"company_name": HOSTILE, "slug": "evil-co"}, {"owner": "o", "name": "nexara-evil-co"})
    for path in (p for p in files if p.startswith("scripts/")):
        text = files[path]
        for marker in ("pwned_company", "pwned_cap", "rm -rf"):
            for line in text.splitlines():
                if marker in line:
                    assert line.lstrip().startswith("#"), (path, line)              # only a comment line may mention it
        assert "\u2028" not in text and "\r" not in text
        if path.endswith(".sh") and shutil.which("bash"):
            assert subprocess.run(["bash", "-n"], input=text, text=True, capture_output=True).returncode == 0


@pytest.mark.parametrize("owner,repo", [('o"; rm -rf ~; "', "nexara-x"), ("o", "nexara-x $(id)"), ("o o", "r"), ("o", "r\nx"), ("", "r"), ("o", "")])
def test_generation_refuses_unsafe_owner_or_repo_names(owner, repo):
    with pytest.raises(ValueError):
        build((SPEC,), CUST, {"owner": owner, "name": repo})


def test_generation_refuses_unsafe_plugin_or_agent_slugs(monkeypatch):
    monkeypatch.setattr(repo_gen, "company_slug", lambda s: "bad name; id")
    with pytest.raises(ValueError):
        build((SPEC,))
    monkeypatch.undo()
    files, agents = build((SPEC,))
    agents[0]["slug"] = "x; id"
    with pytest.raises(ValueError):
        repo_gen.repo_files(CUST, REPO, agents)
