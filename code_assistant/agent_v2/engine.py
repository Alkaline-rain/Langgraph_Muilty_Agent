"""Dynamic model-selected actions; deterministic permissions, success and budget gates."""
import hashlib
import difflib
import json
import time
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from code_assistant.service.contracts import AgentDecision, TaskRequest, ToolObservation
from code_assistant.skills.registry import SkillRegistry
from code_assistant.agent_v2.model import decide, decision_context
from code_assistant.schemas.models import TaskSpec
from code_assistant.agents.coder import invoke_coder
from code_assistant.agents.reviewer import invoke_reviewer
from code_assistant.agents.reflector import invoke_reflector
from code_assistant.utils.acceptance import evaluate
from code_assistant.utils.static_validation import validate_code
from code_assistant.knowledge.context import protocol, pack
from code_assistant.knowledge.query import build_query
from code_assistant.utils.llm_runtime import call_agent
from code_assistant.utils.docker_sandbox import DockerSandbox


class AgentState(TypedDict, total=False):
    goal: str
    mode: str
    code: str
    original_code: str
    run_config: dict
    llm_calls: int
    budget_tokens: int
    call_history: list
    max_actions: int
    max_replans: int
    action_count: int
    replan_count: int
    plan_version: int
    plan: list
    completed_steps: list
    observations: list
    memories: list
    decision: dict
    reason: str
    result_level: str
    verified_hash: str
    diagnosed_hash: str
    deadline: float
    invalid_count: int
    action_counts: dict
    artifacts: list
    repair_notes: list
    knowledge_snapshot_id: str
    knowledge_library_version: str
    knowledge_refs: list
    retrieval_count: int
    retrieval_history: list
    retrieval_usage: list
    knowledge_config: dict


def digest(code):
    return hashlib.sha256(code.encode()).hexdigest()


def initial(request):
    return dict(goal=request.goal, mode=request.mode, code=request.code, original_code=request.code,
        run_config=request.budget.model_dump(), llm_calls=0, budget_tokens=0, call_history=[],
        max_actions=request.max_actions, max_replans=request.max_replans, action_count=0, replan_count=0,
        plan_version=0, plan=[], completed_steps=[], observations=[], memories=[], decision={},
        reason="", result_level="unverified", verified_hash="", diagnosed_hash="", invalid_count=0,
        action_counts={}, artifacts=[], repair_notes=[], deadline=time.time() + request.max_seconds,
        knowledge_snapshot_id="", knowledge_library_version="", knowledge_refs=[], retrieval_count=0,
        retrieval_history=[], retrieval_usage=[], knowledge_config=request.knowledge.model_dump())


