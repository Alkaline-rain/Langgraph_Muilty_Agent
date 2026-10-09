"""固定模拟 Agent + 真实 Docker 的失败→修复示例，不调用模型。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code_assistant"))

from main import build_graph, initial_state
from schemas.models import TaskSpec, CodeSolution, ReviewResult, ReflectionResult
from utils.docker_sandbox import DockerSandbox
from utils.run_store import RunStore, run_recorded


def main():
    sandbox = DockerSandbox()
    if problem := sandbox.preflight():
        raise SystemExit(problem)
    task = TaskSpec.model_validate_json((ROOT / "examples/add.json").read_text(encoding="utf-8"))
    state = initial_state(task)
    solutions = iter([
        CodeSolution(prefix="fixed faulty fixture", imports="", code="def solve(payload):\n    return payload['a'] - payload['b']"),
        CodeSolution(prefix="fixed corrected fixture", imports="", code="def solve(payload):\n    return payload['a'] + payload['b']"),
    ])
    agents = {
        "coder": lambda **kwargs: next(solutions),
        "reviewer": lambda **kwargs: ReviewResult(passes=False, issues=["算术操作错误"], suggestions=["使用加法"]),
        "reflector": lambda **kwargs: ReflectionResult(root_cause="实现与加法规格不符", new_approach="按规格求和", key_changes=["减法改加法"]),
    }
    store = RunStore(ROOT / "runs", state, {"mode": "scripted_agents_real_docker", "sandbox_image": sandbox.image,
                                           "sandbox_image_id": sandbox.image_identity()})
    result, directory = run_recorded(build_graph(agents=agents, observer=store), state, ROOT / "runs", store=store)
    print(f"Run records: {directory}")
    print(f"Passed: {result['task_success']}; attempts: {result['iteration']}; route: {result['route_path']}")
    if not result["task_success"] or result["iteration"] != 2:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
