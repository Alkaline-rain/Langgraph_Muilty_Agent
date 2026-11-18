import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from code_assistant.agent_v2.engine import AgentEngine, initial
from code_assistant.service.contracts import TaskRequest, AgentDecision, ResumeRequest
from code_assistant.schemas.models import AcceptanceResult, CodeSolution, ReviewResult, ReflectionResult
from code_assistant.persistence.database import Repository, Conflict, LeaseLost, checkpoint_store
from code_assistant.service.tasks import TaskService
from code_assistant.skills.registry import SkillRegistry
from code_assistant.memory.store import MemoryStore
from backend.app.main import create_app

GOOD = "def solve(payload):\n    return payload['a'] + payload['b']"
BAD = "def solve(payload):\n    return payload['a'] - payload['b']"
CASE = {"name": "example", "input": {"a": 2, "b": 3}, "expected": 987654321}


def plan_step(id, skill):
    return {"step_id": id, "objective": skill, "skill_id": skill, "dependencies": [], "success_criteria": "观察工具结果"}


def planner(context):
    data = json.loads(context)
    if not data["plan"]:
        skill = "diagnose_python" if data["mode"] == "diagnose" else "verify_cases"
        return AgentDecision(type="call_skill", skill_id=skill, step_id="inspect", decision_summary="先检查输入代码",
                             plan=[plan_step("inspect", skill)])
    last = data["observations"][-1]
    if data["mode"] == "diagnose":
        return AgentDecision(type="finish", decision_summary="诊断已完成")
    if data["plan"][0]["step_id"] == "inspect":
        return AgentDecision(type="revise_plan", decision_summary="错误结果需要修复", plan=[plan_step("repair", "repair_python"), plan_step("verify", "verify_cases")])
    if "repair" not in data["completed_steps"]:
        return AgentDecision(type="call_skill", skill_id="repair_python", step_id="repair", decision_summary="按观察修复")
    return AgentDecision(type="call_skill", skill_id="verify_cases", step_id="verify", decision_summary="重新验收")


def options():
    return dict(planner=planner, preflight=lambda: "", coder=lambda **k: CodeSolution(prefix="fix", imports="", code=GOOD),
        reviewer=lambda **k: ReviewResult(passes=False, issues=["wrong operator"], suggestions=["add"]),
        reflector=lambda **k: ReflectionResult(root_cause="operator", new_approach="addition", key_changes=["+"]),
        evaluator=lambda code, task: AcceptanceResult(success=" + " in code, error_type="" if " + " in code else "test_failure"))


