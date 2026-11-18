import threading
from langgraph.types import Command
from code_assistant.agent_v2.engine import AgentEngine, initial
from code_assistant.service.contracts import TaskRequest
from code_assistant.persistence.database import LeaseLost, Conflict
from code_assistant.memory.store import MemoryStore
from code_assistant.utils.docker_sandbox import DockerSandbox


class TaskService:
    """One fenced execution per task. Both worker and CLI use this service."""
    def __init__(self, repository, checkpointer, *, engine_options=None):
        self.repo, self.checkpointer = repository, checkpointer
        self.engine_options = engine_options or {}

    def run(self, task_id, fence):
        stopped, lost = threading.Event(), threading.Event()
        def heartbeat():
            while not stopped.wait(10):
                try:
                    self.repo.heartbeat(task_id, fence)
                    self.repo.worker_alive("active-task")
                except Exception:
                    lost.set()
                    return
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        def cancelled():
            return lost.is_set() or self.repo.cancelled(task_id, fence)
        try:
            row = self.repo.get(task_id)
            memory = MemoryStore(self.repo, allow_external=row["request"].get("knowledge", {}).get("allow_external", False))
            import hashlib
            from code_assistant.skills.registry import SkillRegistry
            image_identity = DockerSandbox().image_identity() or "unavailable"
            skill_identity = '|'.join(s['hash'] for s in SkillRegistry().catalog())
            environment = "v2:" + hashlib.sha256((image_identity + skill_identity).encode()).hexdigest()
            from code_assistant.knowledge.repository import KnowledgeRepository
            from code_assistant.knowledge.retriever import Retriever
            from code_assistant.knowledge.environment import library_version
            from sqlalchemy import inspect
            knowledge_repo = KnowledgeRepository(self.repo.engine)
            def identity():
                if not inspect(self.repo.engine).has_table("knowledge_snapshots"):
                    return "", ""
                sid = knowledge_repo.current()
                return sid, library_version() if sid else ""
            def retrieve(state, query, limit):
                settings = state.get("knowledge_config", {})
                result = Retriever(knowledge_repo).retrieve(query, state.get("knowledge_snapshot_id", ""),
                    state.get("knowledge_library_version", ""), mode=settings.get("mode", "rerank"),
                    limit=limit, budget=settings.get("context_chars", 6000),
                    allow_external=settings.get("allow_external", False))
                if inspect(self.repo.engine).has_table("retrieval_runs"):
                    result["run_id"] = knowledge_repo.record(task_id, f"retrieval-{state['action_count']}",
                        state.get("knowledge_snapshot_id", ""), query, result)
                return result
            engine_options = {"knowledge_identity": identity, "knowledge": retrieve, **self.engine_options}
            engine = AgentEngine(lambda: TaskRequest.model_validate(self.repo.get(task_id)["request"]),
                cancelled=cancelled,
                journal=lambda key, fn: self.repo.action(task_id, fence, key, fn),
                emit=lambda kind, value: self.repo.event(task_id, fence, kind, value),
                save_artifact=lambda key, content: self.repo.artifact(task_id, fence, key, content),
                memories=lambda goal: memory.search(goal, row["owner"], environment, task_id),
                **engine_options)
            graph = engine.build(self.checkpointer)
            config = {"configurable": {"thread_id": task_id}, "recursion_limit": 100}
            checkpoint = graph.get_state(config)
            if row["resume_value"]:
                argument = Command(resume=row["resume_value"])
            elif checkpoint.values:
                argument = None
            else:
                argument = initial(TaskRequest.model_validate(row["request"]))
            if cancelled():
                self.repo.finish(task_id, fence, "cancelled", {"reason": "cancelled", "result_level": "unverified"})
                return
            graph.invoke(argument, config)
            snapshot = graph.get_state(config)
            state = snapshot.values
            pending = [i for task in snapshot.tasks for i in task.interrupts]
            result = {k: state.get(k) for k in ("reason", "result_level", "plan", "plan_version", "observations",
                "llm_calls", "budget_tokens", "call_history", "verified_hash", "artifacts", "replan_count",
                "knowledge_snapshot_id", "knowledge_library_version", "knowledge_refs", "retrieval_history", "retrieval_usage")}
            if pending:
                result.update(interrupt_id=pending[0].id, pending_input=pending[0].value)
                status = "waiting_input"
            else:
                status = "succeeded" if state.get("reason") in {"tests_passed", "diagnosis_complete"} else "failed"
            self.repo.finish(task_id, fence, status, result)
            if status == "succeeded" and result["result_level"] == "verified" and row["request"].get("use_memory", True):
                errors = [o.get("error_type") for o in state["observations"] if o.get("error_type")]
                # Store compact structural experience, not tasks, answers or candidate stdout.
                summary = "Python solve(payload); errors=" + ",".join(errors) + "; skills=" + ",".join(
                    o.get("skill_id", "") for o in state["observations"]) + "; trusted cases passed."
                if state.get("repair_notes"):
                    from code_assistant.memory.store import sanitize_note
                    summary += " " + sanitize_note(str(state["repair_notes"][-1]))
                try:
                    memory.remember(task_id, summary, environment)
                except Exception:
                    # Memory maintenance must not rewrite an already verified task result.
                    pass
        except LeaseLost:
            return  # Another worker owns the task; never overwrite its result.
        except Exception as exc:
            try:
                self.repo.finish(task_id, fence, "failed", {"reason": "unknown_action_outcome" if isinstance(exc, Conflict) else "internal_error",
                    "error_class": type(exc).__name__, "result_level": "unverified"})
            except LeaseLost:
                pass
        finally:
            # Separate append-only diagnostics: do not rewrite a completed/fenced task result.
            if "memory" in locals() and "knowledge_repo" in locals():
                ledger = getattr(memory.embedder, "ledger", [])
                if ledger:
                    try:
                        knowledge_repo.record(task_id, f"memory-usage-{fence}", "", "", {
                            "mode": "memory_embedding", "usage": ledger, "evidence": [], "fallback": []})
                    except Exception:
                        pass
            stopped.set()
            thread.join(timeout=2)
