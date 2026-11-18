import math
import os
import re
import time
import uuid
import httpx
from sqlalchemy import select, insert, delete, cast, Text, func, case, literal_column
from pgvector.sqlalchemy import Vector
from code_assistant.persistence.database import episodes


def sanitize_note(text):
    """Minimize retained notes; automatic scrubbing is not a universal DLP guarantee."""
    text = re.sub(r"(?i)(bearer\s+|api[_-]?key\s*[:=]\s*)\S+", "[redacted]", text)
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", text)
    return text[:1200]


class EmbeddingClient:
    """Explicitly configured OpenAI-compatible endpoint; disabled by default."""
    def __init__(self):
        self.url = os.getenv("EMBEDDING_URL", "")
        self.model = os.getenv("EMBEDDING_MODEL", "")
        self.ledger = []

    def embed(self, text):
        if not self.url or not self.model:
            return None
        if len(self.ledger) >= 2:
            raise ValueError("memory_embedding_call_budget")
        from code_assistant.knowledge.embeddings import request_json
        reply = request_json(self.url, os.getenv("EMBEDDING_API_KEY", ""),
            {"model": self.model, "input": text[:2000]}, self.ledger, "memory_embedding", True)
        values = reply["data"][0]["embedding"]
        if not 1 <= len(values) <= 4096 or not all(isinstance(x, (float, int)) and math.isfinite(x) for x in values):
            raise ValueError("invalid_embedding")
        return values


class MemoryStore:
    def __init__(self, repository, embedder=None, allow_external=True):
        self.repo = repository
        self.embedder = embedder or EmbeddingClient()
        self.allow_external = allow_external

    def remember(self, task_id, summary, environment):
        task = self.repo.get(task_id)
        result = task["result"] or {}
        if task["status"] != "succeeded" or result.get("result_level") != "verified" or not result.get("verified_hash"):
            raise ValueError("unverified_memory")
        text = summary[:2000]
        embedding, fallback = None, "disabled"
        try:
            embedding = self.embedder.embed(text) if self.allow_external else None
            fallback = "" if embedding else "disabled"
        except Exception as exc:
            fallback = type(exc).__name__
        with self.repo.engine.begin() as conn:
            if conn.execute(select(episodes.c.id).where(episodes.c.task_id == task_id)).first():
                return
            conn.execute(insert(episodes).values(id=str(uuid.uuid4()), task_id=task_id, owner=task["owner"],
                status="verified", environment=environment, text=text, created_at=time.time(),
                embedding_model=self.embedder.model if embedding else "", embedding=embedding))
        return fallback

    def search(self, query, owner, environment, exclude_task="", limit=3):
        base = select(episodes).where(episodes.c.owner == owner, episodes.c.status == "verified",
            episodes.c.environment == environment, episodes.c.task_id != exclude_task)
        with self.repo.engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(base.order_by(episodes.c.created_at.desc()).limit(200)).mappings()]
        words = set(re.findall(r"[\w]+", query.lower()))
        def score(row):
            return sum(word in row["text"].lower() for word in words)
        ranked = sorted(rows, key=score, reverse=True)
        chosen = [r for r in ranked if score(r) > 0][:limit]
        strategy, fallback = "lexical", "embedding_disabled"
        if self.repo.engine.dialect.name == "postgresql":
            language = literal_column("'simple'")
            document = func.to_tsvector(language, episodes.c.text)
            terms = func.plainto_tsquery(language, query[:2000])
            with self.repo.engine.connect() as conn:
                fulltext = [dict(r) for r in conn.execute(base.where(document.op('@@')(terms)).order_by(
                    func.ts_rank(document, terms).desc()).limit(limit)).mappings()]
            chosen = list({r['id']: r for r in fulltext + chosen}.values())[:limit]
            strategy = "postgres_fulltext"
        try:
            vector = self.embedder.embed(query) if self.allow_external else None
            if vector and self.repo.engine.dialect.name == "postgresql":
                # JSON storage carries dimensions/version; pgvector executes exact cosine search.
                # Exclude null/wrong dimensions before the SQL cast via an explicit dimension guard.
                expression = cast(cast(episodes.c.embedding, Text), Vector())
                query_sql = base.where(episodes.c.embedding_model == self.embedder.model,
                    case((func.json_typeof(episodes.c.embedding) == 'array', func.json_array_length(episodes.c.embedding)), else_=0) == len(vector)).order_by(expression.cosine_distance(vector)).limit(limit)
                with self.repo.engine.connect() as conn:
                    semantic = [dict(r) for r in conn.execute(query_sql).mappings()]
                merged = {r["id"]: r for r in chosen + semantic}
                # Reciprocal rank fusion, preserving both lexical and semantic evidence.
                ranks = {}
                for result_list in (chosen, semantic):
                    for index, row in enumerate(result_list):
                        ranks[row["id"]] = ranks.get(row["id"], 0) + 1 / (60 + index + 1)
                chosen = [merged[key] for key in sorted(ranks, key=ranks.get, reverse=True)[:limit]]
                strategy, fallback = "hybrid_pgvector", ""
            elif vector:
                fallback = "vector_requires_postgresql"
        except Exception as exc:
            fallback = type(exc).__name__
        return [{k: r[k] for k in ("id", "task_id", "text", "environment")} for r in chosen], {"strategy": strategy, "fallback": fallback}

    def list(self, owner):
        with self.repo.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(episodes.c.id, episodes.c.task_id, episodes.c.status,
                episodes.c.text, episodes.c.environment).where(episodes.c.owner == owner).limit(100)).mappings()]

    def delete(self, memory_id, owner):
        with self.repo.engine.begin() as conn:
            return conn.execute(delete(episodes).where(episodes.c.id == memory_id, episodes.c.owner == owner)).rowcount
