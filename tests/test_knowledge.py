import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine, select, func
from code_assistant.knowledge.chunking import split_parent_child
from code_assistant.knowledge.contracts import DocumentInput, RetrievalConfig
from code_assistant.knowledge.repository import KnowledgeRepository, digest
from code_assistant.knowledge.retriever import Retriever, rrf
from code_assistant.knowledge.context import pack
from code_assistant.knowledge.reranker import Reranker
from code_assistant.knowledge import schema as s


def doc(text="# RunnableLambda\n\nRunnableLambda wraps callable functions.\n\n## invoke\n\ninvoke returns output synchronously.\n"):
    return DocumentInput(source_uri="project://test", title="test", supported_versions=["1.6.6"],
        source_revision="test", license_note="project-authored", text=text)


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.repo = KnowledgeRepository(self.engine)
        self.repo.initialize()

    def tearDown(self):
        self.engine.dispose()

    def test_fences_offsets_and_rejection(self):
        text = "# API\n\nExample:\n\n```python\n# not a heading\nx = 1\n```\n\n## Next\n\ninvoke\n"
        parts = split_parent_child(text, parent_size=200, child_size=40, overlap=10)
        self.assertEqual(len(parts), 2)
        self.assertIn("# not a heading", parts[0].piece.text)
        for part in parts:
            for child in part.children:
                self.assertEqual(child.text, "".join(text.splitlines(keepends=True)[child.start_line-1:child.end_line]))
                self.assertEqual(child.text.count("```") % 2, 0)
        with self.assertRaisesRegex(ValueError, "unclosed"):
            split_parent_child("```python\nx=1")
        with self.assertRaisesRegex(ValueError, "oversized"):
            split_parent_child("# long\n\n" + "x"*250, 200, 40, 10)

    def test_snapshots_idempotency_and_compatibility(self):
        first = self.repo.ingest([doc()])
        second = self.repo.ingest([doc()])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertTrue(self.repo.children(first["snapshot_id"], "1.6.6"))
        self.assertEqual(self.repo.children(first["snapshot_id"], "0.0.1"), [])
        new = self.repo.ingest([doc("# Changed\n\nOther content.")])
        self.assertNotEqual(new["snapshot_id"], first["snapshot_id"])
        self.assertTrue(self.repo.children(first["snapshot_id"], "1.6.6"))
        self.assertEqual(self.repo.current(), new["snapshot_id"])

    def test_failed_embedding_does_not_publish(self):
        first = self.repo.ingest([doc()])["snapshot_id"]
        class Broken:
            profile, model, dimensions = "broken", "test", 2
            def embed(self, *args):
                raise ValueError("failed")
        with self.assertRaises(ValueError):
            self.repo.ingest([doc("# New\n\nnew")], Broken(), allow_external=True)
        self.assertEqual(self.repo.current(), first)

    def test_rrf_dedup_and_bm25_small_corpus(self):
        self.assertAlmostEqual(rrf([["a", "a"], ["a"]])["a"], 2/61)
        sid = self.repo.ingest([doc()])["snapshot_id"]
        result = Retriever(self.repo).retrieve("RunnableLambda", sid, "1.6.6", mode="bm25")
        self.assertTrue(result["evidence"])
        self.assertIn("RunnableLambda", result["context"])
        self.assertEqual(len({e["parent_id"] for e in result["evidence"]}), len(result["evidence"]))
        self.assertEqual(pack(result["evidence"], 10), ([], ""))
        with patch("httpx.Client", side_effect=AssertionError("network")):
            fallback = Retriever(self.repo).retrieve("RunnableLambda", sid, "1.6.6")
        self.assertIn("external_retrieval_disabled", fallback["fallback"])

    def test_rerank_validation(self):
        ranker = Reranker()
        ranker.url, ranker.model = "https://invalid.example", "test"
        for rows in ([{"index": 5, "relevance_score": 1}],
                     [{"index": 0, "relevance_score": float("nan")}],
                     [{"index": 0, "relevance_score": 1}]*2, []):
            with patch("code_assistant.knowledge.reranker.request_json", return_value={"results": rows}):
                with self.assertRaises(ValueError):
                    ranker.rerank("q", ["doc"], [], True)

    def test_parent_rerank_and_partial_fallback(self):
        sid = self.repo.ingest([doc()])["snapshot_id"]
        class Rank:
            def rerank(self, q, texts, ledger, allowed):
                return [(i, float(i)) for i in reversed(range(len(texts)))]
        result = Retriever(self.repo, reranker=Rank()).retrieve("invoke RunnableLambda", sid, "1.6.6")
        self.assertEqual(result["evidence"][0]["score_type"], "rerank")
        class Partial:
            def rerank(self, *args): return []
        fallback = Retriever(self.repo, reranker=Partial()).retrieve("invoke RunnableLambda", sid, "1.6.6")
        self.assertIn("rerank:ValueError", fallback["fallback"])
        self.assertEqual(fallback["evidence"][0]["score_type"], "rrf")

    def test_record_idempotency(self):
        sid = self.repo.ingest([doc()])["snapshot_id"]
        result = Retriever(self.repo).retrieve("invoke", sid, "1.6.6", mode="bm25")
        first = self.repo.record("task", "action", sid, "invoke", result)
        self.assertEqual(first, self.repo.record("task", "action", sid, "invoke", result))
        self.assertEqual(len(self.repo.history("task")), 1)


