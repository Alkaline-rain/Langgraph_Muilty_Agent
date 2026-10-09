import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code_assistant"))

from schemas.models import TaskSpec, ExecutionResult, AcceptanceResult, CodeSolution, ReviewResult, ReflectionResult
from utils.acceptance import evaluate
from utils.static_validation import validate_code
from utils.sandbox import execute_code
from utils.docker_sandbox import DockerSandbox, run_bounded, MAX_OUTPUT
from graph.edges import route_after_execute, route_after_review
from main import build_graph, initial_state


TASK = TaskSpec.model_validate_json((ROOT / "examples/add.json").read_text(encoding="utf-8"))
GOOD = "def solve(payload):\n    return payload['a'] + payload['b']"
BAD = "def solve(payload):\n    return payload['a'] - payload['b']"


class AcceptanceTests(unittest.TestCase):
    def test_installed_lcel_fake_model_contract(self):
        # 人工固定示例，验证依赖接口；不代表已完成容器集成验证。
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.language_models.fake import FakeListLLM
        from langchain_core.output_parsers import StrOutputParser
        chain = ChatPromptTemplate.from_template("{text}") | FakeListLLM(responses=["OK"]) | StrOutputParser()
        self.assertEqual(chain.invoke({"text": "hello"}), "OK")

    def test_wrong_answer_is_not_execution_success(self):
        result = evaluate("", BAD, TASK, lambda *a, **kw: ExecutionResult(success=True, stdout="-1"))
        self.assertEqual(result.error_type, "test_failure")
        self.assertFalse(result.success)

    def test_all_cases_and_expected_not_sent(self):
        inputs = []
        def executor(imports, code, *, stdin):
            inputs.append(json.loads(stdin))
            value = json.loads(stdin)
            self.assertNotIn("expected", value)
            return ExecutionResult(success=True, stdout=json.dumps(value["a"] + value["b"]))
        result = evaluate("", GOOD, TASK, executor)
        self.assertTrue(result.success)
        self.assertEqual(len(inputs), 3)
        self.assertEqual(len(result.cases), 3)

    def test_non_json_and_boolean_do_not_pass(self):
        for output in ('{"success":true}', '5\n5', 'NaN', 'true', ''):
            with self.subTest(output=output):
                self.assertFalse(evaluate("", GOOD, TASK, lambda *a, **k: ExecutionResult(success=True, stdout=output)).success)

    def test_runtime_failure_preserved(self):
        result = evaluate("", GOOD, TASK, lambda *a, **k: ExecutionResult(success=False, error_type="timeout_error"))
        self.assertEqual(result.error_type, "timeout_error")

    def test_reject_missing_empty_online_and_nonfinite_tasks(self):
        for changes in ({"cases": []}, {"execution_mode": "online"},
                        {"cases": [{"name": "bad", "input": float("nan"), "expected": 1}]}):
            with self.assertRaises(ValueError):
                TaskSpec.model_validate({**TASK.model_dump(), **changes})


