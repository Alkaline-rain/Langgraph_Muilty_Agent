"""Controlled RAG context ablation, fixed v2 graph. Preview is the default."""
import argparse
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from code_assistant.agent_v2.engine import AgentEngine, initial
from code_assistant.knowledge.repository import KnowledgeRepository, digest
from code_assistant.knowledge.retriever import Retriever
from code_assistant.knowledge.context import pack
from code_assistant.knowledge.query import build_query
from code_assistant.knowledge.environment import library_version
from code_assistant.persistence.database import Repository
from code_assistant.service.contracts import TaskRequest
from code_assistant.schemas.models import TaskSpec, RunConfig
from code_assistant.utils.acceptance import evaluate
from code_assistant.utils.doc_loader import load_lcel_context
from code_assistant.utils.docker_sandbox import DockerSandbox
from backend.settings import ROOT, database_url
from code_assistant.config import MODEL_NAME

VARIANTS = ["none", "fixed", "bm25", "dense", "hybrid", "rerank", "child_only"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=ROOT/"benchmarks/rag/repairs.json")
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=["fixed", "bm25"])
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-calls", type=int, default=9)
    parser.add_argument("--max-experiment-calls", type=int, default=100)
    parser.add_argument("--allow-external-retrieval", action="store_true")
    parser.add_argument("--max-retrieval-calls", type=int, default=40)
    parser.add_argument("--execute-live", action="store_true")
    args = parser.parse_args()
    if min(args.limit, args.repeats, args.max_calls) < 1 or args.max_calls > 20:
        parser.error("invalid limits")
    corpus = json.loads(args.tasks.read_text(encoding="utf-8"))
    planned = [(t, v, r) for t in corpus["tasks"][:args.limit] for v in args.variants for r in range(args.repeats)]
    random.Random(42).shuffle(planned)
    ceiling = len(planned)*args.max_calls
    external = sum(2 if v in {"rerank", "child_only"} else 1 if v in {"dense", "hybrid"} else 0
                   for _, v, _ in planned) if args.allow_external_retrieval else 0
    if ceiling > args.max_experiment_calls or external > args.max_retrieval_calls:
        parser.error("whole experiment budget exceeded")
    manifest = {"mode": "live" if args.execute_live else "preview", "workflows": len(planned),
                "model_call_ceiling": ceiling, "retrieval_call_ceiling": external,
                "corpus_hash": digest(corpus), "model": MODEL_NAME, "max_calls": args.max_calls,
                "variants": args.variants, "memory": "disabled",
                "design": "fixed v2 graph; one initial context retrieval; no adaptive re-retrieval"}
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if not args.execute_live:
        return
    problem = DockerSandbox().preflight()
    if problem:
        raise SystemExit(problem)
    repo = Repository(database_url())
    knowledge = KnowledgeRepository(repo.engine)
    sid, version = knowledge.current(), library_version()
    if not sid or not version:
        raise SystemExit("knowledge snapshot or verified sandbox library version unavailable")
    folder = ROOT/"benchmark_results"/("rag-repair-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    folder.mkdir(parents=True)
    manifest.update(snapshot=sid, library_version=version, image= DockerSandbox().image_identity())
    (folder/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = []
    try:
        for entry, variant, repetition in planned:
            started = time.monotonic()
            request = TaskRequest(goal=entry["goal"], code=entry["code"], mode="verified_repair", entrypoint="solve",
                                  cases=entry["cases"], use_memory=False, budget=RunConfig(max_calls=args.max_calls))
            request.knowledge.max_retrievals = 0
            state = initial(request)
            retrieval = {"evidence": [], "usage": [], "fallback": [], "mode": variant}
            if variant == "fixed":
                retrieval["evidence"] = [{"id":"K-fixed", "parent_id":"fixed", "child_ids":[],
                    "version_id":"legacy", "source_uri":"project://offline_context", "title":"Original fixed LCEL context",
                    "heading":"fixed", "start_line":1, "end_line":10, "content_hash":digest(load_lcel_context()),
                    "text":load_lcel_context()[:1600], "score":0, "score_type":"fixed"}]
            elif variant != "none":
                retrieval = Retriever(knowledge).retrieve(build_query(state), sid, version,
                    mode="rerank" if variant == "child_only" else variant, allow_external=args.allow_external_retrieval)
                if variant == "child_only":
                    children = {r["id"]:r for r in knowledge.children(sid, version)}
                    for evidence in retrieval["evidence"]:
                        evidence["text"] = "\n".join(children[c]["text"] for c in evidence["child_ids"])
            state["knowledge_refs"], _ = pack(retrieval["evidence"], 6000)
            result, failure = {}, ""
            try:
                # Same roles/tools in all groups. Disable adaptive retrieval to isolate supplied context.
                engine = AgentEngine(lambda: request, knowledge_identity=lambda: (sid,version))
                result = engine.build().invoke(state, {"recursion_limit":100})
                held = evaluate("", result["code"], TaskSpec(question=entry["goal"], cases=entry["holdout_cases"])) if result["result_level"] == "verified" else None
                success = bool(held and held.success)
            except Exception as exc:
                success, failure = False, type(exc).__name__
            record = {"task_id":entry["id"], "variant":variant, "repetition":repetition, "success":success,
                "reason":result.get("reason"), "error_class":failure, "calls":result.get("llm_calls"),
                "call_history":result.get("call_history", []), "retrieval":retrieval,
                "initial_code_hash":digest(entry["code"]), "final_code_hash":digest(result.get("code", "")),
                "elapsed_ms":int((time.monotonic()-started)*1000)}
            with (folder/"runs.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(record,ensure_ascii=False)+"\n")
            rows.append(record)
            summary = {}
            for group in args.variants:
                selected = [r for r in rows if r["variant"] == group]
                if not selected:
                    continue
                known = [r["calls"] for r in selected if r["calls"] is not None]
                summary[group] = {"runs":len(selected),
                    "pass_rate":sum(r["success"] for r in selected)/len(selected),
                    "mean_calls_known":sum(known)/len(known) if known else None,
                    "unknown_calls_runs":len(selected)-len(known),
                    "mean_elapsed_ms":sum(r["elapsed_ms"] for r in selected)/len(selected),
                    "degraded_retrieval_runs":sum(bool(r["retrieval"]["fallback"]) for r in selected)}
            (folder/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    finally:
        repo.engine.dispose()
    print(str(folder))


if __name__ == "__main__":
    main()
