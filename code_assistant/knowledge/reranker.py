import math
import os
from code_assistant.knowledge.embeddings import request_json


class Reranker:
    def __init__(self):
        self.url = os.getenv("KNOWLEDGE_RERANK_URL", "")
        self.key = os.getenv("KNOWLEDGE_RERANK_KEY", "")
        self.model = os.getenv("KNOWLEDGE_RERANK_MODEL", "")

    def rerank(self, query, documents, ledger, allowed=False):
        if not self.url or not self.model:
            raise ValueError("rerank_not_configured")
        if len(documents) > 10 or any(len(d) > 12000 for d in documents):
            raise ValueError("rerank_input_limit")
        data = request_json(self.url, self.key, {"model": self.model, "query": query,
            "documents": documents, "top_n": len(documents)}, ledger, "rerank", allowed)
        seen, results = set(), []
        for row in data["results"]:
            index, score = row["index"], row["relevance_score"]
            if type(index) is not int or index in seen or not 0 <= index < len(documents):
                raise ValueError("invalid_rerank_index")
            if not isinstance(score, (float, int)) or isinstance(score, bool) or not math.isfinite(score):
                raise ValueError("invalid_rerank_score")
            seen.add(index)
            results.append((index, float(score)))
        if not results:
            raise ValueError("empty_rerank")
        return sorted(results, key=lambda r: (-r[1], r[0]))
