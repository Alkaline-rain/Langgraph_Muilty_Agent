import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code_assistant"))

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from main import build_graph, initial_state
from schemas.models import TaskSpec, RunConfig, CodeSolution, ReviewResult, ReflectionResult, AcceptanceResult
from utils.llm_runtime import call_agent, structured_call
from utils.repair_policy import fingerprint, code_fingerprint, feedback
from utils.acceptance import evaluate
from benchmark import load_corpus, run_experiment, aggregate

TASK = TaskSpec.model_validate_json((ROOT / "examples/add.json").read_text(encoding="utf-8"))
SOLUTION = CodeSolution(prefix="test", imports="", code="def solve(payload):\n    return payload['a'] + payload['b']")


def agents():
    return {"coder": Mock(return_value=SOLUTION),
            "reviewer": Mock(return_value=ReviewResult(passes=True, issues=["wrong"], suggestions=["fix"])),
            "reflector": Mock(return_value=ReflectionResult(root_cause="logic", new_approach="replace", key_changes=["change"]))}


class PolicyTests(unittest.TestCase):
    def test_simple_errors_skip_agents(self):
        for strategy in ("direct", "adaptive"):
            functions = agents()
            executor = Mock(side_effect=[AcceptanceResult(success=False, error_type="syntax_error", error_message="bad"), AcceptanceResult(success=True)])
            state = build_graph(functions, executor).invoke(initial_state(TASK, 3, RunConfig(strategy=strategy)))
            self.assertEqual(state["route_path"], ["generate", "execute", "generate", "execute"])
            functions["reviewer"].assert_not_called()
            self.assertEqual(state["llm_calls"], 2)

    def test_repeat_escalates_and_stops(self):
        functions = agents()
        executor = Mock(return_value=AcceptanceResult(success=False, error_type="syntax_error", error_message="same"))
        state = build_graph(functions, executor).invoke(initial_state(TASK, 6, RunConfig(strategy="adaptive")))
        self.assertEqual(state["termination_reason"], "repeated_failure")
        self.assertEqual(state["iteration"], 3)
        self.assertEqual(state["repeated_error_count"], 3)
        self.assertEqual(state["repair_strategy"], "change_approach")
        functions["reviewer"].assert_called_once()
        self.assertIn("change_approach", functions["coder"].call_args.kwargs["feedback"])
        self.assertEqual(len(state["failure_trail"]), 3)

    def test_fixed_baselines_do_not_use_repeat_stop(self):
        for method, expected_calls in (("single", 1), ("direct", 4), ("full", 10)):
            functions = agents()
            state = build_graph(functions, Mock(return_value=AcceptanceResult(success=False, error_type="test_failure"))).invoke(
                initial_state(TASK, 4, RunConfig(strategy=method, max_calls=12)), config={"recursion_limit": 100})
            self.assertEqual(state["llm_calls"], expected_calls)
            self.assertNotEqual(state["termination_reason"], "repeated_failure")

    def test_fingerprint_noise_and_case_separation(self):
        a = fingerprint("runtime_error", 'File "/tmp/a.py", line 9\nTypeError: bad at 0xabc', "case1")
        b = fingerprint("runtime_error", 'File "C:/tmp/b.py", line 42\nTypeError: bad at 0xff', "case1")
        self.assertEqual(a, b)
        self.assertNotEqual(a, fingerprint("runtime_error", 'TypeError: other', "case1"))
        self.assertNotEqual(a, fingerprint("runtime_error", 'File "/tmp/a.py", line 9\nTypeError: bad at 0xabc', "case2"))

    def test_code_hash_ignores_comments(self):
        self.assertEqual(code_fingerprint({"code": "x = 1 # foo"}), code_fingerprint({"code": "x=1 # bar"}))

    def test_context_keeps_recent_failure_and_is_bounded(self):
        state = initial_state(TASK, run_config=RunConfig(feedback_chars=2000, history_window=2))
        state.update(generation_history=[{"imports": "", "code": "OLDEST" * 10000},
                                         {"imports": "", "code": "LATEST" * 10000}],
                     exec_error="LATEST_ERROR", exec_error_type="test_failure",
                     reflection={"new_approach": "REPLACE"}, repair_strategy="change_approach",
                     failure_trail=[{"iteration": i, "error_type": "runtime_error", "error_message": str(i)} for i in range(10)])
        text = feedback(state)
        self.assertLessEqual(len(text), 2000)
        self.assertIn("LATEST_ERROR", text)
        self.assertIn("REPLACE", text)
        self.assertIn("LATEST", text)
        self.assertNotIn("OLDEST", text)


