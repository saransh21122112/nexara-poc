import copy

import pytest

from backend.artifacts import checks, prompts, validate

from . import golden


@pytest.fixture
def run_on(monkeypatch):
    monkeypatch.setenv("NEXARA_RUN_GENERATED_CODE", "1")


@pytest.fixture
def run_off(monkeypatch):
    monkeypatch.delenv("NEXARA_RUN_GENERATED_CODE", raising=False)


def failed(rep):
    return [c["name"] for c in rep["checks"] if c["ok"] is False]


def test_plain_specs_have_nothing_to_check():
    assert checks.run_checks({"instructions": "x"})["applicable"] is False


def test_a_good_plugin_is_verified_when_code_may_run(run_on):
    rep = checks.run_checks(golden.plugin_all(), "acme")
    assert rep["verified"] and rep["ran_code"] and not failed(rep), failed(rep)
    names = " ".join(c["name"] for c in rep["checks"])
    assert "server hr-data: run_query example 3" in names and "tool csv-check: --selftest" in names and "static code gate" in names


def test_code_is_never_run_and_never_called_verified_when_the_switch_is_off(run_off):
    rep = checks.run_checks(golden.server_only(), "acme")
    assert rep["verified"] is False and rep["ran_code"] is False and "NEXARA_RUN_GENERATED_CODE" in rep["reason"]
    assert not any(c["group"] == "code" for c in rep["checks"])


def test_a_skill_only_spec_needs_no_code_run(run_off):
    rep = checks.run_checks(golden.skill_only(), "acme")
    assert rep["verified"] is True and rep["ran_code"] is False and not failed(rep)


def test_a_wrong_expectation_fails_the_check_and_names_it(run_on):
    spec = golden.server_only()
    spec["mcp_servers"][0]["tools"][2]["examples"][0]["expect_contains"] = ["Marketing"]
    rep = checks.run_checks(spec, "acme")
    assert rep["verified"] is False and any("run_query example 1" in n for n in failed(rep))
    assert "Marketing" in checks.failure_text(rep)


def test_a_broken_handler_is_reported_not_raised(run_on):
    spec = golden.tool_only()
    spec["scripts"][0]["handler_source"] = "def handle(args):\n    return 1 / 0\n"
    rep = checks.run_checks(spec, "acme")
    assert rep["verified"] is False and failed(rep)


def test_the_static_gate_rejects_before_anything_runs(run_on):
    spec = golden.tool_only()
    spec["scripts"][0]["handler_source"] = "import os\ndef handle(args):\n    return os.getcwd()\n"
    rep = checks.run_checks(spec, "acme")
    assert rep["verified"] is False and rep["ran_code"] is False and failed(rep) == ["static code gate"]


def test_prompt_example_code_passes_the_gate_and_covers_every_kind():
    import ast
    import json
    ex = prompts.EXAMPLE.split("and its setup_source:")
    tool = json.loads(ex[0].split("shape only, write your own for the customer's domain)\n")[1])
    assert validate.check_source(tool["handler_source"]) == []
    assert validate.check_source(json.loads(ex[1].strip()), entry="setup", min_args=1, max_args=1) == []
    for t in ("Plugin", "Skill", "MCP server", "Tool", "Custom", "weird"):
        assert "DELIVERABLE CONTRACT" in prompts.structure_prompt(t)
    assert prompts.kind_of("MCP server") == "mcp_server" and prompts.kind_of("nonsense") == "custom"
    ast.parse("x = 1")
