import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from openai import AuthenticationError, RateLimitError, APITimeoutError, APIConnectionError, APIStatusError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code_assistant"))
from main import build_graph, initial_state, main
from schemas.models import TaskSpec, CodeSolution, AcceptanceResult
from utils.run_store import RunStore, run_recorded
from utils.trace import dump
from utils.errors import model_error_code, OutputParseError
from utils.docker_sandbox import DockerSandbox, run_bounded, find_docker
from doctor import diagnose

TASK = TaskSpec.model_validate_json((ROOT / "examples/add.json").read_text(encoding="utf-8"))
GEN = {"prefix": "test", "imports": "", "code": "def solve(payload):\n    return 5"}


class RunStoreTests(unittest.TestCase):
    def test_success_artifacts_and_node_events(self):
        with tempfile.TemporaryDirectory() as root:
            state = initial_state(TASK)
            store = RunStore(root, state)
            graph = build_graph({"coder": Mock(return_value=CodeSolution(**GEN))},
                                Mock(return_value=AcceptanceResult(success=True)), observer=store)
            result, directory = run_recorded(graph, state, root, store=store)
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            snapshot = json.loads((directory / "state.json").read_text(encoding="utf-8"))
            events = [json.loads(x) for x in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(manifest["status"], "succeeded")
            self.assertEqual(snapshot["termination_reason"], "tests_passed")
            self.assertEqual(snapshot["generation_history"][0], GEN)
            self.assertTrue((directory / "code/attempt_001.py").is_file())
            self.assertIn("node_started", [e["kind"] for e in events])
            self.assertIn("node_completed", [e["kind"] for e in events])
            self.assertNotIn("task", snapshot)
            self.assertNotIn("expected", json.dumps(snapshot))
            self.assertFalse((directory / "state.json.tmp").exists())

    def test_exception_and_interrupt_preserve_completed_generation(self):
        for exception, reason in ((RuntimeError("sensitive"), "internal_error"), (KeyboardInterrupt(), "interrupted")):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as root:
                graph = Mock()
                def stream(*a, **k):
                    yield {"generate": {"generation_history": [GEN], "generation": GEN, "iteration": 1}}
                    raise exception
                graph.stream.side_effect = stream
                state, directory = run_recorded(graph, initial_state(TASK), root)
                saved = (directory / "state.json").read_text(encoding="utf-8")
                self.assertEqual(state["termination_reason"], reason)
                self.assertEqual(json.loads(saved)["iteration"], 1)
                self.assertNotIn("sensitive", saved)
                self.assertTrue((directory / "code/attempt_001.py").is_file())

    def test_run_ids_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            a = RunStore(root, initial_state(TASK))
            b = RunStore(root, initial_state(TASK))
            self.assertNotEqual(a.directory, b.directory)

    def test_executor_exception_is_terminal_not_repaired(self):
        reviewer = Mock()
        graph = build_graph({"coder": Mock(return_value=CodeSolution(**GEN)), "reviewer": reviewer},
                            Mock(side_effect=OSError("private_path")))
        result = graph.invoke(initial_state(TASK))
        self.assertEqual(result["termination_reason"], "internal_error")
        self.assertNotIn("private_path", result["exec_error"])
        reviewer.assert_not_called()

    def test_environment_failure_is_written_before_model_call(self):
        with tempfile.TemporaryDirectory() as root, patch("sys.argv", ["main.py", "--task", str(ROOT / "examples/add.json"), "--output", root]), patch("main.DockerSandbox.preflight", return_value="engine stopped"), patch("main.build_graph") as graph:
            with self.assertRaises(SystemExit) as raised:
                main()
            self.assertEqual(raised.exception.code, 2)
            graph.assert_not_called()
            manifest = json.loads(next(Path(root).glob("*/manifest.json")).read_text(encoding="utf-8"))
            self.assertEqual(manifest["termination_reason"], "environment_error")


class ErrorClassificationTests(unittest.TestCase):
    def test_provider_errors_are_distinct(self):
        request = httpx.Request("POST", "https://example.invalid")
        for error, code in (
            (AuthenticationError("secret", response=httpx.Response(401, request=request), body=None), "model_authentication"),
            (RateLimitError("secret", response=httpx.Response(429, request=request), body=None), "model_rate_limit"),
            (APITimeoutError(request=request), "model_timeout"),
            (APIConnectionError(request=request), "model_connection"),
            (APIStatusError("secret", response=httpx.Response(503, request=request), body=None), "model_server_error"),
            (OutputParseError("bad"), "model_output_invalid"),
        ):
            with self.subTest(code=code):
                self.assertEqual(model_error_code(error), code)

    def test_docker_discovery_handles_access_denied(self):
        with patch("utils.docker_sandbox.shutil.which", return_value=None), patch("pathlib.Path.is_file", side_effect=PermissionError()):
            self.assertIsNone(find_docker())

    def test_doctor_missing_engine_never_probes(self):
        with patch("doctor.DockerSandbox.preflight", return_value="not ready"), patch("doctor.DockerSandbox.execute") as execute:
            report = diagnose(probe=True)
            self.assertFalse(report["ready"])
            execute.assert_not_called()


@unittest.skipUnless(os.getenv("RUN_DOCKER_TESTS") == "1", "需要真实 Docker 验证隔离配置")
class IsolationDockerTests(unittest.TestCase):
    def test_kernel_enforced_container_settings(self):
        # 固定受信探针替换 runner 命令，复用生产执行器的全部容器参数；不是生成代码绕过预检。
        probe = """import json, os, pathlib
def writable(path):
    try:
        pathlib.Path(path).write_text('probe')
        return True
    except OSError:
        return False
status = dict(line.split(':', 1) for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line)
root = pathlib.Path('/sys/fs/cgroup')
print(json.dumps({'uid':os.getuid(), 'interfaces':os.listdir('/sys/class/net'),
 'root_writable':writable('/probe'), 'code_writable':writable('/code/candidate.py'),
 'no_new_privs':status['NoNewPrivs'].strip(), 'caps':status['CapEff'].strip(),
 'memory':(root/'memory.max').read_text().strip(), 'pids':(root/'pids.max').read_text().strip(),
 'cpu':(root/'cpu.max').read_text().strip(), 'has_api_key':'DEEPSEEK_API_KEY' in os.environ}))
"""
        def execute_probe(cmd, data, timeout):
            index = cmd.index(DockerSandbox().image)
            return run_bounded(cmd[:index+1] + ["python", "-I", "-c", probe], b"", timeout)
        with patch("utils.docker_sandbox.run_bounded", side_effect=execute_probe):
            result = DockerSandbox().execute("", GEN["code"])
        self.assertTrue(result.success, result.error_message)
        data = json.loads(result.stdout)
        self.assertEqual(data["uid"], 65534)
        self.assertEqual(data["interfaces"], ["lo"])
        self.assertFalse(data["root_writable"])
        self.assertFalse(data["code_writable"])
        self.assertFalse(data["has_api_key"])
        self.assertEqual(data["no_new_privs"], "1")
        self.assertEqual(int(data["caps"], 16), 0)
        self.assertEqual(int(data["memory"]), 256 * 1024 * 1024)
        self.assertEqual(int(data["pids"]), 64)
        quota, period = map(int, data["cpu"].split())
        self.assertAlmostEqual(quota / period, 0.5)


if __name__ == "__main__":
    unittest.main()
