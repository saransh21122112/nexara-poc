import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend.artifacts import materialize as m, sandbox as sb, validate
from backend.models import AgentSpec

from . import golden

PLUGIN = "acme"


def build(spec):
    AgentSpec(**spec)                                   # the model validator (static gate) accepts the golden specs
    return m.assemble([AgentSpec(**spec).model_dump()], PLUGIN)


def write(files, root: Path):
    for p, c in files.items():
        f = root / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(c, encoding="utf-8")
    return root


def test_golden_specs_pass_the_static_gate():
    for spec in (golden.skill_only(), golden.tool_only(), golden.server_only(), golden.plugin_all()):
        assert validate.check_spec(spec) == []


def test_skill_only_layout():
    f = build(golden.skill_only())
    assert set(f) == {"skills/ticket-triage/SKILL.md", "skills/ticket-triage/references/categories.md"}
    text = f["skills/ticket-triage/SKILL.md"]
    assert text.startswith("---\nname: ticket-triage\n") and 'argument-hint: "paste the customer message"' in text and "allowed-tools: Read, Grep, Glob" in text
    assert "## Guardrails" in text and "Never change data." in text and "$ARGUMENTS" in text
    assert "## References" in text and "`references/categories.md`" in text


def test_tool_layout_and_wrapper_skill():
    f = build(golden.tool_only())
    assert {"tools/csv-check/csv-check.py", "tools/csv-check/tests.json", "tools/csv-check/test_csv_check.py", "tools/csv-check/README.md", "skills/csv-check/SKILL.md",
            "lib/nx_cli.py", "lib/nx_testkit.py"} <= set(f)
    skill = f["skills/csv-check/SKILL.md"]
    assert "allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/tools/csv-check/csv-check.py:*)" in skill
    assert "--json '<JSON input>'" in skill and ".mcp.json" not in f
    assert "lib/nx_mcp.py" not in f


def test_server_layout_mcp_json_and_wrapper_skill():
    f = build(golden.server_only())
    for p in ("servers/hr-data/server.py", "servers/hr-data/setup_ctx.py", "servers/hr-data/tool_run_query.py", "servers/hr-data/config.example.json", "servers/hr-data/tests.json",
              "servers/hr-data/test_server.py", "servers/hr-data/README.md", "skills/hr-data/SKILL.md", "lib/nx_mcp.py", "lib/nx_sqlguard.py", "lib/nx_db.py", "lib/nx_config.py"):
        assert p in f, p
    assert json.loads(f[".mcp.json"]) == {"mcpServers": {"hr-data": {"command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/servers/hr-data/server.py"]}}}
    assert "mcp__plugin_acme_hr-data__run_query" in f["skills/hr-data/SKILL.md"]
    cfg = json.loads(f["servers/hr-data/config.example.json"])
    assert cfg["database"] == {"path": "./hr.db"} and cfg["blocked_columns"] == ["salary", "national_id"]
    assert "secret" not in f["servers/hr-data/config.example.json"].lower()
    for p, c in f.items():
        if p.endswith(".py"):
            compile(c, p, "exec")


def test_plugin_has_all_component_kinds_and_explicit_skills_replace_wrappers():
    f = build(golden.plugin_all())
    assert "agents/reviewer.md" in f and "skills/check-data/SKILL.md" in f and "skills/hr-lookup/SKILL.md" in f
    assert "skills/csv-check/SKILL.md" not in f and "skills/hr-data/SKILL.md" not in f          # covered by the explicit skills that use them
    assert "Bash(python3 ${CLAUDE_PLUGIN_ROOT}/tools/csv-check/csv-check.py:*)" in f["skills/check-data/SKILL.md"]
    assert "mcp__plugin_acme_hr-data__list_tables" in f["skills/hr-lookup/SKILL.md"]
    assert f["agents/reviewer.md"].startswith("---\nname: reviewer\n") and "tools: Read, Grep, Glob" in f["agents/reviewer.md"]
    assert "Read, Grep, Glob" not in f["skills/check-data/SKILL.md"].split("allowed-tools:")[1].splitlines()[0]


def test_two_specs_with_the_same_component_are_refused():
    s = AgentSpec(**golden.tool_only()).model_dump()
    with pytest.raises(ValueError, match="same file"):
        m.assemble([s, s], PLUGIN)


def test_assemble_merges_servers_from_several_specs():
    a = AgentSpec(**golden.server_only()).model_dump()
    other = golden.server_only()
    other["mcp_servers"][0]["name"] = "hr-data-two"
    b = AgentSpec(**other).model_dump()
    f = m.assemble([a, b], PLUGIN)
    assert set(json.loads(f[".mcp.json"])["mcpServers"]) == {"hr-data", "hr-data-two"}


