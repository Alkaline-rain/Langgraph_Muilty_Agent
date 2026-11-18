"""V2 ablations. Default previews cost ceiling only; --execute-live makes paid calls."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import time
from code_assistant.benchmark import load_corpus
from code_assistant.main import build_graph, initial_state
from code_assistant.schemas.models import TaskSpec, RunConfig
from code_assistant.agent_v2.engine import AgentEngine, initial
from code_assistant.service.contracts import TaskRequest
from code_assistant.utils.acceptance import evaluate
from code_assistant.utils.docker_sandbox import DockerSandbox
from code_assistant.skills.registry import SkillRegistry
from code_assistant.utils.trace import atomic_json
from code_assistant.config import MODEL_NAME

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ["legacy_adaptive", "agent_without_skill_guidance", "agent_skills", "agent_skills_frozen_memory"]


def summarize(rows):
    result = {}
    for variant in VARIANTS:
        selected = [r for r in rows if r["variant"] == variant]
        if not selected:
            continue
        known = [r for r in selected if r["provider_tokens"] is not None]
        known_calls = [r for r in selected if r["calls"] is not None]
        result[variant] = {"runs": len(selected), "pass_rate_all": sum(r["success"] for r in selected) / len(selected),
            "mean_calls_known": sum(r["calls"] for r in known_calls) / len(known_calls) if known_calls else None,
            "unknown_call_count_runs": len(selected) - len(known_calls),
            "mean_elapsed_ms": sum(r["elapsed_ms"] for r in selected) / len(selected),
            "provider_tokens": sum(r["provider_tokens"] for r in known) if len(known) == len(selected) else None,
            "unknown_usage_runs": len(selected) - len(known),
            "termination_reasons": {reason: sum(r["reason"] == reason for r in selected) for reason in {r["reason"] for r in selected}}}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-calls", type=int, default=9)
    parser.add_argument("--max-experiment-calls", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--memory-snapshot", type=Path, help="审核过的跨题经验 JSON 列表；不从本次评测学习")
    args = parser.parse_args()
    if args.limit < 1 or args.repeats < 1 or not 1 <= args.max_calls <= 20:
        parser.error("limit/repeats 必须为正，max-calls 为 1..20")
    corpus, corpus_hash = load_corpus(ROOT / "benchmarks/tasks.v1.json")
    entries = corpus["tasks"][:args.limit]
    memories = json.loads(args.memory_snapshot.read_text(encoding="utf-8")) if args.memory_snapshot else []
    if not isinstance(memories, list) or len(memories) > 20:
        parser.error("记忆快照须为最多 20 条的列表")
    evaluation_ids = {entry["id"] for entry in entries}
    for record in memories:
        if not all(k in record for k in ("id", "text", "source_task_ids")) or len(record["text"]) > 2000:
            parser.error("记忆需要 id/text/source_task_ids，文本最多 2000 字符")
        if evaluation_ids.intersection(record["source_task_ids"]):
            parser.error("记忆来源与评测任务重合；禁止测试泄漏")
    variants = VARIANTS if memories else VARIANTS[:-1]
    planned = [(entry, variant, repeat) for entry in entries for variant in variants for repeat in range(args.repeats)]
    random.Random(args.seed).shuffle(planned)
    ceiling = len(planned) * args.max_calls
    if ceiling > args.max_experiment_calls:
        parser.error(f"调用尝试上限 {ceiling} 超过整批准入预算 {args.max_experiment_calls}")
    manifest = {"mode": "live" if args.execute_live else "preview", "model": MODEL_NAME,
        "corpus_hash": corpus_hash, "variants": variants, "workflows": len(planned), "call_ceiling": ceiling,
        "budget_profile": "same_call_ceiling", "max_calls": args.max_calls, "seed": args.seed,
        "skill_versions": SkillRegistry().catalog(), "memory_snapshot_hash": hashlib.sha256(json.dumps(memories, sort_keys=True).encode()).hexdigest(),
        "memory_learning": False, "memory_note": "无快照时不运行记忆组；人工确认同源变体不泄漏"}
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if not args.execute_live:
        return
    problem = DockerSandbox().preflight()
    if problem:
        raise SystemExit(problem)
    folder = ROOT / "benchmark_results" / ("v2-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    folder.mkdir(parents=True, exist_ok=False)
    manifest["sandbox_image_id"] = DockerSandbox().image_identity()
    atomic_json(folder / "manifest.json", manifest)
    rows = []
    for index, (entry, variant, repetition) in enumerate(planned):
        task = TaskSpec.model_validate(entry["task"])
        budget = RunConfig(max_calls=args.max_calls)
        started = time.monotonic()
        state, error = {}, ""
        try:
            if variant == "legacy_adaptive":
                state = build_graph().invoke(initial_state(task, min(20, args.max_calls), budget), {"recursion_limit": 100})
                code = state.get("generation", {}).get("imports", "") + "\n" + state.get("generation", {}).get("code", "")
                development = state.get("task_success", False)
                reason = state.get("termination_reason", "")
            else:
                request = TaskRequest(goal=task.question, mode="verified_repair", entrypoint="solve", cases=task.cases, budget=budget, use_memory=False)
                # This group uses an immutable, pre-audited snapshot; no provider embedding calls.
                recall = lambda goal: (memories[:3] if variant.endswith("frozen_memory") else [], {"strategy": "frozen_snapshot"})
                request.use_memory = variant.endswith("frozen_memory")
                state = AgentEngine(lambda: request, memories=recall, skill_guidance=variant != "agent_without_skill_guidance").build().invoke(initial(request), {"recursion_limit": 100})
                code, reason = state["code"], state["reason"]
                development = state["result_level"] == "verified"
            holdout = evaluate("", code, TaskSpec(question=task.question, cases=entry["holdout_cases"])) if development else None
            success = bool(development and holdout and holdout.success)
        except Exception as exc:
            reason, error, success, holdout, development = "internal_error", type(exc).__name__, False, None, False
        calls = state.get("call_history", [])
        known = not error and all(call.get("usage") is not None for call in calls)
        record = {"task_id": entry["id"], "variant": variant, "repetition": repetition,
            "success": success, "development_success": development, "holdout": holdout.model_dump() if holdout else None,
            "reason": reason, "error_class": error, "calls": state.get("llm_calls") if error else state.get("llm_calls", 0),
            "provider_tokens": sum(c["usage"]["total_tokens"] for c in calls) if known else None,
            "elapsed_ms": int((time.monotonic() - started) * 1000)}
        # Do not export trusted task input/expected values in the agent trace.
        atomic_json(folder / f"state_{index:03d}.json", {k: v for k, v in state.items() if k not in {"task", "messages"}})
        rows.append(record)
        with (folder / "runs.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
            file.flush()
        atomic_json(folder / "summary.json", summarize(rows))
    print(f"实验记录：{folder}")


if __name__ == "__main__":
    main()
