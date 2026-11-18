import time
from code_assistant.knowledge import bm25
from code_assistant.knowledge.contracts import Evidence
from code_assistant.knowledge.context import pack
from code_assistant.knowledge.embeddings import Embedder
from code_assistant.knowledge.reranker import Reranker
from code_assistant.knowledge.repository import digest


def rrf(lists, k=60):
    result = {}
    for items in lists:
        for rank, key in enumerate(dict.fromkeys(items), 1):
            result[key] = result.get(key, 0) + 1 / (k + rank)
    return result


class Retriever:
    def __init__(self, repository, embedder=None, reranker=None):
        self.repo = repository
        self.embedder = embedder or Embedder()
        self.reranker = reranker or Reranker()

    def retrieve(self, query, snapshot_id, library_version, *, mode="rerank", limit=3,
                 budget=6000, allow_external=False):
        if mode not in {"bm25", "dense", "hybrid", "rerank"} or not 1 <= limit <= 5:
            raise ValueError("invalid_retrieval_options")
        start = time.monotonic()
        result = {"query": query, "query_hash": digest(query), "snapshot_id": snapshot_id,
                  "library_version": library_version, "requested_mode": mode, "mode": "bm25",
                  "fallback": [], "usage": [], "candidates": [], "evidence": [], "context": ""}
        if not snapshot_id:
            result["fallback"].append("knowledge_not_initialized")
            return result
        snapshot = self.repo.snapshot(snapshot_id)
        if not snapshot:
            result["fallback"].append("snapshot_unavailable")
            return result
        rows = self.repo.children(snapshot_id, library_version)
        if not rows:
            result["fallback"].append("no_compatible_evidence")
            return result
        index = {r["id"]: r for r in rows}
        sparse = bm25.search(rows, query)
        dense, dense_ok = [], False
        if mode != "bm25":
            try:
                if not allow_external:
                    raise ValueError("external_retrieval_disabled")
                if snapshot["profile_id"] != self.embedder.profile:
                    raise ValueError("embedding_profile_mismatch")
                vector = self.embedder.embed(query, result["usage"], allow_external)
                dense = self.repo.dense(vector, self.embedder.profile, list(index))
                dense_ok = True
            except Exception as exc:
                # Only our fixed errors are exposed; never persist provider exception text/URLs.
                reason = str(exc) if isinstance(exc, ValueError) and str(exc) in {
                    "external_retrieval_disabled", "embedding_profile_mismatch",
                    "vector_requires_postgresql", "embedding_not_configured"} else type(exc).__name__
                result["fallback"].append(reason)
        lists = [[cid for cid, _ in sparse]]
        if dense_ok:
            lists = [[cid for cid, _ in dense]] if mode == "dense" else lists + [[cid for cid, _ in dense]]
            result["mode"] = "dense" if mode == "dense" else "hybrid"
        fused = rrf(lists)
        parent_hits = {}
        for cid, score in sorted(fused.items(), key=lambda p: (-p[1], p[0])):
            row = index[cid]
            item = parent_hits.setdefault(row["parent_id"], {"row": row, "score": score, "child_ids": []})
            item["child_ids"].append(cid)
            item["score"] = max(item["score"], score)
        candidates = sorted(parent_hits.values(), key=lambda p: (-p["score"], p["row"]["parent_id"]))[:10]
        result["candidates"] = [{"parent_id": p["row"]["parent_id"], "child_ids": p["child_ids"],
            "rrf_score": p["score"]} for p in candidates]
        score_type = "rrf"
        if mode == "rerank" and candidates:
            try:
                ranked = self.reranker.rerank(query, [p["row"]["parent_text"] for p in candidates],
                                              result["usage"], allow_external)
                seen = {i for i, _ in ranked}
                if len(seen) < len(candidates):
                    # Partial response is not silently mixed with scores of another scale.
                    raise ValueError("partial_rerank")
                candidates = [{**candidates[i], "score": score} for i, score in ranked]
                score_type, result["mode"] = "rerank", result["mode"] + "+rerank"
            except Exception as exc:
                result["fallback"].append("rerank:" + type(exc).__name__)
        ranks = {"bm25": {cid: i for i, (cid, _) in enumerate(sparse, 1)},
                 "dense": {cid: i for i, (cid, _) in enumerate(dense, 1)}}
        evidence = []
        for item in candidates[:limit]:
            row = item["row"]
            evidence.append(Evidence(id="K-" + row["parent_id"][:12], parent_id=row["parent_id"],
                child_ids=item["child_ids"], version_id=row["version_id"], source_uri=row["source_uri"],
                title=row["title"], heading=row["heading"], start_line=row["start_line"],
                end_line=row["end_line"], content_hash=row["content_hash"], text=row["parent_text"],
                score=item["score"], score_type=score_type,
                child_ranks={cid: {name: values.get(cid) for name, values in ranks.items()}
                             for cid in item["child_ids"]}).model_dump())
        result["evidence"], result["context"] = pack(evidence, budget)
        if not result["evidence"]:
            result["fallback"].append("no_selected_evidence")
        result["duration_ms"] = int((time.monotonic() - start) * 1000)
        return result
