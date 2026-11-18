from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from code_assistant.schemas.models import AcceptanceCase, RunConfig
from code_assistant.knowledge.contracts import RetrievalConfig


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskRequest(StrictModel):
    goal: str = Field(min_length=1, max_length=8000)
    code: str = Field(default="", max_length=100000)
    language: Literal["python"] = "python"
    mode: Literal["diagnose", "verified_repair"] = "diagnose"
    source_type: Literal["cli", "web", "extension"] = "web"
    source_url: str = Field(default="", max_length=2000)
    entrypoint: Literal["", "solve"] = ""
    cases: list[AcceptanceCase] = Field(default_factory=list, max_length=50)
    budget: RunConfig = Field(default_factory=RunConfig)
    max_actions: int = Field(default=12, ge=1, le=30)
    max_replans: int = Field(default=3, ge=0, le=5)
    max_seconds: int = Field(default=600, ge=10, le=1800)
    use_memory: bool = True
    knowledge: RetrievalConfig = Field(default_factory=RetrievalConfig)

    @model_validator(mode="after")
    def limits(self):
        if self.budget.max_calls > 20 or self.budget.max_total_tokens > 300000:
            raise ValueError("平台单任务最多 20 次调用、300000 预算 Token")
        if self.budget.prompt_chars > 30000 or self.budget.feedback_chars > 10000:
            raise ValueError("提示词/反馈长度超出平台上限")
        return self

    def agent_view(self):
        # Trusted answers and raw page URL are never model context.
        return self.model_dump(exclude={"cases", "source_url", "budget"}) | {"has_cases": bool(self.cases)}


class ResumeRequest(StrictModel):
    version: int = Field(ge=0)
    interrupt_id: str
    entrypoint: Literal["solve"] = "solve"
    cases: list[AcceptanceCase] = Field(min_length=1, max_length=50)


class ToolObservation(StrictModel):
    id: str
    skill_id: str
    success: bool
    error_type: str = ""
    summary: str = ""
    evidence: dict = Field(default_factory=dict)
    code_hash: str = ""


class PlanStep(StrictModel):
    step_id: str = Field(min_length=1, max_length=50)
    objective: str = Field(min_length=1, max_length=300)
    skill_id: Literal["diagnose_python", "repair_python", "validate_lcel", "verify_cases", "retrieve_domain_docs"]
    dependencies: list[str] = Field(default_factory=list, max_length=10)
    success_criteria: str = Field(max_length=300)


class AgentDecision(StrictModel):
    type: Literal["call_skill", "revise_plan", "request_input", "finish"]
    skill_id: str = ""
    step_id: str = ""
    arguments: dict = Field(default_factory=dict)
    decision_summary: str = Field(max_length=500)
    plan: list[PlanStep] = Field(default_factory=list, max_length=10)


class SkillArguments(StrictModel):
    focus: str = Field(default="", max_length=1000)