class SecurityTests(unittest.TestCase):
    def test_precheck_happens_before_docker(self):
        for imports, code, kind in [("", "def broken(", "syntax_error"),
                                    ("import os", GOOD, "security_error"),
                                    ("import builtins", GOOD, "security_error"),
                                    ("print('side effect')", GOOD, "security_error"),
                                    ("", "x = (1).__class__", "security_error")]:
            with self.subTest(code=code), patch.object(DockerSandbox, "execute") as run:
                self.assertEqual(execute_code(imports, code).error_type, kind)
                run.assert_not_called()

    def test_class_and_lcel_are_allowed(self):
        self.assertTrue(validate_code("from langchain_core.language_models.fake import FakeListLLM", "class A:\n    pass\n" + GOOD).success)

    def test_safe_and_unknown_modes_cannot_execute(self):
        for mode in ("safe", "typo"):
            with patch.dict(os.environ, {"SANDBOX_MODE": mode}), patch.object(DockerSandbox, "execute") as run:
                self.assertEqual(execute_code("", GOOD).error_type, "sandbox_error")
                run.assert_not_called()

    def test_auto_missing_docker_fails_closed(self):
        with patch.dict(os.environ, {"SANDBOX_MODE": "auto"}), patch("utils.docker_sandbox.find_docker", return_value=None):
            self.assertEqual(execute_code("", GOOD).error_type, "sandbox_error")

    def test_legacy_executor_is_disabled(self):
        from utils.safe_executor import execute_code as legacy
        self.assertEqual(legacy("", "raise AssertionError('must not run')").error_type, "sandbox_error")

    def test_daemon_failure_is_environment_error(self):
        with patch("utils.docker_sandbox.shutil.which", return_value="docker"), patch("utils.docker_sandbox.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "daemon unavailable")):
            self.assertEqual(execute_code("", GOOD).error_type, "sandbox_error")

    def test_runtime_command_and_cleanup(self):
        with patch.object(DockerSandbox, "preflight", return_value=""), patch("utils.docker_sandbox.run_bounded", return_value=(0, "5", "", "")) as run, patch.object(DockerSandbox, "_cleanup", return_value="") as cleanup:
            self.assertTrue(DockerSandbox().execute("", GOOD, stdin='{"a":2,"b":3}').success)
            cmd = run.call_args.args[0]
            for flag, value in (("--network", "none"), ("--read-only", None), ("--cap-drop", "ALL"), ("--user", "65534:65534"), ("--pull", "never")):
                self.assertIn(flag, cmd)
                if value:
                    self.assertEqual(cmd[cmd.index(flag) + 1], value)
            self.assertEqual(run.call_args.args[1], b'{"a":2,"b":3}')
            cleanup.assert_called_once()

    def test_timeout_output_and_process_error_cleanup(self):
        for reason in ("timeout", "output_limit"):
            with patch.object(DockerSandbox, "preflight", return_value=""), patch("utils.docker_sandbox.run_bounded", return_value=(-1, "", "", reason)), patch.object(DockerSandbox, "_cleanup", return_value="") as cleanup:
                result = DockerSandbox().execute("", GOOD)
                self.assertEqual(result.error_type, "timeout_error" if reason == "timeout" else "resource_error")
                cleanup.assert_called_once()
        with patch.object(DockerSandbox, "preflight", return_value=""), patch("utils.docker_sandbox.run_bounded", side_effect=OSError("failed")), patch.object(DockerSandbox, "_cleanup", return_value="") as cleanup:
            self.assertEqual(DockerSandbox().execute("", GOOD).error_type, "sandbox_error")
            cleanup.assert_called_once()

    def test_cleanup_failure_cannot_report_success(self):
        with patch.object(DockerSandbox, "preflight", return_value=""), patch("utils.docker_sandbox.run_bounded", return_value=(0, "5", "", "")), patch.object(DockerSandbox, "_cleanup", return_value="daemon unreachable"):
            self.assertEqual(DockerSandbox().execute("", GOOD).error_type, "sandbox_error")

    def test_exit_codes(self):
        for rc, error in ((125, "sandbox_error"), (124, "timeout_error"), (137, "resource_error")):
            self.assertEqual(DockerSandbox._classify(rc, "", "", 0).error_type, error)

    def test_bounded_host_capture(self):
        # 固定、可信的测试脚本，仅验证 CLI 采集器，不执行任何 LLM 输出。
        rc, out, err, reason = run_bounded([sys.executable, "-c", "print('x' * 200000)"], b"", 3)
        self.assertEqual(reason, "output_limit")
        self.assertLessEqual(len(out), MAX_OUTPUT)

    def test_host_timeout(self):
        _, _, _, reason = run_bounded([sys.executable, "-c", "import time; time.sleep(2)"], b"", 0.1)
        self.assertEqual(reason, "timeout")


class WorkflowTests(unittest.TestCase):
    def run_graph(self, outcomes, limit=3):
        solution = CodeSolution(prefix="test", imports="", code=GOOD)
        with patch("graph.nodes.invoke_coder", return_value=solution), patch("graph.nodes.invoke_reviewer", return_value=ReviewResult(passes=True, issues=[], suggestions=[])), patch("graph.nodes.invoke_reflector", return_value=ReflectionResult(root_cause="logic", new_approach="fix", key_changes=["fix"])), patch("graph.nodes.evaluate", side_effect=outcomes):
            return build_graph().invoke(initial_state(TASK, limit), config={"recursion_limit": 100})

    def test_failed_tests_review_passes_still_repairs(self):
        result = self.run_graph([AcceptanceResult(success=False, error_type="test_failure"), AcceptanceResult(success=True)])
        self.assertEqual(result["route_path"], ["generate", "execute", "review", "reflect", "generate", "execute"])
        self.assertTrue(result["task_success"])
        self.assertEqual(result["termination_reason"], "tests_passed")

    def test_limit_preserves_last_failure(self):
        result = self.run_graph([AcceptanceResult(success=False, error_type="test_failure")] * 3)
        self.assertFalse(result["task_success"])
        self.assertEqual(result["iteration"], 3)
        self.assertEqual(result["termination_reason"], "max_iterations")
        self.assertEqual(len(result["failure_trail"]), 3)

    def test_environment_failure_does_not_retry(self):
        result = self.run_graph([AcceptanceResult(success=False, error_type="sandbox_error")])
        self.assertEqual(result["iteration"], 1)
        self.assertEqual(result["termination_reason"], "environment_error")
        self.assertNotIn("review", result["route_path"])

    def test_execution_flag_alone_cannot_pass(self):
        state = {**initial_state(TASK), "exec_success": True, "iteration": 1, "review": {"passes": True}}
        self.assertEqual(route_after_execute(state), "review")
        self.assertEqual(route_after_review(state), "reflect")

    def test_final_round_can_succeed(self):
        result = self.run_graph([AcceptanceResult(success=True)], limit=1)
        self.assertEqual(result["termination_reason"], "tests_passed")


@unittest.skipUnless(os.getenv("RUN_DOCKER_TESTS") == "1", "需 Docker 与已构建镜像；设置 RUN_DOCKER_TESTS=1")
class DockerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        problem = DockerSandbox().preflight()
        if problem:
            raise RuntimeError(problem)

    def test_add_passes(self):
        self.assertTrue(evaluate("", GOOD, TASK).success)

    def test_subtraction_fails(self):
        self.assertEqual(evaluate("", BAD, TASK).error_type, "test_failure")

    def test_real_timeout(self):
        result = DockerSandbox().execute("", "def solve(payload):\n    while True: pass", timeout=1)
        self.assertIn(result.error_type, {"timeout_error", "resource_error"})

    def test_output_limit(self):
        result = execute_code("", "def solve(payload):\n    print('x' * 200000)\n    return 1")
        self.assertEqual(result.error_type, "resource_error")

    def test_offline_lcel(self):
        imports = "from langchain_core.prompts import ChatPromptTemplate\nfrom langchain_core.language_models.fake import FakeListLLM\nfrom langchain_core.output_parsers import StrOutputParser"
        code = "def solve(payload):\n    chain = ChatPromptTemplate.from_template('{text}') | FakeListLLM(responses=[payload['response']]) | StrOutputParser()\n    return chain.invoke({'text': payload['text']})"
        task = TaskSpec.model_validate_json((ROOT / "examples/lcel_offline.json").read_text(encoding="utf-8"))
        self.assertTrue(evaluate(imports, code, task).success)


if __name__ == "__main__":
    unittest.main()
