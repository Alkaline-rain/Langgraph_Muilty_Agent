import os
import threading
import unittest
import uuid
from unittest.mock import patch
from sqlalchemy import update
from backend.settings import database_url
from code_assistant.persistence.database import Repository, tasks, LeaseLost, checkpoint_store
from code_assistant.service.contracts import TaskRequest, ResumeRequest
from code_assistant.service.tasks import TaskService
from code_assistant.memory.store import MemoryStore
from code_assistant.utils.docker_sandbox import DockerSandbox
from test_platform import options, GOOD, CASE


@unittest.skipUnless(os.getenv("RUN_PLATFORM_INTEGRATION") == "1", "Set RUN_PLATFORM_INTEGRATION=1 for PostgreSQL + Docker")
class PostgresIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(database_url())
        self.owner = "integration-" + uuid.uuid4().hex

    def tearDown(self):
        self.repo.engine.dispose()

    def test_pg_checkpoint_interrupt_resume_and_fenced_reclaim(self):
        task_id = self.repo.create(TaskRequest(goal="Python add", code=GOOD, mode="verified_repair", use_memory=False), owner=self.owner)
        claimed = self.repo.claim("test")
        self.assertEqual(claimed[0], task_id, "integration requires empty queue")
        with patch("code_assistant.service.tasks.DockerSandbox.image_identity", return_value="fixture"):
            with checkpoint_store(database_url()) as saver:
                TaskService(self.repo, saver, engine_options=options()).run(*claimed)
            row = self.repo.get(task_id)
            self.assertEqual(row["status"], "waiting_input", row)
            self.repo.resume(task_id, ResumeRequest(version=row["version"], interrupt_id=row["result"]["interrupt_id"], cases=[CASE]))
            claimed2 = self.repo.claim("second")
            with checkpoint_store(database_url()) as saver:
                TaskService(self.repo, saver, engine_options=options()).run(*claimed2)
        self.assertEqual(self.repo.get(task_id)["result"]["result_level"], "verified")
        with self.assertRaises(LeaseLost):
            self.repo.heartbeat(*claimed)

    def test_pgvector_retrieval_and_namespace(self):
        class Embed:
            model = "deterministic-fixture-v1"
            def embed(self, text):
                return [1.0, 0.0, 0.0]
        task_id = self.repo.create(TaskRequest(goal="Python repair"), owner=self.owner)
        claimed = self.repo.claim("memory-test")
        self.assertEqual(claimed[0], task_id)
        self.repo.finish(*claimed, "succeeded", {"result_level": "verified", "verified_hash": "test-fixture-only"})
        store = MemoryStore(self.repo, Embed())
        store.remember(task_id, "Python solve wrong operator fix addition", "fixture-image")
        memories, status = store.search("Python addition", self.owner, "fixture-image")
        self.assertEqual(status["strategy"], "hybrid_pgvector", status)
        self.assertEqual(memories[0]["task_id"], task_id)
        self.assertFalse(store.search("Python addition", "other", "fixture-image")[0])
        store.delete(memories[0]["id"], self.owner)

    def test_cancel_running_real_container(self):
        cancel = threading.Event()
        timer = threading.Timer(1.5, cancel.set)
        timer.start()
        try:
            result = DockerSandbox().execute("", "def solve(payload):\n    while True:\n        pass", cancelled=cancel.is_set)
            self.assertEqual(result.error_type, "cancelled", result)
        finally:
            timer.cancel()
