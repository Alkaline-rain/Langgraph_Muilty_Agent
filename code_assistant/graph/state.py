from typing_extensions import TypedDict


class GraphState(TypedDict):
    # 用户输入
    question: str  # 用户原始问题
    messages: list  # 对话历史
    context: str  # LCEL 文档全文（外部传入）
    task: dict  # 人工维护的 TaskSpec，预期输出不进入 Agent 提示词
    task_success: bool  # 仅可信用例全部通过时为 True
    acceptance: dict  # 本轮验收明细
    termination_reason: str
    run_config: dict
    llm_calls: int
    budget_tokens: int
    call_history: list[dict]
    attempt_history: list[dict]
    error_counts: dict[str, int]
    repeated_error_count: int
    repair_strategy: str

    # Coder 产出
    generation: dict  # 当前轮的 CodeSolution
    generation_history: list[dict]  # 所有历史生成记录

    # 执行结果（非 LLM 节点产出）
    exec_success: bool  # 兼容字段：与可信验收结果同步，不代表仅无异常
    exec_error: str  # 报错信息（traceback 截取关键部分）
    # 错误类型："" / syntax_error / import_error / runtime_error
    #          / security_error / timeout_error / resource_error / sandbox_error
    exec_error_type: str
    exec_stdout: str  # 执行期间的标准输出
    sandbox: str  # 实际使用的沙箱：docker

    # Reviewer 产出
    review: dict  # 当前轮的 ReviewResult
    review_history: list[dict]  # 所有历史审查记录

    # Reflector 产出
    reflection: dict  # 当前轮的 ReflectionResult
    reflection_history: list[dict]  # 所有历史反思记录

    # 结构化失败轨迹（对应需求 4）：记录每轮的代码版本、错误类型、
    # 审查意见、修复策略，注入下一轮生成，减少重复错误
    failure_trail: list[dict]  # 元素为 FailureRecord.dump()

    # Workflow 执行轨迹（对应需求 5）：记录节点执行状态、路由路径、修复次数
    trace: list[dict]  # 元素为 TraceEvent.dump()，含 node / route 两类事件
    route_path: list[str]  # 按序记录访问过的节点名（即路由路径）

    # 控制字段
    iteration: int  # 迭代次数
    max_iteration: int  # 最大允许的迭代次数（外部传入）


"""
1. question 独立存储：用户原始问题需要被每个 Agent 用到，不能淹没在 messages 里。
2. 历史记录用 list：Coder 需要看到自己之前犯过的所有错误，避免反复踩坑。
3. exec_error_type 分类：不同的错误类型可能需要不同的处理策略（导包失败 vs 语法错误 vs 运行时逻辑错误）。
4. max_iterations 从外部传入：不要在图内部硬编码，提高灵活性。
5. failure_trail / trace / route_path：把「失败轨迹」和「执行轨迹」显式落到状态里，
   既用于下一轮反馈注入，也用于调试分析（对应需求 4、5）。
"""