class EngineTests(unittest.TestCase):
    def request(self, **kw):
        return TaskRequest(goal="add", code=BAD, mode="verified_repair", entrypoint="solve", cases=[CASE], **kw)

    def test_imported_code_verified_before_repair_and_dynamic_replan(self):
        request = self.request()
        result = AgentEngine(lambda: request, **options()).build().invoke(initial(request), {"recursion_limit": 100})
        self.assertEqual(result["reason"], "tests_passed")
        self.assertEqual(result["replan_count"], 1)
        self.assertEqual(result["observations"][0]["skill_id"], "verify_cases")
        self.assertEqual(result["original_code"], BAD)
        self.assertNotIn("987654321", json.dumps(result))

    def test_diagnosis_never_claims_verified_or_executes(self):
        request = TaskRequest(goal="inspect", code=GOOD)
        def forbidden(*a):
            self.fail("diagnose executed candidate")
        result = AgentEngine(lambda: request, planner=planner, evaluator=forbidden).build().invoke(initial(request))
        self.assertEqual(result["result_level"], "diagnostic_only")

    def test_missing_contract_interrupt_and_resume(self):
        request = TaskRequest(goal="add", mode="verified_repair", code=GOOD)
        engine = AgentEngine(lambda: request, **options())
        graph = engine.build(InMemorySaver())
        config = {"configurable": {"thread_id": "test"}}
        graph.invoke(initial(request), config)
        self.assertTrue(graph.get_state(config).tasks[0].interrupts)
        request.cases = self.request().cases
        request.entrypoint = "solve"
        result = graph.invoke(Command(resume={"confirmed": True}), config)
        self.assertEqual(result["result_level"], "verified")

    def test_invalid_action_and_false_finish_rejected(self):
        request = self.request()
        def wrong(**k):
            return AgentDecision(type="call_skill", skill_id="shell", decision_summary="bad")
        result = AgentEngine(lambda: request, **(options() | {"planner": wrong})).build().invoke(initial(request))
        self.assertEqual(result["reason"], "invalid_action_limit")
        result = AgentEngine(lambda: request, **(options() | {"planner": lambda **k: AgentDecision(type="finish", decision_summary="trust me")})).build().invoke(initial(request))
        self.assertEqual(result["reason"], "unverified_finish")

    def test_skill_permissions_and_arguments(self):
        registry = SkillRegistry()
        with self.assertRaises(ValueError):
            registry.authorize("diagnose_python", "generate_candidate")
        with self.assertRaises(ValueError):
            registry.load("verify_cases", {"expected": "replace"})

    def test_cleanup_waits_for_auto_remove_race(self):
        from subprocess import CompletedProcess
        from code_assistant.utils.docker_sandbox import DockerSandbox
        replies = [CompletedProcess([], 1, "", "removal of container is already in progress"),
                   CompletedProcess([], 1, "", "No such container")]
        with patch("code_assistant.utils.docker_sandbox.subprocess.run", side_effect=replies) as run:
            self.assertEqual(DockerSandbox._cleanup("sandbox-test"), "")
            self.assertEqual(run.call_count, 2)

    def test_budget_and_cancel(self):
        request = self.request(max_actions=1)
        result = AgentEngine(lambda: request, **options()).build().invoke(initial(request))
        self.assertEqual(result["reason"], "action_budget")
        result = AgentEngine(lambda: request, cancelled=lambda: True, **options()).build().invoke(initial(request))
        self.assertEqual(result["reason"], "cancelled")


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Repository("sqlite:///" + (Path(self.temp.name) / "db.sqlite").as_posix(), initialize=True)
        self.request = TaskRequest(goal="Python solve", code=GOOD)

    def tearDown(self):
        self.repo.engine.dispose()
        self.temp.cleanup()

    def test_idempotent_submit_and_conflict(self):
        first = self.repo.create(self.request, key="a")
        self.assertEqual(first, self.repo.create(self.request, key="a"))
        with self.assertRaises(Conflict):
            self.repo.create(TaskRequest(goal="different"), key="a")

    def test_action_replay_unknown_and_fencing(self):
        task_id = self.repo.create(self.request)
        claimed, fence = self.repo.claim("w1")
        self.assertEqual(claimed, task_id)
        self.assertEqual(self.repo.action(task_id, fence, "one", lambda: {"value": 1}), {"value": 1})
        self.assertEqual(self.repo.action(task_id, fence, "one", lambda: self.fail("replayed")), {"value": 1})
        with self.assertRaises(LeaseLost):
            self.repo.action(task_id, fence + 1, "two", lambda: {})
        def fail():
            raise RuntimeError("request outcome unknown")
        with self.assertRaises(RuntimeError):
            self.repo.action(task_id, fence, "unknown", fail)
        with self.assertRaises(Conflict):
            self.repo.action(task_id, fence, "unknown", lambda: {})

    def test_durable_graph_service_and_memory_gate(self):
        request = TaskRequest(goal="Python add", code=BAD, mode="verified_repair", entrypoint="solve", cases=[CASE])
        task_id = self.repo.create(request)
        with checkpoint_store(self.repo.url, str(Path(self.temp.name) / "checkpoint.sqlite")) as saver:
            with patch("code_assistant.service.tasks.DockerSandbox.image_identity", return_value="test-image"):
                TaskService(self.repo, saver, engine_options=options()).run(*self.repo.claim("w"))
        row = self.repo.get(task_id)
        self.assertEqual(row["status"], "succeeded", row["result"])
        self.assertEqual(row["result"]["result_level"], "verified")
        self.assertEqual(len(self.repo.get_artifacts(task_id)), 2)
        self.assertTrue(MemoryStore(self.repo).list("local"))
        self.assertFalse(MemoryStore(self.repo).list("other"))
        other = self.repo.create(self.request)
        with self.assertRaises(ValueError):
            MemoryStore(self.repo).remember(other, "unverified", "test-image")

    def test_api_auth_body_idempotency_cancel_and_sse_replay(self):
        client = TestClient(create_app(self.repo, "test-access-token"))
        headers = {"Authorization": "Bearer test-access-token", "Idempotency-Key": "ui-1"}
        self.assertEqual(client.get("/api/v1/tasks").status_code, 401)
        response = client.post("/api/v1/tasks", headers=headers, json=self.request.model_dump())
        self.assertEqual(response.status_code, 202, response.text)
        task_id = response.json()["task_id"]
        self.assertEqual(client.post("/api/v1/tasks", headers=headers, json=self.request.model_dump()).json()["task_id"], task_id)
        self.assertEqual(client.post(f"/api/v1/tasks/{task_id}/cancel", headers=headers).status_code, 200)
        self.assertEqual(client.get(f"/api/v1/tasks/{task_id}", headers=headers).json()["status"], "cancelled")
        response = client.get(f"/api/v1/tasks/{task_id}/events", headers=headers | {"Last-Event-ID": "1"})
        self.assertIn("task.cancel_requested", response.text)
        self.assertNotIn("task.queued", response.text)
        self.assertEqual(client.post("/api/v1/tasks", headers=headers, content="x" * (1024 * 1024 + 1)).status_code, 413)

    def test_resume_version_conflict(self):
        task_id = self.repo.create(self.request)
        _, fence = self.repo.claim("w")
        self.repo.finish(task_id, fence, "waiting_input", {"interrupt_id": "i"})
        resume = ResumeRequest(version=1, interrupt_id="i", cases=[CASE])
        self.repo.resume(task_id, resume)
        with self.assertRaises(Conflict):
            self.repo.resume(task_id, resume)


if __name__ == "__main__":
    unittest.main()
