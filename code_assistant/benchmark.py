"""固定任务集的配对对照实验。simulate 仅检查实验管线，live 才测真实模型。"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import random
import statistics
import time
import uuid

from config import MODEL_NAME
from main import build_graph, initial_state
from schemas.models import TaskSpec, CodeSolution, RunConfig, AcceptanceResult, ReviewResult, ReflectionResult
from utils.acceptance import evaluate
from utils.docker_sandbox import DockerSandbox
from utils.static_validation import validate_code
from utils.run_store import RunStore, run_recorded
from utils.trace import atomic_json

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ("single", "direct", "full", "adaptive")


def load_corpus(path):
    raw = Path(path).read_bytes()
    corpus = json.loads(raw)
    seen = set()
    if not corpus.get("version") or not corpus.get("tasks"):
        raise ValueError("评测集需要版本及非空任务列表")
    for entry in corpus["tasks"]:
        if entry["id"] in seen:
            raise ValueError("评测任务 ID 重复")
        seen.add(entry["id"])
        task = TaskSpec.model_validate(entry["task"])
        TaskSpec.model_validate({**task.model_dump(), "cases": entry["holdout_cases"]})
        reference = CodeSolution.model_validate(entry["reference"])
        check = validate_code(reference.imports, reference.code)
        if not check.success:
            raise ValueError(f"参考实现未通过预检：{entry['id']} {check.error_message}")
    return corpus, hashlib.sha256(raw).hexdigest()


def simulate_components(index):
    """预先指定的故障剧本；不会解释执行候选代码、读取答案或调用模型。"""
    plans = [[None], ["syntax_error", None], ["import_error", None],
             ["runtime_error", "runtime_error", None], ["test_failure"] * 30,
             ["sandbox_error"]]
    plan = plans[index % len(plans)]
    calls = {"attempt": 0}

    def coder(**values):
        calls["attempt"] += 1
        return CodeSolution(prefix="SIMULATION ONLY", imports="",
                            code=f"def solve(payload):\n    return None  # fixture {calls['attempt']}")

    def reviewer(**values):
        return ReviewResult(passes=False, issues=["模拟错误"], suggestions=["模拟修改建议"])

    def reflector(**values):
        return ReflectionResult(root_cause="模拟原因", new_approach="模拟替代方案", key_changes=["模拟修改"])

    def evaluator(imports, code, task):
        kind = plan[min(max(calls["attempt"] - 1, 0), len(plan) - 1)]
        return AcceptanceResult(success=kind is None, error_type=kind or "",
                                error_message=f"fixture: {kind}" if kind else "")

    return {"coder": coder, "reviewer": reviewer, "reflector": reflector}, evaluator


def make_record(entry, state, mode, repetition, elapsed_ms, holdout):
    calls = state["call_history"]
    usage_complete = all(c.get("usage") is not None for c in calls)
    usages = [c["usage"] for c in calls if c.get("usage")]
    totals = {key: sum(u.get(key, 0) for u in usages)
              for key in ("input_tokens", "output_tokens", "total_tokens")}
    attempts = state["attempt_history"]
    first_failed = bool(attempts and not attempts[0]["success"])
    final_success = state["task_success"] and holdout is not None and holdout.success
    infrastructure_error = (state["termination_reason"] in {"environment_error", "invalid_task", "model_error", "internal_error", "interrupted"}
                            or (holdout is not None and holdout.error_type in {"sandbox_error", "task_error", "internal_error"}))
    return {
        "task_id": entry["id"], "category": entry["category"], "mode": mode,
        "strategy": state["run_config"]["strategy"], "repetition": repetition,
        "success": final_success, "development_success": state["task_success"],
        "holdout_success": holdout.success if holdout is not None else None,
        "holdout_error_type": holdout.error_type if holdout else None,
        "termination_reason": state["termination_reason"], "infrastructure_error": infrastructure_error,
        "first_attempt_failed": first_failed, "attempts": state["iteration"],
        "repair_rounds": max(0, state["iteration"] - 1), "model_calls": state["llm_calls"],
        "budget_charge_tokens": state["budget_tokens"],
        "usage_complete": usage_complete,
        "provider_tokens": totals if usage_complete and mode == "live" else None,
        "known_partial_provider_tokens": totals if mode == "live" else None,
        "unknown_usage_calls": sum(c.get("usage") is None for c in calls),
        "elapsed_ms": elapsed_ms, "attempt_history": attempts,
        "call_history": calls, "failure_trail": state["failure_trail"],
        "generation_history": state["generation_history"], "route_path": state["route_path"],
    }


def aggregate(records):
    groups = {}
    for name in STRATEGIES:
        rows = [r for r in records if r["strategy"] == name]
        if not rows:
            continue
        eligible = [r for r in rows if not r["infrastructure_error"]]
        failed_first = [r for r in eligible if r["first_attempt_failed"]]
        failures = [a for r in eligible for a in r["attempt_history"] if not a["success"]]
        complete = all(r["provider_tokens"] is not None for r in rows)
        groups[name] = {
            "runs": len(rows), "successes": sum(r["success"] for r in rows),
            "pass_rate_all_runs": sum(r["success"] for r in rows) / len(rows),
            "excluded_infrastructure_runs": len(rows) - len(eligible),
            "pass_rate_eligible": sum(r["success"] for r in eligible) / len(eligible) if eligible else None,
            "initial_failures": len(failed_first),
            "repair_success_rate": sum(r["success"] for r in failed_first) / len(failed_first) if failed_first else None,
            "failed_attempts": len(failures),
            "repeated_error_rate": sum(a["repeat_count"] > 1 for a in failures) / len(failures) if failures else None,
            "mean_repair_rounds": statistics.mean(r["repair_rounds"] for r in rows),
            "mean_calls": statistics.mean(r["model_calls"] for r in rows),
            "mean_elapsed_ms": statistics.mean(r["elapsed_ms"] for r in rows),
            "provider_total_tokens": sum(r["provider_tokens"]["total_tokens"] for r in rows) if complete else None,
            "unknown_usage_calls": sum(r["unknown_usage_calls"] for r in rows),
            "termination_counts": dict(Counter(r["termination_reason"] for r in rows)),
        }
    return groups


def write_summary(directory, records, manifest):
    groups = aggregate(records)
    complete = len(records) == manifest["planned_workflows"]
    payload = {"manifest": manifest, "completed_workflows": len(records), "complete": complete, "groups": groups}
    (directory / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    disclaimer = ("模拟剧本结果：只证明路由与统计管线运行，不能用于宣称模型修复效果或节省成本。"
                  if manifest["mode"] == "simulate" else
                  "真实模型实验。小型自建任务集；请结合原始记录、保留用例和预算解读，不代表通用代码能力。")
    lines = ["# 代码生成修复对照实验", "", disclaimer, "",
             f"已完成 {len(records)} / {manifest['planned_workflows']} 个工作流；{'完整运行' if complete else '部分结果，尚未完成所有配对'}。", "",
             f"任务集 SHA256：`{manifest['corpus_sha256']}`", "",
             "| 策略 | 运行数 | 全体通过率 | 首次失败后修复率 | 重复错误率 | 平均调用 | 平均耗时 ms | 实测总 Token |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    def percent(value):
        return "N/A" if value is None else f"{value:.1%}"
    for name, g in groups.items():
        lines.append(f"| {name} | {g['runs']} | {percent(g['pass_rate_all_runs'])} | {percent(g['repair_success_rate'])} | {percent(g['repeated_error_rate'])} | {g['mean_calls']:.2f} | {g['mean_elapsed_ms']:.1f} | {g['provider_total_tokens'] if g['provider_total_tokens'] is not None else '未知/不适用'} |")
    lines += ["", "完整的环境失败数、Token 缺失数、终止原因见 summary.json，逐次调用和代码见 runs.jsonl。",
              "全体通过率包括环境失败；修复率和重复率排除环境/任务/模型服务错误。",
              "开发用例通过后才运行保留用例，保留用例结果不回流修复。",
              "Token 未知时不填 0：预算估计与厂商实测用量分开记录。"]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_experiment(corpus_path, output_root, *, mode="simulate", strategies=STRATEGIES,
                   repeats=1, seed=42, max_calls=9, max_total_tokens=150000,
                   max_output_tokens=2048, max_attempts=3, budget_profile="attempts", limit=None):
    if mode not in {"simulate", "live"} or budget_profile not in {"attempts", "calls"}:
        raise ValueError("无效实验模式或预算配置")
    if not strategies or len(set(strategies)) != len(strategies) or any(s not in STRATEGIES for s in strategies):
        raise ValueError("策略必须有效且不重复")
    if repeats < 1 or (limit is not None and limit < 1):
        raise ValueError("repeats 和 limit 必须为正数")
    corpus, digest = load_corpus(corpus_path)
    iterations = min(max_calls, 20) if budget_profile == "calls" else max_attempts
    if not 1 <= iterations <= 20:
        raise ValueError("总生成次数需为 1–20")
    config = RunConfig(max_calls=max_calls, max_total_tokens=max_total_tokens, max_output_tokens=max_output_tokens)
    if mode == "live":
        if os.getenv("SANDBOX_MODE", "docker").strip().lower() not in {"docker", "auto"}:
            raise RuntimeError("真实评测必须使用 Docker")
        if problem := DockerSandbox().preflight():
            raise RuntimeError(f"真实评测未启动，也未调用模型：{problem}")
    entries = corpus["tasks"][:limit]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = Path(output_root) / run_id
    directory.mkdir(parents=True, exist_ok=False)
    source = hashlib.sha256()
    for path in sorted((ROOT / "code_assistant").rglob("*.py")):
        source.update(str(path.relative_to(ROOT)).encode())
        source.update(path.read_bytes())
    manifest = {
        "run_id": run_id, "mode": mode, "corpus_version": corpus["version"], "corpus_sha256": digest,
        "source_sha256": source.hexdigest(), "model": MODEL_NAME if mode == "live" else "scripted-fixture",
        "temperatures": {"coder": 0.2, "reviewer": 0.0, "reflector": 0.4},
        "strategies": list(strategies), "repeats": repeats, "order_seed": seed,
        "planned_workflows": len(entries) * repeats * len(strategies),
        "upper_bound_call_attempts": len(entries) * repeats * sum(1 if s == "single" else max_calls for s in strategies),
        "budget_profile": budget_profile, "max_attempts": iterations, "run_config": config.model_dump(),
        "task_ids": [e["id"] for e in entries], "sandbox_image": DockerSandbox().image,
        "sandbox_image_id": DockerSandbox().image_identity() if mode == "live" else None,
        "packages": {name: importlib.metadata.version(name) for name in ("langgraph", "langchain-core", "langchain-deepseek")},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    rng = random.Random(seed)
    records = []
    with (directory / "runs.jsonl").open("w", encoding="utf-8") as stream:
        for repetition in range(repeats):
            order = list(enumerate(entries))
            rng.shuffle(order)
            for index, entry in order:
                methods = list(strategies)
                rng.shuffle(methods)
                for method in methods:
                    task = TaskSpec.model_validate(entry["task"])
                    run_config = config.model_copy(update={"strategy": method})
                    agents, evaluator = simulate_components(index) if mode == "simulate" else (None, evaluate)
                    started = time.monotonic()
                    state = initial_state(task, iterations, run_config)
                    store = RunStore(directory / "workflows", state, {"task_id": entry["id"], "mode": mode, "repetition": repetition})
                    state, artifact_dir = run_recorded(build_graph(agents, evaluator, observer=store), state,
                                                       directory / "workflows", store=store)
                    holdout = None
                    if state["task_success"]:
                        final_task = TaskSpec.model_validate({**task.model_dump(), "cases": entry["holdout_cases"]})
                        gen = state["generation"]
                        try:
                            holdout = evaluator(gen["imports"], gen["code"], final_task)
                        except Exception as exc:
                            holdout = AcceptanceResult(success=False, error_type="internal_error",
                                                       error_message=f"保留验收异常：{type(exc).__name__}")
                        atomic_json(artifact_dir / "holdout.json", holdout.model_dump())
                    record = make_record(entry, state, mode, repetition, int((time.monotonic() - started) * 1000), holdout)
                    records.append(record)
                    record["artifact_dir"] = str(artifact_dir.relative_to(directory))
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stream.flush()
                    # 即使随后任务失败，已完成记录和聚合仍可读取。
                    write_summary(directory, records, manifest)
                    if state["termination_reason"] == "interrupted":
                        return directory
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["simulate", "live"], default="simulate")
    parser.add_argument("--corpus", type=Path, default=ROOT / "benchmarks/tasks.v1.json")
    parser.add_argument("--output", type=Path, default=ROOT / "benchmark_results")
    parser.add_argument("--strategies", nargs="+", choices=STRATEGIES, default=list(STRATEGIES))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-calls", type=int, default=9)
    parser.add_argument("--max-total-tokens", type=int, default=150000)
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--budget-profile", choices=["attempts", "calls"], default="attempts")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    try:
        result = run_experiment(args.corpus, args.output, mode=args.mode, strategies=args.strategies,
            repeats=args.repeats, seed=args.seed, max_calls=args.max_calls, max_total_tokens=args.max_total_tokens,
            max_output_tokens=args.max_output_tokens, max_attempts=args.max_attempts,
            budget_profile=args.budget_profile, limit=args.limit)
    except (ValueError, RuntimeError) as exc:
        parser.exit(2, str(exc) + "\n")
    print(f"实验记录：{result}")


if __name__ == "__main__":
    main()
