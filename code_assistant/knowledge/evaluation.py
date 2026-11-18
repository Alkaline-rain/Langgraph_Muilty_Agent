"""Labeled retrieval evaluation. BM25 by default; external adapters require explicit flag."""
import argparse
import json
import math
import time
from pathlib import Path
from datetime import datetime, timezone
from code_assistant.knowledge.repository import KnowledgeRepository, digest
from code_assistant.knowledge.retriever import Retriever
from code_assistant.persistence.database import Repository
from backend.settings import ROOT, database_url


def metrics(headings, relevant, k=3):
    labels = set(relevant)
    if not labels:
        return {"false_evidence": bool(headings), "recall": None, "mrr": None, "ndcg": None}
    gains = [int(h in labels) for h in headings[:k]]
    dcg = sum(g / math.log2(i+2) for i, g in enumerate(gains))
    ideal = sum(1 / math.log2(i+2) for i in range(min(k, len(labels))))
    return {"recall": len(set(headings[:k]) & labels) / len(labels),
            "mrr": next((1/(i+1) for i, g in enumerate(gains) if g), 0),
            "ndcg": dcg/ideal, "false_evidence": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=ROOT/"benchmarks/rag/queries.json")
    parser.add_argument("--mode", choices=["bm25", "dense", "hybrid", "rerank"], default="bm25")
    parser.add_argument("--split", choices=["dev", "test"], default="test")
    parser.add_argument("--snapshot")
    parser.add_argument("--library-version", default="1.6.6")
    parser.add_argument("--allow-external", action="store_true")
    parser.add_argument("--max-external-calls", type=int, default=60)
    args = parser.parse_args()
    data = json.loads(args.queries.read_text(encoding="utf-8"))
    queries = [q for q in data["queries"] if q["split"] == args.split]
    ceiling = len(queries) * (2 if args.mode == "rerank" else 1) if args.allow_external and args.mode != "bm25" else 0
    if ceiling > args.max_external_calls:
        parser.error("whole experiment external call budget exceeded")
    repo = Repository(database_url())
    knowledge = KnowledgeRepository(repo.engine)
    sid = args.snapshot or knowledge.current()
    rows = []
    try:
        for q in queries:
            result = Retriever(knowledge).retrieve(q["query"], sid, args.library_version,
                mode=args.mode, allow_external=args.allow_external)
            rows.append({"id": q["id"], "metrics": metrics([e["heading"] for e in result["evidence"]], q["relevant"]),
                         "retrieval": result})
    finally:
        repo.engine.dispose()
    answerable = [r for r in rows if r["metrics"]["recall"] is not None]
    empty = [r for r in rows if r["metrics"]["false_evidence"] is not None]
    summary = {key: sum(r["metrics"][key] for r in answerable)/len(answerable) if answerable else None
               for key in ("recall", "mrr", "ndcg")}
    summary["unanswerable_false_evidence_rate"] = sum(r["metrics"]["false_evidence"] for r in empty)/len(empty) if empty else None
    report = {"snapshot": sid, "queries_hash": digest(data), "label_status": data["label_status"],
              "requested_mode": args.mode, "split": args.split, "queries": len(rows), "summary": summary, "rows": rows}
    folder = ROOT/"benchmark_results"/("rag-retrieval-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    folder.mkdir(parents=True)
    (folder/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(folder/"report.json"), "summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