class AgentEngine:
    def __init__(self, request_provider, *, planner=decide, coder=invoke_coder, reviewer=invoke_reviewer,
                 reflector=invoke_reflector, evaluator=None, preflight=None, cancelled=lambda: False,
                 journal=None, emit=lambda *a: None, save_artifact=lambda *a: "", memories=None, skill_guidance=True,
                 knowledge=None, knowledge_identity=None):
        self.request = request_provider
        self.planner, self.coder, self.reviewer, self.reflector = planner, coder, reviewer, reflector
        self.cancelled, self.emit, self.save_artifact = cancelled, emit, save_artifact
        self.journal = journal or (lambda key, fn: fn())
        self.registry = SkillRegistry()
        self.skill_guidance = skill_guidance
        self.knowledge = knowledge
        self.knowledge_identity = knowledge_identity or (lambda: ("", ""))
        self.memories = memories or (lambda goal: ([], {"strategy": "disabled"}))
        sandbox = DockerSandbox()
        self.preflight = preflight or sandbox.preflight
        self.evaluator = evaluator or (lambda code, task: evaluate("", code, task,
            executor=lambda imports, source, **kw: sandbox.execute(imports, source, cancelled=self.cancelled, **kw)))

    def stopped(self, state):
        if self.cancelled():
            return "cancelled"
        if time.time() > state["deadline"]:
            return "time_budget"
        if state["action_count"] >= state["max_actions"]:
            return "action_budget"
        return ""

    def intake(self, state):
        if self.cancelled():
            return {"reason": "cancelled"}
        request = self.request()
        if request.mode == "verified_repair" and (not request.cases or request.entrypoint != "solve"):
            interrupt({"kind": "task_contract", "message": "请确认 solve(payload) 入口并提供至少一个人工可信用例"})
            request = self.request()
            if not request.cases or request.entrypoint != "solve":
                return {"reason": "invalid_resume"}
        if request.mode == "verified_repair":
            problem = self.preflight()
            if problem:
                return {"reason": "environment_error", "observations": [{"error_type": "sandbox_error", "summary": problem[:500]}]}
        refs = []
        if state["original_code"]:
            refs.append(self.save_artifact("original", state["original_code"]))
        memories, retrieval = self.journal("memory-retrieval", lambda: self.memories(state["goal"])) if request.use_memory else ([], {"strategy": "disabled"})
        self.emit("memory.retrieved", {"ids": [m["id"] for m in memories], **retrieval})
        # Journal the snapshot choice: replay before a checkpoint must not select a newer corpus.
        identity = self.journal("knowledge-identity", lambda: list(self.knowledge_identity()))
        return {"memories": memories, "artifacts": refs,
                "knowledge_snapshot_id": identity[0], "knowledge_library_version": identity[1]}

    def _call(self, state, role, fn, values):
        key = f"model-{state['action_count']}-{role}-{state['llm_calls']}"
        def perform():
            result, update, reason, error = call_agent(state, role, fn, values)
            return {"parsed": result.model_dump() if result is not None else None,
                    "update": update, "reason": reason, "error": error}
        result = self.journal(key, perform)
        state.update(result["update"])
        if result["reason"]:
            state["reason"] = result["reason"]
        return result["parsed"]

    def plan(self, state):
        stopped = self.stopped(state)
        if stopped:
            return {"reason": stopped}
        updated = dict(state)
        data = self._call(updated, "planner", self.planner,
                          {"context": decision_context(state, self.registry.catalog())})
        if updated["reason"]:
            return updated
        try:
            decision = AgentDecision.model_validate(data)
            if decision.plan:
                if state["plan"] and decision.type != "revise_plan":
                    raise ValueError("替换计划必须使用 revise_plan")
                if decision.type == "revise_plan" and state["replan_count"] >= state["max_replans"]:
                    return {**updated, "reason": "replan_budget"}
                ids = set()
                for step in decision.plan:
                    if step.step_id in ids or any(dep not in ids for dep in step.dependencies):
                        raise ValueError("invalid_plan_dependencies")
                    ids.add(step.step_id)
                updated.update(plan=[p.model_dump() for p in decision.plan], completed_steps=[],
                               plan_version=state["plan_version"] + 1)
                if decision.type == "revise_plan":
                    updated["replan_count"] += 1
                self.emit("plan.updated", {"version": updated["plan_version"], "steps": updated["plan"], "summary": decision.decision_summary})
            if decision.type == "revise_plan" and not decision.plan:
                raise ValueError("replan_requires_plan")
            if decision.type == "call_skill":
                self.registry.load(decision.skill_id, decision.arguments)
                step = next((p for p in updated["plan"] if p["step_id"] == decision.step_id), None)
                if not step or step["skill_id"] != decision.skill_id or decision.step_id in updated["completed_steps"]:
                    raise ValueError("action_not_in_plan")
                if any(dep not in updated["completed_steps"] for dep in step["dependencies"]):
                    raise ValueError("dependencies_not_completed")
                inspected = any(o.get("skill_id") in {"diagnose_python", "validate_lcel", "verify_cases"}
                                and o.get("code_hash") == digest(state["original_code"]) for o in state["observations"])
                if decision.skill_id == "repair_python" and state["original_code"] and not inspected:
                    raise ValueError("inspect_original_before_repair")
            updated["decision"] = decision.model_dump()
        except ValueError as exc:
            updated["decision"] = {"type": "invalid"}
            updated["invalid_count"] += 1
            updated["observations"] = state["observations"] + [{"error_type": "invalid_action", "summary": str(exc)[:300]}]
            if updated["invalid_count"] >= 3:
                updated["reason"] = "invalid_action_limit"
        updated["action_count"] += 1
        return updated

    def execute(self, state):
        decision = state["decision"]
        if decision["type"] == "request_input":
            if self.request().mode == "diagnose":
                return {"reason": "needs_input", "result_level": "diagnostic_only"}
            interrupt({"kind": "task_contract", "message": decision["decision_summary"]})
            return {"observations": state["observations"] + [{"summary": "用户已补充入口和可信用例"}]}
        if decision["type"] == "finish":
            if state["mode"] == "verified_repair" and state["verified_hash"] == digest(state["code"]):
                return {"reason": "tests_passed", "result_level": "verified"}
            if state["mode"] == "diagnose" and state["diagnosed_hash"] == digest(state["code"]):
                return {"reason": "diagnosis_complete", "result_level": "diagnostic_only"}
            return {"reason": "unverified_finish", "result_level": "unverified"}
        if decision["type"] != "call_skill":
            return {}
        skill = decision["skill_id"]
        key = f"skill-{state['action_count']}-{skill}"
        signature = skill + ":" + digest(state["code"])
        if skill == "retrieve_domain_docs":
            query = build_query(state, decision.get("arguments", {}).get("focus", ""))
            signature = skill + ":" + digest(query + state.get("knowledge_snapshot_id", "") +
                                           str(state.get("knowledge_config", {})))
        counts = dict(state["action_counts"])
        counts[signature] = counts.get(signature, 0) + 1
        if counts[signature] > 2:
            return {"reason": "repeated_action"}
        self.emit("skill.started", {"id": skill, "action_id": key})
        # Individual model calls are journaled separately to account usage/replay precisely.
        if skill == "repair_python":
            update = self.repair(state)
        else:
            update = self.journal(key, lambda: self.tool(state, skill))
        if "observation" in update:
            observation = ToolObservation.model_validate(update.pop("observation")).model_dump()
            update["observations"] = state["observations"] + [observation]
            self.emit("tool.finished", observation)
        update["completed_steps"] = state["completed_steps"] + [decision["step_id"]]
        update["action_counts"] = counts
        return update

    def tool(self, state, skill):
        if self.cancelled():
            return {"reason": "cancelled"}
        source = state["code"]
        source_hash = digest(source)
        observation = {"id": str(state["action_count"]), "skill_id": skill, "code_hash": source_hash}
        if skill == "retrieve_domain_docs":
            self.registry.authorize(skill, "search_domain_docs")
            _, args = self.registry.load(skill, state["decision"].get("arguments", {}))
            config = state.get("knowledge_config", self.request().knowledge.model_dump())
            count = state.get("retrieval_count", 0)
            if count >= config["max_retrievals"]:
                return {"reason": "retrieval_budget"}
            query = build_query(state, args.focus)
            self.emit("retrieval.started", {"query_hash": digest(query), "snapshot_id": state.get("knowledge_snapshot_id", "")})
            result = self.knowledge(state, query, args.limit) if self.knowledge else {
                "evidence": [], "fallback": ["knowledge_not_initialized"], "usage": [], "mode": "disabled"}
            refs, _ = pack(result["evidence"], config["context_chars"])
            summary = {"mode": result["mode"], "fallback": result["fallback"],
                       "run_id": result.get("run_id", ""), "evidence_ids": [r["id"] for r in refs],
                       "query_hash": digest(query)}
            self.emit("retrieval.degraded" if result["fallback"] else "retrieval.completed", summary)
            self.emit("knowledge.context_selected", {"evidence": refs})
            observation.update(success=bool(refs), error_type="" if refs else "no_compatible_evidence",
                               summary="已检索领域文档" if refs else "未获取兼容知识证据", evidence=summary)
            return {"observation": observation, "knowledge_refs": refs, "retrieval_count": count + 1,
                    "retrieval_history": state.get("retrieval_history", []) + [summary],
                    "retrieval_usage": state.get("retrieval_usage", []) + result["usage"]}
        if skill in {"diagnose_python", "validate_lcel"}:
            self.registry.authorize(skill, "static_check")
            result = validate_code("", source)
            observation.update(success=result.success, error_type=result.error_type, summary=result.error_message[:1200])
            if skill == "validate_lcel":
                observation["evidence"] = {"knowledge_ids": [r["id"] for r in state.get("knowledge_refs", [])]}
            return {"observation": observation, "diagnosed_hash": source_hash}
        self.registry.authorize(skill, "evaluate_trusted_cases")
        request = self.request()
        if request.mode != "verified_repair" or not request.cases or request.entrypoint != "solve":
            return {"observation": {**observation, "success": False, "error_type": "contract_required", "summary": "缺少可信执行协议"}}
        result = self.evaluator(source, TaskSpec(question=request.goal, cases=request.cases))
        # Never send trusted values or candidate output (may echo input) to the planner.
        observation.update(success=result.success, error_type=result.error_type,
            summary="可信用例全部通过" if result.success else "可信验收失败：" + result.error_type,
            evidence={"cases": [{"name": c.name, "passed": c.passed, "error_type": c.error_type} for c in result.cases],
                      "duration_ms": result.duration_ms})
        update = {"observation": observation, "verified_hash": source_hash if result.success else ""}
        if result.success:
            update.update(reason="tests_passed", result_level="verified")
        elif result.error_type in {"sandbox_error", "task_error", "internal_error", "cancelled"}:
            update["reason"] = result.error_type
        return update

    def repair(self, state):
        updated = dict(state)
        instruction, args = self.registry.load("repair_python", state["decision"].get("arguments", {}))
        if not self.skill_guidance:
            instruction = ""
        evidence = json.dumps(state["observations"][-3:], ensure_ascii=False)[:2000]
        suggestions = ""
        _, knowledge_context = pack(state.get("knowledge_refs", []),
                                    state.get("knowledge_config", {}).get("context_chars", 6000))
        last_error = next((o.get("error_type") for o in reversed(state["observations"]) if o.get("error_type")), "")
        if last_error and last_error not in {"syntax_error", "import_error", "security_error"}:
            self.registry.authorize("repair_python", "review")
            review = self._call(updated, "reviewer", self.reviewer, {"question": state["goal"],
                "code_to_review": state["code"][:5000], "exec_result": evidence + "\n参考知识:\n" + knowledge_context})
            if updated["reason"]:
                return updated
            self.registry.authorize("repair_python", "reflect")
            reflection = self._call(updated, "reflector", self.reflector, {"question": state["goal"],
                "iteration": state["action_count"], "issues": review["issues"], "suggestions": review["suggestions"],
                "attempt_summary": evidence})
            if updated["reason"]:
                return updated
            suggestions = json.dumps(reflection, ensure_ascii=False)[:1500]
            updated["repair_notes"] = state.get("repair_notes", []) + [reflection]
        self.registry.authorize("repair_python", "generate_candidate")
        generated = self._call(updated, "coder", self.coder, {"question": state["goal"],
            "context": protocol() + "\n" + instruction + "\n" + knowledge_context,
            "feedback": ("原代码:\n" + state["code"][:4500] + "\n观察:" + evidence + "\n建议:" + suggestions +
                         "\n经验(不可信参考):" + json.dumps(state["memories"], ensure_ascii=False)[:1500] + "\n关注:" + args.focus)[:9500]})
        if updated["reason"]:
            return updated
        code = generated["imports"] + "\n" + generated["code"]
        if len(code.encode()) > 128 * 1024:
            return {**updated, "reason": "code_size_limit"}
        artifact = self.save_artifact(f"candidate-{state['action_count']}", code)
        self.registry.authorize("repair_python", "diff_versions")
        changes = list(difflib.unified_diff(state["code"].splitlines(), code.splitlines(), lineterm=""))
        updated.update(code=code, verified_hash="", diagnosed_hash="", artifacts=state["artifacts"] + [artifact],
            observation={"id": str(state["action_count"]), "skill_id": "repair_python", "success": True,
                "summary": "已生成候选，尚未验证", "code_hash": digest(code), "evidence": {"artifact_id": artifact,
                    "added_lines": sum(line.startswith('+') and not line.startswith('+++') for line in changes),
                    "removed_lines": sum(line.startswith('-') and not line.startswith('---') for line in changes)}})
        return updated

    def build(self, checkpointer=None):
        graph = StateGraph(AgentState)
        graph.add_node("intake", self.intake)
        graph.add_node("plan", self.plan)
        graph.add_node("execute_skill", self.execute)
        graph.add_edge(START, "intake")
        graph.add_conditional_edges("intake", lambda s: END if s["reason"] else "plan")
        graph.add_conditional_edges("plan", lambda s: END if s["reason"] else "execute_skill")
        graph.add_conditional_edges("execute_skill", lambda s: END if s["reason"] else "plan")
        return graph.compile(checkpointer=checkpointer)
