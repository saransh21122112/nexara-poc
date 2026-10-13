import re
from typing import Any, Literal
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

AGENT_TYPES = ["Plugin", "Skill", "MCP server", "Tool", "Custom"]   # what the customer wants built; the DB column keeps its old name `agent_type`
AgentType = Literal["Plugin", "Skill", "MCP server", "Tool", "Custom"]


# GitHub usernames: 1-39 chars, letters/digits/single hyphens, cannot start or end with a hyphen
GITHUB_USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
ClaudeStatus = Literal["have_claude_code", "have_claude_account", "no_claude", "not_sure"]
OsName = Literal["mac", "windows", "linux", "other"]


class RequestIn(BaseModel):
    company_name: str = Field(min_length=1, max_length=120)
    industry: str = Field(min_length=1, max_length=120)
    agent_type: AgentType
    business_problem: str = Field(min_length=10, max_length=4000)
    tasks: str = Field(min_length=5, max_length=4000)
    existing_tools: str = Field(min_length=1, max_length=2000)
    required_output: str = Field(min_length=3, max_length=2000)
    examples: str | None = Field(default=None, max_length=4000)      # optional: a few real examples from the customer (confidential parts removed)
    contact_email: EmailStr
    github_username: str
    claude_status: ClaudeStatus
    os: OsName | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("github_username")
    @classmethod
    def _github(cls, v: str) -> str:
        v = v.lstrip("@")
        if not GITHUB_USER_RE.match(v):
            raise ValueError("enter your GitHub username (letters, digits, single hyphens; no URL)")
        return v


ASSIST_FIELDS = ("company_name", "industry", "agent_type", "business_problem", "tasks", "existing_tools", "required_output", "examples")


class AssistIn(BaseModel):
    """Free-text request to the form assistant: fill everything from `description`, or change boxes per `instruction`."""
    description: str = Field(default="", max_length=1500)
    instruction: str = Field(default="", max_length=500)
    field: str | None = None                                  # limit an edit to one box
    current: dict[str, str] = Field(default_factory=dict)

    @field_validator("field")
    @classmethod
    def _field(cls, v):
        if v is not None and v not in ASSIST_FIELDS:
            raise ValueError("unknown field")
        return v

    @field_validator("current")
    @classmethod
    def _current(cls, v):
        return {k: str(x)[:4000] for k, x in v.items() if k in ASSIST_FIELDS}   # drop anything that is not a form box


class AssistOut(BaseModel):
    """What the assistant may return: text boxes only (never contact, GitHub or OS)."""
    company_name: str | None = Field(default=None, max_length=120)
    industry: str | None = Field(default=None, max_length=120)
    agent_type: AgentType | None = None
    business_problem: str | None = Field(default=None, max_length=4000)
    tasks: str | None = Field(default=None, max_length=4000)
    existing_tools: str | None = Field(default=None, max_length=2000)
    required_output: str | None = Field(default=None, max_length=2000)
    examples: str | None = Field(default=None, max_length=4000)


