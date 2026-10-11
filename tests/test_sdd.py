"""Spec 007 behaviour 12: the PR gate that enforces spec-first development."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("check_spec", Path(__file__).resolve().parent.parent / "scripts" / "check_spec.py")
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


def test_docs_and_tests_only_changes_need_no_spec():
    assert cs.check(["README.md", "docs/flows.md", "tests/test_x.py"], "")[0]


def test_code_change_without_a_spec_fails_with_a_helpful_message():
    ok, why = cs.check(["backend/main.py"], "Fixes things")
    assert not ok and "docs/specs/" in why and "No-spec:" in why
    assert not cs.check(["frontend/css/site.css"], "")[0]


def test_code_change_with_a_spec_passes():
    assert cs.check(["backend/main.py", "docs/specs/008-thing.md"], "")[0]


def test_the_template_alone_is_not_a_spec():
    assert not cs.check(["backend/main.py", "docs/specs/000-template.md"], "")[0]


def test_no_spec_escape_needs_a_real_reason():
    assert cs.check(["backend/main.py"], "Bump dependency\n\nNo-spec: dependency bump, no behaviour change")[0]
    assert not cs.check(["backend/main.py"], "No-spec:")[0]
    assert not cs.check(["backend/main.py"], "No-spec:   ")[0]
    assert not cs.check(["backend/main.py"], "no-spec fine")[0]


def test_case_insensitive_marker_and_mixed_changes():
    assert cs.check(["frontend/js/x.js", "tests/t.py"], "no-spec: copy fix")[0]
    assert not cs.check(["tests/t.py", "frontend/js/x.js"], "")[0]