def test_output_is_deterministic():
    assert build(golden.plugin_all()) == build(golden.plugin_all())


# ---- the delivered code really works (runs inside the sandbox)
@pytest.fixture(scope="module")
def plugin_dir(tmp_path_factory):
    return write(build(golden.plugin_all()), tmp_path_factory.mktemp("plugin"))


def run_kit(root: Path, sub: str):
    return sb.run_python(root / "lib" / "nx_testkit.py", cwd=root, args=[str(root / sub / "tests.json")], timeout_s=60, guard=True) if "guard" in sb.run_python.__code__.co_varnames else \
        sb.run_python(root / "lib" / "nx_testkit.py", cwd=root, args=[str(root / sub / "tests.json")], timeout_s=60)


def test_generated_server_passes_its_own_testkit(plugin_dir):
    r = run_kit(plugin_dir, "servers/hr-data")
    assert r.ok, r.stdout[-2000:] + r.stderr[-1000:]
    report = json.loads(r.stdout)
    assert report["failed"] == 0 and report["passed"] >= 12


def test_generated_script_passes_its_own_testkit_and_selftest(plugin_dir):
    r = run_kit(plugin_dir, "tools/csv-check")
    assert r.ok, r.stdout[-2000:] + r.stderr[-1000:]


def test_generated_unittests_run_with_plain_unittest(plugin_dir):
    for sub in ("tools/csv-check", "servers/hr-data"):
        p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", sub], cwd=plugin_dir, capture_output=True, text=True, timeout=180)
        assert p.returncode == 0, p.stdout + p.stderr


def test_server_without_a_config_says_how_to_set_it_up(plugin_dir):
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}, {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_tables", "arguments": {}}}]
    p = subprocess.run([sys.executable, str(plugin_dir / "servers/hr-data/server.py")], input="\n".join(json.dumps(x) for x in msgs) + "\n", capture_output=True, text=True,
                       timeout=30, env={"PATH": "/usr/bin:/bin"}, cwd=plugin_dir)
    last = json.loads(p.stdout.strip().splitlines()[-1])
    assert last["result"]["isError"] and "README.md" in last["result"]["content"][0]["text"] and "Traceback" not in p.stdout + p.stderr


def test_script_reads_a_file_via_text_file(plugin_dir, tmp_path):
    f = tmp_path / "d.csv"
    f.write_text("a,b\n1,\n1,\n")
    p = subprocess.run([sys.executable, str(plugin_dir / "tools/csv-check/csv-check.py"), "--text-file", "d.csv"], capture_output=True, text=True, cwd=tmp_path)
    out = json.loads(p.stdout)
    assert out["ok"] and out["result"]["duplicate_rows"] == 1


@pytest.mark.skipif(not shutil.which("claude"), reason="claude CLI not installed")
def test_real_claude_plugin_validate_accepts_every_kind(tmp_path):
    for name, spec in (("skill", golden.skill_only()), ("tool", golden.tool_only()), ("server", golden.server_only()), ("plugin", golden.plugin_all())):
        root = write(build(spec), tmp_path / name)
        (root / ".claude-plugin").mkdir()
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "acme", "description": "t", "author": {"name": "Nexara AI"}}))
        p = subprocess.run(["claude", "plugin", "validate", str(root), "--json"], capture_output=True, text=True, timeout=120)
        data = json.loads(p.stdout)
        bad = [x for x in data.get("errors", [])] + [w for w in data.get("warnings", []) if "version" not in json.dumps(w).lower()]
        assert not bad, (name, bad)


def test_repo_files_mixes_plain_and_structured_agents():
    from backend import repo_gen
    from tests.test_api import SPEC
    cust = {"company_name": "Acme Corp", "slug": "acme-corp"}
    repo = {"owner": "o", "name": "nexara-acme-corp"}
    structured = AgentSpec(**golden.plugin_all()).model_dump()
    files = repo_gen.repo_files(cust, repo, [{"slug": "plain-one", "spec": SPEC}, {"slug": "golden", "spec": structured}])
    assert "agents/plain-one.md" in files and "skills/plain-one/SKILL.md" in files                # the old layout still comes out for plain specs
    assert "servers/hr-data/server.py" in files and ".mcp.json" in files and "lib/nx_mcp.py" in files
    assert "/acme-corp:hr-lookup" in files["README.md"] and "servers/hr-data" in files["README.md"]
    assert repo_gen.is_executable("tools/csv-check/csv-check.py") and repo_gen.is_executable("servers/hr-data/server.py") and not repo_gen.is_executable("lib/nx_cli.py")