CONTACT_TOPICS = ["General", "New request", "Existing project", "Partnership", "Press", "Other"]


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    topic: Literal["General", "New request", "Existing project", "Partnership", "Press", "Other"] = "General"
    message: str = Field(min_length=10, max_length=4000)
    ticket: str = Field(max_length=200)
    website: str = Field(default="", max_length=500)       # honeypot: real people never see or fill it

    @field_validator("name", "message", "topic", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


class GenerateTestsIn(BaseModel):
    count: int = Field(default=3, ge=1, le=5)
    focus: str = Field(default="", max_length=300)


class TestScenario(BaseModel):
    name: str
    task: str
    expected_behavior: str
    must_contain: list[str] = []
    must_call_tool: str | None = None


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class Example(_Strict):
    """One executable expectation. For MCP tools and scripts `args` are the call arguments; the checks run against the real thing."""
    args: dict[str, Any]
    expect_contains: list[str] = []
    expect_not_contains: list[str] = []
    expect_error: str | None = None


class ConfigKey(_Strict):
    key: str
    description: str
    secret: bool = False                       # secrets hold the NAME of an environment variable (key must end in _env), never the value
    example: Any = ""


class Fixtures(_Strict):
    """Declarative test data (no code): a SQLite script, files, canned HTTP routes, env values and the test config (placeholders: {{sqlite_path}},
    {{docs_dir}}, {{http_base}}, {{tmp}})."""
    sqlite_sql: str | None = None
    files: dict[str, str] = {}
    http: dict[str, Any] = {}
    env: dict[str, str] = {}
    config: dict[str, Any] = {}


class McpToolSpec(_Strict):
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = True
    handler_source: str                        # defines handle(args, ctx) -> JSON-serialisable; may raise nx_mcp.ToolError for expected problems
    examples: list[Example]


class McpServerSpec(_Strict):
    name: str
    description: str
    blocks: list[str] = []
    packages: list[str] = []
    config_schema: list[ConfigKey] = []
    setup_source: str = "def setup(config):\n    return {}"          # builds the shared context (db, guards, audit) from the config
    fixtures: Fixtures = Fixtures()
    tools: list[McpToolSpec]
    instructions: str = ""


class ScriptSpec(_Strict):
    name: str
    description: str
    blocks: list[str] = []
    handler_source: str                        # defines handle(args) -> JSON-serialisable; may raise nx_cli.UserError for bad input
    examples: list[Example]
    fixtures: Fixtures = Fixtures()
    inputs: dict[str, Any] = {}                # documented input shape (shown in the README and the wrapper skill)


class SkillRef(_Strict):
    name: str
    content: str


class SkillSpec(_Strict):
    name: str
    description: str
    argument_hint: str = ""
    body: str
    references: list[SkillRef] = []
    allowed_tools: list[str] = []              # Read, Grep, Glob only
    uses: list[str] = []                       # script names, or "server/tool" for MCP tools


class SubagentSpec(_Strict):
    name: str
    description: str
    instructions: str
    tools: list[str] = ["Read", "Grep", "Glob"]


class AgentSpec(BaseModel):
    agent_name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    business_problem: str = Field(min_length=1)
    instructions: str = Field(min_length=20)
    capabilities: list[str] = Field(min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_tools: list[str] = Field(description="Subset of: calculator. Empty if none needed (the agent works on the customer's own files and systems).")
    data_sources: list[str]
    safety_restrictions: list[str] = Field(min_length=1)
    test_scenarios: list[TestScenario] = Field(min_length=1)
    success_criteria: list[str] = Field(min_length=1)
    # Structured deliverables (optional: specs without them generate the original layout). See docs/specs/009.
    kind: str | None = None
    skills: list[SkillSpec] = []
    subagents: list[SubagentSpec] = []
    scripts: list[ScriptSpec] = []
    mcp_servers: list[McpServerSpec] = []

    @model_validator(mode="after")
    def _static_gate(self):
        if self.skills or self.subagents or self.scripts or self.mcp_servers:
            from .artifacts import validate
            problems = validate.check_spec(self.model_dump())
            if problems:
                raise ValueError("; ".join(problems[:12]))
        return self


class RunIn(BaseModel):
    task: str = Field(min_length=1, max_length=4000)


class LoginIn(BaseModel):
    password: str


class ProviderIn(BaseModel):
    provider: Literal["anthropic", "openai", "claude_subscription"]


class ConfirmIn(BaseModel):
    confirm: bool = False


class DeliverIn(BaseModel):
    confirm: bool = False
    invite: bool = True
    existing_repo_ack: bool = False   # required when the agent is added to a repo that already exists
    overwrite_dev: bool = False
    unverified_code_ack: bool = False   # required when the delivery contains code whose checks have not passed


class AttachIn(BaseModel):
    customer_id: str
    confirm: bool = False


class SyncIn(BaseModel):
    confirm: bool = False
    overwrite_conflicts: bool = False
    overwrite_dev: bool = False       # replace commits on the repo's dev branch that Nexara did not make
    unverified_code_ack: bool = False


class ReviseIn(BaseModel):
    instruction: str = Field(min_length=3, max_length=1500)


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class OnboardingIn(BaseModel):
    step: Literal["cloned", "first_use", "done"]
    value: bool = True


class CollaboratorIn(BaseModel):
    username: str
    confirm: bool = False

    @field_validator("username")
    @classmethod
    def _github(cls, v: str) -> str:
        v = v.strip().lstrip("@")
        if not GITHUB_USER_RE.match(v):
            raise ValueError("invalid GitHub username")
        return v


class VerifyIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class ManualVerifyIn(BaseModel):
    confirm: bool = False
    note: str = Field(min_length=5, max_length=500)    # how you verified them (e.g. "confirmed by phone on 11 Oct")
