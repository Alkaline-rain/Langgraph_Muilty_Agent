"""入口：读取人工验收任务，预检 Docker，再运行生成与修复闭环。"""

import argparse
import os
import time
from functools import partial
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from graph.edges import route_after_execute, route_after_review, route_after_reflect, route_after_generate
from graph.nodes import execute, generate, reflect, review
from graph.state import GraphState
from utils.doc_loader import load_lcel_context
from utils.trace import dump, summarize
from utils.trace import atomic_json
from schemas.models import TaskSpec, RunConfig
from utils.docker_sandbox import DockerSandbox
from utils.run_store import RunStore, run_recorded, mark_failure


def build_graph(agents=None, evaluator=None, observer=None):
    """构建多智能体代码生成图。"""
    graph = StateGraph(GraphState)

    agents = agents or {}
    def observed(name, function):
        if observer is None:
            return function
        def node(state):
            started = time.monotonic()
            observer.event("node_started", node=name, iteration=state["iteration"])
            try:
                return function(state)
            finally:
                observer.event("node_finished", node=name, duration_ms=int((time.monotonic() - started) * 1000))
        return node
    graph.add_node("generate", observed("generate", partial(generate, agent=agents.get("coder"))))
    graph.add_node("execute", observed("execute", partial(execute, evaluator=evaluator)))
    graph.add_node("review", observed("review", partial(review, agent=agents.get("reviewer"))))
    graph.add_node("reflect", observed("reflect", partial(reflect, agent=agents.get("reflector"))))

    graph.add_edge(START, "generate")
    graph.add_conditional_edges("generate", route_after_generate, {"execute": "execute", "end": END})

    graph.add_conditional_edges(
        "execute",
        route_after_execute,
        {"end": END, "review": "review", "generate": "generate"},
    )
    graph.add_conditional_edges(
        "review",
        route_after_review,
        {"end": END, "reflect": "reflect"},
    )
    graph.add_conditional_edges(
        "reflect",
        route_after_reflect,
        {"end": END, "generate": "generate"},
    )

    return graph.compile()


def initial_state(task: TaskSpec, max_iteration: int = 3, run_config: RunConfig | None = None) -> GraphState:
    if not 1 <= max_iteration <= 20:
        raise ValueError("max_iteration 必须在 1 到 20 之间")
    question = task.question
    context = load_lcel_context()
    return {
        "run_config": (run_config or RunConfig()).model_dump(),
        "llm_calls": 0,
        "budget_tokens": 0,
        "call_history": [],
        "attempt_history": [],
        "error_counts": {},
        "repeated_error_count": 0,
        "repair_strategy": "",
        "task": task.model_dump(),
        "task_success": False,
        "acceptance": {},
        "termination_reason": "",
        "question": question,
        "messages": [HumanMessage(content=question)],
        "context": context,
        "generation": {},
        "generation_history": [],
        "exec_success": False,
        "exec_error": "",
        "exec_error_type": "",
        "exec_stdout": "",
        "sandbox": "",
        "review": {},
        "review_history": [],
        "reflection": {},
        "reflection_history": [],
        "failure_trail": [],
        "trace": [],
        "route_path": [],
        "iteration": 0,
        "max_iteration": max_iteration,
    }


def main():
    parser = argparse.ArgumentParser(description="离线代码生成与可信用例验收")
    parser.add_argument("--task", type=Path,
                        default=Path(__file__).resolve().parents[1] / "examples" / "lcel_offline.json")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--strategy", choices=["single", "direct", "full", "adaptive"], default="adaptive")
    parser.add_argument("--max-calls", type=int, default=9)
    parser.add_argument("--max-total-tokens", type=int, default=150000)
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--check-environment", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "runs")
    args = parser.parse_args()
    try:
        task = TaskSpec.model_validate_json(args.task.read_text(encoding="utf-8"))
        state = initial_state(task, args.max_iterations, RunConfig(
            strategy=args.strategy, max_calls=args.max_calls,
            max_total_tokens=args.max_total_tokens, max_output_tokens=args.max_output_tokens))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"任务文件或参数无效：{type(exc).__name__}，请核对任务 JSON 与参数范围\n")
    store = None if args.check_environment else RunStore(args.output, state, {"task_file": str(args.task.resolve())})
    if os.getenv("SANDBOX_MODE", "docker").strip().lower() not in {"docker", "auto"}:
        problem = "仅支持 docker/auto，禁止进程内降级"
    else:
        problem = DockerSandbox().preflight()
    if problem:
        if store:
            store.finish(mark_failure(state, "environment_error", "sandbox_error", problem))
            print(f"诊断记录：{store.directory}")
        parser.exit(2, f"环境不可用（尚未调用模型）：{problem}\n")
    if args.check_environment:
        print("Docker 服务与镜像预检通过；端到端隔离行为请运行 Docker 集成测试。")
        return
    store.manifest["sandbox"] = {"image": DockerSandbox().image, "image_id": DockerSandbox().image_identity()}
    atomic_json(store.directory / "manifest.json", store.manifest)
    app = build_graph(observer=store)
    result, directory = run_recorded(app, state, args.output, store=store)
    print(f"完整运行记录：{directory}")
    print(f"可信验收通过：{result['task_success']}")
    print(f"终止原因：{result['termination_reason']}")
    print(f"共生成 {result['iteration']} 次代码方案")
    final_gen = result["generation"]
    if final_gen:
        print(final_gen["imports"] + "\n" + final_gen["code"])
    if not result["task_success"]:
        print(f"最终错误：{result['exec_error_type']}：{result['exec_error']}")
    print(summarize(result))
    if result["termination_reason"] == "interrupted":
        raise SystemExit(130)
    if not result["task_success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
