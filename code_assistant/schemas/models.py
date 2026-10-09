"""所有 Agent 的结构化输出模型（Pydantic Schema）。

每个 Agent 拥有独立的输出 Schema，绝不互相复用：
- CodeSolution     -> Coder 输出代码方案
- ReviewResult     -> Reviewer 输出审查意见
- ReflectionResult -> Reflector 输出反思策略

此外，跨 Agent 的「机器数据」也有统一 Schema（对应需求 2/4/5）：
- ExecutionResult  -> 沙箱执行器输出（统一成功/失败/错误分类格式）
- FailureRecord    -> 单次失败尝试的结构化轨迹（注入下一轮生成）
- TraceEvent       -> Workflow 执行轨迹事件（节点状态 / 路由决策）
"""

from pydantic import BaseModel, Field
from typing import Literal
from pydantic import ConfigDict, JsonValue, model_validator
import json


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: Literal["single", "direct", "full", "adaptive"] = "adaptive"
    max_calls: int = Field(default=9, ge=1, le=100)
    max_total_tokens: int = Field(default=150000, ge=1)
    max_output_tokens: int = Field(default=2048, ge=128, le=16384)
    prompt_chars: int = Field(default=20000, ge=1000)
    feedback_chars: int = Field(default=6000, ge=1000)
    history_window: int = Field(default=3, ge=1, le=20)
    repeat_limit: int = Field(default=3, ge=2, le=20)


class AcceptanceCase(BaseModel):
    """人工提供的 JSON 黑盒用例；expected 不进入候选代码容器。"""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    input: JsonValue
    expected: JsonValue


    @model_validator(mode="after")
    def validate_json_size(self):
        for value in (self.input, self.expected):
            if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 64 * 1024:
                raise ValueError("单个用例输入/输出不得超过 64 KiB，数值必须有限")
        return self


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1)
    execution_mode: Literal["offline"] = "offline"
    cases: list[AcceptanceCase] = Field(min_length=1, max_length=50)


class CaseResult(BaseModel):
    name: str
    passed: bool
    error_type: str = ""
    error_message: str = ""
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0


class AcceptanceResult(BaseModel):
    success: bool
    error_type: str = ""
    error_message: str = ""
    cases: list[CaseResult] = Field(default_factory=list)
    duration_ms: int = 0
    sandbox: str = "docker"


class CodeSolution(BaseModel):
    """Coder Agent 输出的代码解决方案。"""

    prefix: str = Field(description="解决方案思路和描述")
    imports: str = Field(description="所有导入语句")
    code: str = Field(description="不包含导入语句的完整可执行代码块")


class ReviewResult(BaseModel):
    """Reviewer Agent 输出的审查结果。"""

    passes: bool = Field(description="代码是否通过审查")
    issues: list[str] = Field(description="发现的问题列表")
    suggestions: list[str] = Field(description="具体的修改建议列表")


class ReflectionResult(BaseModel):
    """Reflector Agent 输出的反思结果。"""

    root_cause: str = Field(description="代码生成失败的根本原因")
    new_approach: str = Field(description="解决问题的新思路")
    key_changes: list[str] = Field(description="下一次生成需要做出的具体改变")


class ExecutionResult(BaseModel):
    """沙箱执行器的统一返回格式。

    不可信代码仅允许 Docker 后端，统一执行错误分类与输出。
    """

    success: bool = Field(description="代码是否全部执行成功")
    # 错误类型："" / syntax_error / import_error / runtime_error
    #          / security_error / timeout_error / resource_error / sandbox_error
    error_type: str = Field(default="", description="错误分类")
    error_message: str = Field(default="", description="错误信息（截取关键部分）")
    stdout: str = Field(default="", description="执行期间的标准输出")
    stderr: str = Field(default="", description="执行期间的诊断输出")
    returncode: int | None = None
    sandbox: str = Field(default="docker", description="实际执行后端")
    duration_ms: int = Field(default=0, description="执行耗时（毫秒）")


class FailureRecord(BaseModel):
    """一次失败尝试的完整结构化轨迹（对应需求 4）。

    记录：代码版本标识（迭代轮次）、错误类型、报错信息、审查意见、
    修复策略（反思结论）。这些信息会被注入下一轮 Coder 的反馈中，
    以减少重复错误和无效迭代。
    """

    iteration: int = Field(description="代码版本标识（第几轮生成）")
    fingerprint: str = ""
    code_fingerprint: str = ""
    repeat_count: int = 1
    strategy: str = ""
    error_type: str = Field(default="", description="错误分类")
    error_message: str = Field(default="", description="报错信息")
    review_issues: list[str] = Field(default_factory=list, description="审查发现的问题")
    review_suggestions: list[str] = Field(default_factory=list, description="审查给出的修改建议")
    root_cause: str = Field(default="", description="反思得到的根本原因")
    new_approach: str = Field(default="", description="反思给出的新思路")
    key_changes: list[str] = Field(default_factory=list, description="反思给出的具体修改点")


class TraceEvent(BaseModel):
    """一条 Workflow 执行轨迹事件（对应需求 5）。"""

    timestamp: str = Field(description="事件发生时间（ISO 格式）")
    kind: str = Field(description="事件类型：node=节点执行；route=路由决策")
    node: str = Field(description="事件所属节点名")
    status: str = Field(description="执行状态：ok | fail")
    detail: dict = Field(default_factory=dict, description="附加关键数据（错误类型、路由目标等）")
