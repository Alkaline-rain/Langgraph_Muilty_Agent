import json
import os
import unittest
import uuid
from pathlib import Path
from sqlalchemy import select, insert
from sqlalchemy.exc import DBAPIError
from backend.settings import database_url
from code_assistant.persistence.database import Repository
from code_assistant.knowledge.repository import KnowledgeRepository
from code_assistant.knowledge.retriever import Retriever
from code_assistant.knowledge.contracts import DocumentInput
from code_assistant.knowledge import schema as s
from code_assistant.utils.acceptance import evaluate
from code_assistant.schemas.models import TaskSpec


@unittest.skipUnless(os.getenv("RUN_PLATFORM_INTEGRATION") == "1", "PostgreSQL/Docker opt-in")
class RealKnowledgeTests(unittest.TestCase):
    def test_native_vector_and_dimension_constraint(self):
        repo = Repository(database_url())
        store = KnowledgeRepository(repo.engine)
        previous = store.current("fixture")
        # Use private fixture domain through a model_copy; runtime requests cannot choose it.
        doc = DocumentInput(source_uri="project://vector-test/"+uuid.uuid4().hex, title="fixture",
            supported_versions=["1.6.6"], source_revision="test", license_note="fixture",
            text="# RunnableLambda\n\nRunnableLambda callable adapter.").model_copy(update={"domain":"fixture"})
        class Embed:
            profile, model, dimensions = "fixture-v3-3d", "deterministic-fixture", 3
            def embed(self, *args): return [1.,0.,0.]
        try:
            sid = store.ingest([doc], Embed(), allow_external=True)["snapshot_id"]
            children = store.children(sid, "1.6.6", domain="fixture")
            hits = store.dense([1.,0.,0.], Embed.profile, [r["id"] for r in children])
            self.assertAlmostEqual(hits[0][1], 0.)
            with self.assertRaises(DBAPIError):
                with repo.engine.begin() as conn:
                    from sqlalchemy import update
                    conn.execute(update(s.embeddings).where(s.embeddings.c.child_id == hits[0][0])
                                 .values(vector=[1.,0.]))
        finally:
            if previous:
                store.activate(previous, "fixture")
            repo.engine.dispose()

    def test_reference_repairs_and_negative_candidates_in_real_docker(self):
        corpus = json.loads(Path("benchmarks/rag/repairs.json").read_text(encoding="utf-8"))
        for entry in corpus["tasks"]:
            with self.subTest(task=entry["id"]):
                task = TaskSpec(question=entry["goal"], cases=entry["cases"]+entry["holdout_cases"])
                self.assertTrue(evaluate("", entry["reference"], task).success, entry["id"])
                self.assertFalse(evaluate("", entry["code"], task).success, entry["id"])

    def test_scripted_retrieval_to_repair_real_docker(self):
        from code_assistant.agent_v2.engine import AgentEngine, initial
        from code_assistant.service.contracts import TaskRequest, AgentDecision
        from code_assistant.schemas.models import CodeSolution
        from code_assistant.knowledge.environment import library_version
        repo = Repository(database_url())
        knowledge = KnowledgeRepository(repo.engine)
        sid, version = knowledge.current(), library_version()
        self.assertTrue(sid)
        self.assertEqual(version, "1.6.6")
        entry = json.loads(Path("benchmarks/rag/repairs.json").read_text(encoding="utf-8"))["tasks"][0]
        def planner(context):
            state = json.loads(context)
            steps = [{"step_id":a,"objective":a,"skill_id":b,"dependencies":[],"success_criteria":"tool evidence"}
                for a,b in [("inspect","diagnose_python"),("docs","retrieve_domain_docs"),("fix","repair_python"),("verify","verify_cases")]]
            step = next(s for s in steps if s["step_id"] not in state["completed_steps"])
            return AgentDecision(type="call_skill", skill_id=step["skill_id"], step_id=step["step_id"],
                plan=steps if not state["plan"] else [], decision_summary="fixture")
        seen = []
        def coder(**values):
            seen.append(values["context"])
            return CodeSolution(prefix="fixture", imports="", code=entry["reference"])
        request = TaskRequest(goal=entry["goal"], code=entry["code"], mode="verified_repair",
            entrypoint="solve", cases=entry["cases"], use_memory=False)
        try:
            result = AgentEngine(lambda:request, planner=planner, coder=coder,
                knowledge_identity=lambda:(sid,version),
                knowledge=lambda state,q,k:Retriever(knowledge).retrieve(q,sid,version,mode="bm25",limit=k)
                ).build().invoke(initial(request))
            self.assertEqual(result["result_level"],"verified")
            self.assertTrue(result["knowledge_refs"])
            self.assertIn("[K-", seen[0])
        finally:
            repo.engine.dispose()
