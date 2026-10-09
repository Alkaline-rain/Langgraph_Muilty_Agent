"""有限预算的生成、可信验收、审查与反思节点。"""
import json
from pydantic import ValidationError
from agents.coder import invoke_coder
from agents.reviewer import invoke_reviewer
from agents.reflector import invoke_reflector
from schemas.models import TaskSpec, AcceptanceResult, FailureRecord
from utils.acceptance import evaluate
from utils.llm_runtime import call_agent
from utils.repair_policy import clip, feedback, summary, fingerprint, code_fingerprint, strategy
from utils.trace import record_node, record_route
from graph.edges import termination_reason


def _enter(state, node, status="ok", **details):
    previous = state["route_path"][-1] if state["route_path"] else "START"
    trace = record_route(state["trace"], previous, node, state.get("repair_strategy") or "workflow")
    return {"route_path": state["route_path"] + [node],
            "trace": record_node(trace, node, status, **details)}


def _finish(state, update, node):
    merged = {**state, **update}
    reason = termination_reason(merged)
    if reason:
        update["termination_reason"] = reason
        update["trace"] = record_route(update.get("trace", state["trace"]), node, "END", reason)
    return update


def _agent_failure(state, update, node, reason, message):
    update.update(_enter(state, node, "fail", reason=reason))
    update.update(termination_reason=reason, exec_error_type=reason, exec_error=message)
    return _finish(state, update, node)


def generate(state, agent=None):
    solution, update, reason, message = call_agent(state, "coder", agent or invoke_coder, {
        "context": clip(state["context"], 4000), "question": state["question"], "feedback": feedback(state)})
    if reason:
        return _agent_failure(state, update, "generate", reason, message)
    generation = solution.model_dump()
    update.update(_enter(state, "generate", iteration=state["iteration"] + 1))
    update.update(generation=generation, generation_history=state["generation_history"] + [generation],
                  iteration=state["iteration"] + 1, review={}, reflection={}, termination_reason="")
    return update


def execute(state, evaluator=None):
    generation = state["generation"]
    try:
        task = TaskSpec.model_validate(state.get("task", {}))
    except ValidationError:
        result = AcceptanceResult(success=False, error_type="task_error", error_message="需要非空可信离线验收用例")
    else:
        try:
            result = (evaluator or evaluate)(generation["imports"], generation["code"], task)
        except Exception as exc:
            result = AcceptanceResult(success=False, error_type="internal_error",
                                      error_message=f"执行器内部异常：{type(exc).__name__}")
    counts = dict(state["error_counts"])
    fp = ""
    code_fp = code_fingerprint(generation)
    if not result.success:
        failed_case = next((c.name for c in result.cases if not c.passed), "")
        fp = fingerprint(result.error_type, result.error_message, failed_case)
        counts[fp] = counts.get(fp, 0) + 1
    repeated = counts.get(fp, 0)
    attempt = {"iteration": state["iteration"], "success": result.success,
               "error_type": result.error_type, "fingerprint": fp, "code_fingerprint": code_fp,
               "repeat_count": repeated, "duration_ms": result.duration_ms,
               "cases_run": len(result.cases), "acceptance": result.model_dump()}
    update = _enter(state, "execute", "ok" if result.success else "fail", **attempt)
    update.update(exec_success=result.success, task_success=result.success,
                  exec_error=result.error_message, exec_error_type=result.error_type,
                  exec_stdout="", sandbox=result.sandbox, acceptance=result.model_dump(),
                  error_counts=counts, repeated_error_count=repeated,
                  attempt_history=state["attempt_history"] + [attempt])
    update["repair_strategy"] = strategy({**state, **update}) if not result.success else ""
    if not result.success:
        record = FailureRecord(iteration=state["iteration"], error_type=result.error_type,
            error_message=result.error_message, fingerprint=fp, code_fingerprint=code_fp,
            repeat_count=repeated, strategy=update["repair_strategy"]).model_dump()
        update["failure_trail"] = state["failure_trail"] + [record]
    return _finish(state, update, "execute")


def _enrich_failure(state, fields):
    records = list(state["failure_trail"])
    if records:
        records[-1] = {**records[-1], **fields}
    return records


def review(state, agent=None):
    limit = state["run_config"]["feedback_chars"]
    generation = state["generation"]
    result, update, reason, message = call_agent(state, "reviewer", agent or invoke_reviewer, {
        "question": state["question"],
        "code_to_review": clip(generation["imports"] + "\n" + generation["code"], limit),
        "exec_result": clip(f"可信验收失败，passes不能覆盖此结果。策略：{state['repair_strategy']}。"
                            f"{state['exec_error_type']}: {state['exec_error']}", 1500)})
    if reason:
        return _agent_failure(state, update, "review", reason, message)
    data = result.model_dump()
    update.update(_enter(state, "review", passes=result.passes))
    update.update(review=data, review_history=state["review_history"] + [data],
                  failure_trail=_enrich_failure(state, {"review_issues": result.issues, "review_suggestions": result.suggestions}))
    return _finish(state, update, "review")


def reflect(state, agent=None):
    data = state["review"]
    result, update, reason, message = call_agent(state, "reflector", agent or invoke_reflector, {
        "question": state["question"], "iteration": state["iteration"],
        "issues": [clip(str(x), 500) for x in data.get("issues", [])[:5]],
        "suggestions": [clip(str(x), 500) for x in data.get("suggestions", [])[:5]],
        "attempt_summary": state["repair_strategy"] + "\n" + summary(state)})
    if reason:
        return _agent_failure(state, update, "reflect", reason, message)
    data = result.model_dump()
    update.update(_enter(state, "reflect"))
    update.update(reflection=data, reflection_history=state["reflection_history"] + [data],
                  failure_trail=_enrich_failure(state, data))
    return _finish(state, update, "reflect")