class BudgetTests(unittest.TestCase):
    def test_token_budget_blocks_before_function(self):
        fn = Mock(return_value=SOLUTION)
        result, update, reason, _ = call_agent(initial_state(TASK, run_config=RunConfig(max_total_tokens=1)), "coder", fn, {"question": "hi"})
        self.assertEqual(reason, "token_budget")
        self.assertEqual(update, {})
        fn.assert_not_called()

    def test_last_call_result_is_still_evaluated(self):
        for passes in (True, False):
            functions = agents()
            evaluator = Mock(return_value=AcceptanceResult(success=passes, error_type="" if passes else "test_failure"))
            state = build_graph(functions, evaluator).invoke(initial_state(TASK, run_config=RunConfig(max_calls=1)))
            evaluator.assert_called_once()
            self.assertEqual(state["termination_reason"], "tests_passed" if passes else "call_budget")
            functions["reviewer"].assert_not_called()

    def test_no_execute_after_denied_generation(self):
        functions, evaluator = agents(), Mock()
        state = build_graph(functions, evaluator).invoke(initial_state(TASK, run_config=RunConfig(max_total_tokens=1)))
        self.assertEqual(state["iteration"], 0)
        self.assertEqual(state["llm_calls"], 0)
        self.assertEqual(state["termination_reason"], "token_budget")
        evaluator.assert_not_called()

    def test_model_failure_is_recorded_without_secret(self):
        functions = agents()
        functions["coder"].side_effect = RuntimeError("SECRET_AUTH_VALUE")
        state = build_graph(functions, Mock()).invoke(initial_state(TASK))
        self.assertEqual(state["termination_reason"], "model_error")
        self.assertNotIn("SECRET_AUTH_VALUE", json.dumps(state, default=str))
        self.assertEqual(state["llm_calls"], 1)

    def invoke_provider(self, reply, config=None):
        fake = Mock()
        fake.with_structured_output.return_value.invoke.return_value = reply
        prompt = ChatPromptTemplate.from_template("Question: {question}")
        fn = lambda **kw: structured_call(prompt, CodeSolution, kw, 0.2)
        with patch("utils.llm_runtime.get_llm", return_value=fake) as factory:
            result = call_agent(initial_state(TASK, run_config=config), "coder", fn, {"question": "hello"})
        return result, factory, fake

    def test_provider_usage_is_actual_and_output_limited(self):
        reply = {"raw": AIMessage(content="", usage_metadata={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}), "parsed": SOLUTION}
        (result, update, reason, _), factory, fake = self.invoke_provider(reply)
        self.assertEqual(update["budget_tokens"], 30)
        self.assertEqual(update["call_history"][0]["usage"]["total_tokens"], 30)
        factory.assert_called_once_with(temperature=0.2, max_tokens=2048)
        self.assertFalse(reason)

    def test_unknown_usage_is_not_zero(self):
        (_, update, _, _), _, _ = self.invoke_provider({"raw": AIMessage(content=""), "parsed": SOLUTION})
        self.assertIsNone(update["call_history"][0]["usage"])
        self.assertGreater(update["budget_tokens"], 2048)

    def test_parse_failure_still_counts_billed_usage(self):
        reply = {"raw": AIMessage(content="", usage_metadata={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}), "parsed": None, "parsing_error": ValueError("bad")}
        (_, update, reason, _), _, _ = self.invoke_provider(reply)
        self.assertEqual(reason, "model_error")
        self.assertEqual(update["budget_tokens"], 30)

    def test_complete_template_budget_checked_before_request(self):
        fake = Mock()
        prompt = ChatPromptTemplate.from_template("x" * 3000 + "{question}")
        with patch("utils.llm_runtime.get_llm", return_value=fake) as factory:
            _, update, reason, _ = call_agent(initial_state(TASK, run_config=RunConfig(prompt_chars=1000)),
                "coder", lambda **kw: structured_call(prompt, CodeSolution, kw, 0.2), {"question": "short"})
        self.assertEqual(reason, "token_budget")
        self.assertEqual(update, {})
        factory.assert_not_called()


class BenchmarkTests(unittest.TestCase):
    def test_corpus_size_and_separate_holdout(self):
        corpus, digest = load_corpus(ROOT / "benchmarks/tasks.v1.json")
        self.assertEqual(len(corpus["tasks"]), 24)
        self.assertEqual(len(digest), 64)
        self.assertEqual(sum(len(e["task"]["cases"]) + len(e["holdout_cases"]) for e in corpus["tasks"]), 72)
        for entry in corpus["tasks"]:
            self.assertFalse({c["name"] for c in entry["task"]["cases"]} & {c["name"] for c in entry["holdout_cases"]})

    def test_simulation_report_is_explicit_not_model_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_experiment(ROOT / "benchmarks/tasks.v1.json", directory, limit=6)
            rows = [json.loads(line) for line in (result / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 24)
            self.assertTrue(all(r["mode"] == "simulate" for r in rows))
            self.assertTrue(all(r["provider_tokens"] is None for r in rows))
            groups = aggregate(rows)
            self.assertEqual(groups["single"]["excluded_infrastructure_runs"], 1)
            self.assertEqual(groups["direct"]["initial_failures"], 4)
            self.assertAlmostEqual(groups["direct"]["repair_success_rate"], 3 / 4)
            self.assertIn("模拟剧本", (result / "report.md").read_text(encoding="utf-8"))
            self.assertTrue(all("expected" not in json.dumps(r["call_history"]) for r in rows))

    def test_live_preflight_does_not_run_or_create_report(self):
        with tempfile.TemporaryDirectory() as directory, patch("benchmark.DockerSandbox.preflight", return_value="missing"), patch("benchmark.build_graph") as build:
            with self.assertRaises(RuntimeError):
                run_experiment(ROOT / "benchmarks/tasks.v1.json", directory, mode="live")
            build.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])


@unittest.skipUnless(os.getenv("RUN_DOCKER_TESTS") == "1", "需要真实 Docker 验证参考实现")
class CorpusDockerTests(unittest.TestCase):
    def test_all_reference_solutions(self):
        corpus, _ = load_corpus(ROOT / "benchmarks/tasks.v1.json")
        for entry in corpus["tasks"]:
            with self.subTest(task=entry["id"]):
                task = TaskSpec.model_validate({**entry["task"], "cases": entry["task"]["cases"] + entry["holdout_cases"]})
                ref = entry["reference"]
                result = evaluate(ref["imports"], ref["code"], task)
                self.assertTrue(result.success, result.error_message)


if __name__ == "__main__":
    unittest.main()
