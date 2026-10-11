import re
from typing import Any, Literal
from pydantic import BaseModel, EmailStr, Field, field_validator

AGENT_TYPES = ["Customer Support", "Sales", "Data Analyst", "Research", "Workflow Automation", "Custom"]
AgentType = Literal["Customer Support", "Sales", "Data Analyst", "Research", "Workflow Automation", "Custom"]


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


class TestScenario(BaseModel):
    name: str
    task: str
    expected_behavior: str
    must_contain: list[str] = []
    must_call_tool: str | None = None


class AgentSpec(BaseModel):
    agent_name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    business_problem: str = Field(min_length=1)
    instructions: str = Field(min_length=20)
    capabilities: list[str] = Field(min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_tools: list[str] = Field(description="Subset of: calculator, lookup_business_data. Empty if none needed.")
    data_sources: list[str]
    safety_restrictions: list[str] = Field(min_length=1)
    test_scenarios: list[TestScenario] = Field(min_length=1)
    success_criteria: list[str] = Field(min_length=1)


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


class AttachIn(BaseModel):
    customer_id: str
    confirm: bool = False


class SyncIn(BaseModel):
    confirm: bool = False
    overwrite_conflicts: bool = False


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
