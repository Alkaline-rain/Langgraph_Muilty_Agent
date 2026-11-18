"""Recorded PostgreSQL + checkpointer + real Docker smoke. Models are scripted unless --live."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.settings import database_url, checkpoint_path
from backend.worker.main import worker_lock
from code_assistant.persistence.database import Repository, checkpoint_store
from code_assistant.service.contracts import TaskRequest, AgentDecision
from code_assistant.service.tasks import TaskService
from code_assistant.schemas.models import CodeSolution, ReviewResult, ReflectionResult


def scripted_planner(context):
    data = json.loads(context)
    def step(id, skill):
        return dict(step_id=id, objective=skill, skill_id=skill, dependencies=[], success_criteria="工具证据")
    if not data["plan"]:
        skill = "diagnose_python" if data["mode"] == "diagnose" else "verify_cases"
        return AgentDecision(type="call_skill", skill_id=skill, step_id="check", decision_summary="检查原代码", plan=[step("check", skill)])
    if data["mode"] == "diagnose":
        return AgentDecision(type="finish", decision_summary="静态诊断完成")
    if data["plan"][0]["step_id"] == "check":
        return AgentDecision(type="revise_plan", decision_summary="错误结果需要修复", plan=[step("repair", "repair_python"),step("verify", "verify_cases")])
    skill, id = ("verify_cases", "verify") if "repair" in data["completed_steps"] else ("repair_python", "repair")
    return AgentDecision(type="call_skill", skill_id=skill, step_id=id, decision_summary="按更新计划执行")


def scripted_options():
    return dict(planner=scripted_planner,
        coder=lambda **kw: CodeSolution(prefix="fixed test fixture", imports="", code="def solve(payload):\n    return payload['a'] + payload['b']"),
        reviewer=lambda **kw: ReviewResult(passes=False, issues=["wrong operator"], suggestions=["addition"]),
        reflector=lambda **kw: ReflectionResult(root_cause="subtraction", new_approach="use addition", key_changes=["replace - with +"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="真实模型，最多 9 次调用；会产生费用")
    args = parser.parse_args()
    request = TaskRequest.model_validate_json((ROOT / "examples/platform_add.json").read_text(encoding="utf-8"))
    request.use_memory = False
    repo = Repository(database_url())
    task_id = repo.create(request, owner="smoke-live" if args.live else "smoke-scripted")
    with worker_lock(repo), checkpoint_store(database_url(), checkpoint_path()) as saver:
        # Smoke owns a dedicated newly queued task; do not silently run unrelated user jobs.
        claimed = repo.claim("smoke", task_id=task_id)
        if not claimed or claimed[0] != task_id:
            raise RuntimeError("队列中有较早任务，请先由 Worker 处理，再重试 smoke")
        TaskService(repo, saver, engine_options={} if args.live else scripted_options()).run(*claimed)
    row = repo.get(task_id)
    report = {"at": datetime.now(timezone.utc).isoformat(), "mode": "live" if args.live else "scripted_agents_real_docker",
        "task": repo.public(row), "events": repo.task_events(task_id), "artifacts": repo.get_artifacts(task_id)}
    directory = ROOT / "test_results" / ("platform-" + task_id)
    directory.mkdir(parents=True)
    path = directory / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"task_id": task_id, "status": row["status"], "reason": (row["result"] or {}).get("reason"), "report": str(path)}, ensure_ascii=False))
    raise SystemExit(0 if row["status"] == "succeeded" else 1)


if __name__ == "__main__":
    main()
