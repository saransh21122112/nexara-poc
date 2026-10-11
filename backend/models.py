from typing import Any, Literal
from pydantic import BaseModel, EmailStr, Field, field_validator

AGENT_TYPES = ["Customer Support", "Sales", "Data Analyst", "Research", "Workflow Automation", "Custom"]
AgentType = Literal["Customer Support", "Sales", "Data Analyst", "Research", "Workflow Automation", "Custom"]


class RequestIn(BaseModel):
    company_name: str = Field(min_length=1, max_length=120)
    industry: str = Field(min_length=1, max_length=120)
    agent_type: AgentType
    business_problem: str = Field(min_length=10, max_length=4000)
    tasks: str = Field(min_length=5, max_length=4000)
    existing_tools: str = Field(min_length=1, max_length=2000)
    required_output: str = Field(min_length=3, max_length=2000)
    contact_email: EmailStr

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


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
