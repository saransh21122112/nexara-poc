"""AgentSpec stays backward compatible and now carries structured components that are validated on load."""
import pytest
from pydantic import ValidationError

from backend.models import AgentSpec
from tests.test_api import SPEC

TOOL = {"name": "run_query", "description": "Run a read-only query.", "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
        "handler_source": "def handle(args, ctx):\n    return {'rows': []}", "examples": [{"args": {"sql": "SELECT 1"}, "expect_contains": ["rows"]}]}
SERVER = {"name": "demo-server", "description": "Demo.", "blocks": ["db"], "tools": [TOOL]}


def test_legacy_specs_still_validate_and_have_no_structure():
    s = AgentSpec.model_validate(SPEC)
    assert s.kind is None and s.skills == [] and s.scripts == [] and s.mcp_servers == [] and s.subagents == []
    assert AgentSpec.model_validate(s.model_dump()).model_dump() == s.model_dump()


def test_structured_spec_round_trips_with_defaults_filled():
    s = AgentSpec.model_validate({**SPEC, "kind": "MCP server", "mcp_servers": [SERVER]})
    srv = s.mcp_servers[0]
    assert srv.tools[0].read_only is True and srv.setup_source.startswith("def setup") and srv.packages == [] and srv.fixtures.sqlite_sql is None
    assert AgentSpec.model_validate(s.model_dump()).model_dump() == s.model_dump()


def test_static_gate_problems_surface_as_validation_errors_so_the_model_can_be_asked_to_repair():
    bad = {**SERVER, "tools": [{**TOOL, "handler_source": "import os\ndef handle(args, ctx):\n    return os.listdir('/')"}]}
    with pytest.raises(ValidationError) as e:
        AgentSpec.model_validate({**SPEC, "mcp_servers": [bad]})
    assert "import of 'os' is not allowed" in str(e.value)
    with pytest.raises(ValidationError):
        AgentSpec.model_validate({**SPEC, "mcp_servers": [{**SERVER, "tools": [{**TOOL, "read_only": False}]}]})


def test_unknown_fields_in_components_are_rejected():
    with pytest.raises(ValidationError):
        AgentSpec.model_validate({**SPEC, "mcp_servers": [{**SERVER, "run_as_root": True}]})
