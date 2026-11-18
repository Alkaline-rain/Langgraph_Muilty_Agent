"""TEST ONLY: real API/SQLite/Docker with fixed model responses; never production fallback."""
from pathlib import Path
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.main import create_app
from code_assistant.persistence.database import Repository, checkpoint_store
from code_assistant.service.tasks import TaskService
from scripts.platform_smoke import scripted_options
import uvicorn

directory = ROOT / ".tmp" / ("e2e-" + uuid.uuid4().hex)
directory.mkdir(parents=True)
repo = Repository("sqlite:///" + (directory / "tasks.sqlite").as_posix(), initialize=True)
from code_assistant.knowledge.repository import KnowledgeRepository
from code_assistant.knowledge.ingest import load_manifest
from code_assistant.service.contracts import AgentDecision
import json
knowledge = KnowledgeRepository(repo.engine)
knowledge.initialize()
knowledge.ingest(load_manifest(ROOT / "knowledge_sources/lcel/manifest.json"))


def options():
    configured = scripted_options()
    previous = configured["planner"]
    def planner(context):
        state = json.loads(context)
        if "[RAG]" not in state["goal"]:
            return previous(context)
        steps = [{"step_id": a, "objective": a, "skill_id": b, "dependencies": [], "success_criteria":"tool evidence"}
                 for a,b in [("inspect","diagnose_python"),("docs","retrieve_domain_docs"),
                             ("repair","repair_python"),("verify","verify_cases")]]
        step = next(s for s in steps if s["step_id"] not in state["completed_steps"])
        return AgentDecision(type="call_skill", skill_id=step["skill_id"], step_id=step["step_id"],
            decision_summary="scripted RAG browser fixture", plan=steps if not state["plan"] else [])
    return {**configured, "planner":planner}


def worker():
    with checkpoint_store(repo.url, str(directory / "checkpoints.sqlite")) as saver:
        service = TaskService(repo, saver, engine_options=options())
        while True:
            repo.worker_alive("e2e")
            claimed = repo.claim("e2e")
            if claimed:
                service.run(*claimed)
            time.sleep(0.3)


threading.Thread(target=worker, daemon=True).start()
uvicorn.run(create_app(repo, "e2e-fixed-fixture-token"), host="127.0.0.1", port=8000, log_level="warning")