class GraphKnowledgeTests(unittest.TestCase):
    def test_evaluation_metrics_and_preview_no_network(self):
        from code_assistant.knowledge.evaluation import metrics
        from code_assistant.knowledge.repair_evaluation import main
        self.assertEqual(metrics(["b","a"], ["a"])["mrr"], .5)
        self.assertTrue(metrics(["a"], [])["false_evidence"])
        with patch("sys.argv", ["evaluation", "--limit", "1"]), patch("builtins.print"), \
             patch("httpx.Client", side_effect=AssertionError("network")), \
             patch("code_assistant.knowledge.repair_evaluation.DockerSandbox", side_effect=AssertionError("docker")):
            main()

    def test_retrieve_repair_evidence_budget_and_no_answers(self):
        from code_assistant.agent_v2.engine import AgentEngine, initial
        from code_assistant.service.contracts import TaskRequest, AgentDecision
        from code_assistant.schemas.models import CodeSolution
        from test_platform import options, CASE, GOOD, BAD, plan_step
        captured = []
        def planner(context):
            captured.append(context)
            state = json.loads(context)
            completed = state["completed_steps"]
            steps = [plan_step("inspect", "diagnose_python"), plan_step("docs", "retrieve_domain_docs"),
                     plan_step("fix", "repair_python"), plan_step("verify", "verify_cases")]
            step = next(s for s in steps if s["step_id"] not in completed)
            return AgentDecision(type="call_skill", step_id=step["step_id"], skill_id=step["skill_id"],
                                 plan=steps if not state["plan"] else [], decision_summary="test")
        def coder(**kwargs):
            self.assertIn("unique_document_evidence", kwargs["context"])
            self.assertNotIn(str(CASE["expected"]), kwargs["context"])
            return CodeSolution(prefix="fix", imports="", code=GOOD)
        evidence = {"id": "K-test", "parent_id": "p", "child_ids": ["c"], "version_id": "v",
            "source_uri": "project://test", "title": "test", "heading": "API", "start_line": 1, "end_line": 2,
            "content_hash": "hash", "text": "unique_document_evidence", "score": 1, "score_type": "rrf"}
        request = TaskRequest(goal="LCEL", code=BAD, mode="verified_repair", entrypoint="solve", cases=[CASE])
        result = AgentEngine(lambda: request, **(options() | {"planner": planner, "coder": coder}),
            knowledge_identity=lambda: ("snapshot", "1.6.6"),
            knowledge=lambda *a: {"evidence": [evidence], "mode": "bm25", "fallback": [], "usage": []}
            ).build().invoke(initial(request))
        self.assertEqual(result["result_level"], "verified")
        self.assertEqual(result["retrieval_count"], 1)
        self.assertNotIn(str(CASE["expected"]), "".join(captured))
        request.knowledge.max_retrievals = 0
        stopped = AgentEngine(lambda: request, **(options() | {"planner": planner})).build().invoke(initial(request))
        self.assertEqual(stopped["reason"], "retrieval_budget")
