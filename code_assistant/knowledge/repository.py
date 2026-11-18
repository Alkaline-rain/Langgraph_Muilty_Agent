import hashlib
import json
import math
import time
from dataclasses import asdict
from sqlalchemy import select, insert, update
from code_assistant.knowledge import schema as s
from code_assistant.knowledge.chunking import split_parent_child, VERSION


def digest(value):
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()


class KnowledgeRepository:
    def __init__(self, engine):
        self.engine = engine

    def initialize(self):
        """SQLite/test bootstrap. PostgreSQL uses migrations including dimension trigger."""
        s.metadata.create_all(self.engine)

    def ingest(self, documents, embedder=None, max_calls=200, allow_external=False):
        if not documents or len(documents) > 100:
            raise ValueError("invalid_document_count")
        if len({d.domain for d in documents}) != 1:
            raise ValueError("one_domain_per_snapshot")
        prepared, total = [], 0
        for document in documents:
            normalized = document.text.replace("\r\n", "\n").replace("\r", "\n")
            parents = split_parent_child(normalized)
            total += sum(len(p.children) for p in parents)
            did = digest([document.domain, document.source_uri])
            vid = digest([did, digest(normalized), VERSION, document.supported_versions,
                          document.source_revision, document.license_note, document.title, document.library])
            prepared.append((document, did, vid, normalized, parents))
        if len({d for _, d, _, _, _ in prepared}) != len(prepared):
            raise ValueError("duplicate_source")
        if total > 2000 or (embedder and total > max_calls):
            raise ValueError("ingest_budget")
        profile = embedder.profile if embedder else None
        sid = digest([sorted(p[2] for p in prepared), profile])
        with self.engine.connect() as conn:
            if conn.execute(select(s.snapshots.c.id).where(s.snapshots.c.id == sid)).first():
                self.activate(sid, documents[0].domain)
                return {"snapshot_id": sid, "idempotent": True, "usage": []}
        vectors, ledger = {}, []
        for document, did, vid, text, parents in prepared:
            for pi, parent in enumerate(parents):
                for ci, child in enumerate(parent.children):
                    cid = digest([vid, "child", pi, ci, child.text])
                    if embedder:
                        vector = embedder.embed(child.heading + "\n" + child.text, ledger, allow_external)
                        if not vector:
                            raise ValueError("embedding_required_for_dense_snapshot")
                        vectors[cid] = vector
        # All network work finishes before publication. Any write error rolls back the whole snapshot.
        with self.engine.begin() as conn:
            if profile and not conn.execute(select(s.profiles.c.id).where(s.profiles.c.id == profile)).first():
                conn.execute(insert(s.profiles).values(id=profile, model=embedder.model, dimensions=embedder.dimensions))
            for document, did, vid, text, parents in prepared:
                if not conn.execute(select(s.documents.c.id).where(s.documents.c.id == did)).first():
                    conn.execute(insert(s.documents).values(id=did, source_uri=document.source_uri,
                        domain=document.domain, library=document.library, title=document.title))
                if not conn.execute(select(s.versions.c.id).where(s.versions.c.id == vid)).first():
                    conn.execute(insert(s.versions).values(id=vid, document_id=did, content_hash=digest(text),
                        source_revision=document.source_revision, supported_versions=document.supported_versions,
                        splitter_version=VERSION, license_note=document.license_note, created_at=time.time()))
                    for pi, parent in enumerate(parents):
                        pid = digest([vid, "parent", pi, parent.piece.text])
                        conn.execute(insert(s.chunks).values(id=pid, version_id=vid, kind="parent", ordinal=pi,
                            content_hash=digest(parent.piece.text), **asdict(parent.piece)))
                        for ci, child in enumerate(parent.children):
                            cid = digest([vid, "child", pi, ci, child.text])
                            conn.execute(insert(s.chunks).values(id=cid, version_id=vid, parent_id=pid,
                                kind="child", ordinal=ci, content_hash=digest(child.text), **asdict(child)))
                if profile:
                    for pi, parent in enumerate(parents):
                        for ci, child in enumerate(parent.children):
                            cid = digest([vid, "child", pi, ci, child.text])
                            if not conn.execute(select(s.embeddings.c.child_id).where(
                                    s.embeddings.c.child_id == cid, s.embeddings.c.profile_id == profile)).first():
                                vector = vectors[cid]
                                if len(vector) != embedder.dimensions or not all(math.isfinite(x) for x in vector):
                                    raise ValueError("invalid_vector")
                                conn.execute(insert(s.embeddings).values(child_id=cid, profile_id=profile, vector=vector))
            conn.execute(insert(s.snapshots).values(id=sid, created_at=time.time(), profile_id=profile))
            conn.execute(insert(s.members), [{"snapshot_id": sid, "version_id": p[2]} for p in prepared])
            self._activate(conn, sid, documents[0].domain)
        return {"snapshot_id": sid, "idempotent": False, "children": total, "usage": ledger}

    def _activate(self, conn, sid, domain):
        if not conn.execute(update(s.active).where(s.active.c.domain == domain).values(snapshot_id=sid)).rowcount:
            conn.execute(insert(s.active).values(domain=domain, snapshot_id=sid))

    def activate(self, sid, domain="lcel"):
        with self.engine.begin() as conn:
            if not conn.execute(select(s.snapshots.c.id).where(s.snapshots.c.id == sid)).first():
                raise ValueError("unknown_snapshot")
            self._activate(conn, sid, domain)

    def current(self, domain="lcel"):
        with self.engine.connect() as conn:
            return conn.execute(select(s.active.c.snapshot_id).where(s.active.c.domain == domain)).scalar() or ""

    def snapshot(self, sid):
        with self.engine.connect() as conn:
            row = conn.execute(select(s.snapshots).where(s.snapshots.c.id == sid)).mappings().first()
            return dict(row) if row else None

    def children(self, sid, library_version, domain="lcel"):
        parent = s.chunks.alias("parent")
        query = select(s.chunks.c.id, s.chunks.c.text, s.chunks.c.parent_id, s.chunks.c.heading,
            s.chunks.c.version_id, parent.c.text.label("parent_text"), parent.c.start_line,
            parent.c.end_line, parent.c.content_hash, s.documents.c.source_uri, s.documents.c.title,
            s.versions.c.supported_versions).select_from(s.chunks.join(parent, s.chunks.c.parent_id == parent.c.id)
            .join(s.versions, s.chunks.c.version_id == s.versions.c.id)
            .join(s.documents, s.versions.c.document_id == s.documents.c.id)
            .join(s.members, s.versions.c.id == s.members.c.version_id)).where(
                s.members.c.snapshot_id == sid, s.documents.c.domain == domain,
                s.documents.c.library == "langchain-core").order_by(s.chunks.c.id)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(query).mappings()
                    if library_version in row["supported_versions"]]

    def dense(self, vector, profile, child_ids, limit=20):
        if self.engine.dialect.name != "postgresql":
            raise ValueError("vector_requires_postgresql")
        distance = s.embeddings.c.vector.cosine_distance(vector)
        with self.engine.connect() as conn:
            return [(row.child_id, float(row.distance)) for row in conn.execute(
                select(s.embeddings.c.child_id, distance.label("distance")).where(
                    s.embeddings.c.profile_id == profile, s.embeddings.c.child_id.in_(child_ids))
                .order_by(distance, s.embeddings.c.child_id).limit(limit))]

    def record(self, task_id, action_key, sid, query, result):
        rid = digest([task_id, action_key])
        with self.engine.begin() as conn:
            if not conn.execute(select(s.runs.c.id).where(s.runs.c.id == rid)).first():
                conn.execute(insert(s.runs).values(id=rid, task_id=task_id, action_key=action_key,
                    snapshot_id=sid, query_hash=digest(query), result=result, created_at=time.time()))
        return rid

    def history(self, task_id):
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(s.runs).where(s.runs.c.task_id == task_id)
                .order_by(s.runs.c.created_at)).mappings()]

    def status(self):
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(s.active.c.domain, s.snapshots).join(
                s.snapshots, s.active.c.snapshot_id == s.snapshots.c.id)).mappings()]
