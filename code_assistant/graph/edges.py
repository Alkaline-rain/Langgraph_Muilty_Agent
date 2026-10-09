"""策略路由：客观验收、预算及重复失败控制。"""
from graph.state import GraphState


def termination_reason(state: GraphState) -> str:
    if state.get("task_success"):
        return "tests_passed"
    if state.get("termination_reason"):
        return state["termination_reason"]
    error = state.get("exec_error_type")
    if error == "internal_error":
        return "internal_error"
    if error in {"sandbox_error", "task_error"}:
        return "environment_error" if error == "sandbox_error" else "invalid_task"
    config = state.get("run_config", {})
    if state["iteration"] >= state["max_iteration"]:
        return "max_iterations"
    if config.get("strategy") == "single" and state["iteration"]:
        return "single_attempt"
    if config.get("strategy") == "adaptive" and state.get("repeated_error_count", 0) >= config.get("repeat_limit", 3):
        return "repeated_failure"
    if state.get("llm_calls", 0) >= config.get("max_calls", 999):
        return "call_budget"
    if state.get("budget_tokens", 0) >= config.get("max_total_tokens", 10**9):
        return "token_budget"
    return ""


def route_after_generate(state):
    # 最后一笔调用产出的代码仍须执行验收，不能被调用预算拦截。
    return "end" if state.get("termination_reason") else "execute"


def route_after_execute(state: GraphState) -> str:
    if termination_reason(state):
        return "end"
    config = state.get("run_config", {})
    if config.get("strategy") == "direct":
        return "generate"
    if config.get("strategy") == "adaptive" and state.get("repeated_error_count", 0) < 2 and state.get("exec_error_type") in {"syntax_error", "import_error", "security_error"}:
        return "generate"
    return "review"


def route_after_review(state: GraphState) -> str:
    return "end" if termination_reason(state) else "reflect"


def route_after_reflect(state: GraphState) -> str:
    return "end" if termination_reason(state) else "generate"
