import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from sqlalchemy import (create_engine, MetaData, Table, Column, String, Integer, Float,
                        Text, JSON, UniqueConstraint, select, update, insert, delete, or_, func)
from sqlalchemy.exc import IntegrityError

metadata = MetaData()
tasks = Table("tasks", metadata,
    Column("id", String(36), primary_key=True), Column("owner", String(80), nullable=False),
    Column("idempotency_key", String(128)), Column("request_hash", String(64)),
    Column("request", JSON, nullable=False), Column("status", String(24), nullable=False),
    Column("version", Integer, default=0), Column("seq", Integer, default=0),
    Column("fence", Integer, default=0), Column("lease_owner", String(64)),
    Column("lease_until", Float, default=0), Column("cancel", Integer, default=0),
    Column("created_at", Float), Column("updated_at", Float), Column("result", JSON),
    Column("resume_value", JSON), UniqueConstraint("owner", "idempotency_key"))
events = Table("task_events", metadata,
    Column("task_id", String(36), primary_key=True), Column("seq", Integer, primary_key=True),
    Column("type", String(80)), Column("data", JSON), Column("at", Float))
actions = Table("action_runs", metadata,
    Column("task_id", String(36), primary_key=True), Column("key", String(100), primary_key=True),
    Column("status", String(20)), Column("result", JSON))
artifacts = Table("artifacts", metadata,
    Column("id", String(36), primary_key=True), Column("task_id", String(36), index=True),
    Column("kind", String(24)), Column("content", Text), Column("sha256", String(64)),
    Column("action_key", String(100)), UniqueConstraint("task_id", "action_key"))
episodes = Table("repair_episodes", metadata,
    Column("id", String(36), primary_key=True), Column("owner", String(80), index=True),
    Column("task_id", String(36), unique=True), Column("status", String(20)),
    Column("environment", String(100)), Column("text", Text), Column("created_at", Float),
    Column("embedding_model", String(100)), Column("embedding", JSON))
workers = Table("workers", metadata, Column("id", String(64), primary_key=True), Column("heartbeat", Float))
TERMINAL = {"succeeded", "failed", "cancelled"}


class Conflict(Exception):
    pass


class LeaseLost(Exception):
    pass


class Repository:
    def __init__(self, url, *, initialize=False):
        self.url = url
        self.engine = create_engine(url, pool_pre_ping=True,
            connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {"connect_timeout": 5})
        if initialize:
            metadata.create_all(self.engine)

    def get(self, task_id, owner=None):
        query = select(tasks).where(tasks.c.id == task_id)
        if owner is not None:
            query = query.where(tasks.c.owner == owner)
        with self.engine.connect() as conn:
            row = conn.execute(query).mappings().first()
            return dict(row) if row else None

    def create(self, request, owner="local", key=None):
        data = request.model_dump(mode="json")
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        task_id, now = str(uuid.uuid4()), time.time()
        try:
            with self.engine.begin() as conn:
                # Bound local queue to avoid unbounded execution submissions.
                active = conn.execute(select(func.count()).select_from(tasks).where(tasks.c.status.not_in(list(TERMINAL)))).scalar()
                if active >= 100:
                    raise Conflict("任务队列已满")
                conn.execute(insert(tasks).values(id=task_id, owner=owner, idempotency_key=key,
                    request_hash=digest, request=data, status="queued", version=0, seq=0,
                    fence=0, lease_until=0, cancel=0, created_at=now, updated_at=now))
                self._event(conn, task_id, "task.queued", {})
        except IntegrityError:
            with self.engine.connect() as conn:
                row = conn.execute(select(tasks).where(tasks.c.owner == owner, tasks.c.idempotency_key == key)).mappings().first()
            if not row or row["request_hash"] != digest:
                raise Conflict("幂等键已用于不同请求")
            return row["id"]
        return task_id

    def _event(self, conn, task_id, kind, data):
        seq = conn.execute(update(tasks).where(tasks.c.id == task_id).values(seq=tasks.c.seq + 1).returning(tasks.c.seq)).scalar_one()
        conn.execute(insert(events).values(task_id=task_id, seq=seq, type=kind, data=data, at=time.time()))

    def list_tasks(self, owner="local"):
        with self.engine.connect() as conn:
            rows = conn.execute(select(tasks).where(tasks.c.owner == owner).order_by(tasks.c.created_at.desc()).limit(100)).mappings()
            return [self.public(dict(row)) for row in rows]

    @staticmethod
    def public(row):
        return {k: row[k] for k in ("id", "status", "version", "created_at", "updated_at", "result")} | {
            "goal": row["request"]["goal"], "mode": row["request"]["mode"], "cancel_requested": bool(row["cancel"])}

    def task_events(self, task_id, after=0):
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(events).where(events.c.task_id == task_id,
                events.c.seq > after).order_by(events.c.seq).limit(200)).mappings()]

    def claim(self, worker_id, lease_seconds=90, task_id=None):
        now = time.time()
        with self.engine.begin() as conn:
            query = select(tasks).where(or_(tasks.c.status == "queued",
                (tasks.c.status.in_(["running", "recovering"])) & (tasks.c.lease_until < now)))
            if task_id is not None:
                query = query.where(tasks.c.id == task_id)
            row = conn.execute(query.order_by(tasks.c.created_at).with_for_update(skip_locked=True).limit(1)).mappings().first()
            if row is None:
                return None
            fence = row["fence"] + 1
            changed = conn.execute(update(tasks).where(tasks.c.id == row["id"], tasks.c.fence == row["fence"],
                tasks.c.status == row["status"]).values(status="running", fence=fence,
                lease_owner=worker_id, lease_until=now + lease_seconds, updated_at=now))
            if not changed.rowcount:
                return None
            self._event(conn, row["id"], "task.started" if row["status"] == "queued" else "task.recovering", {"fence": fence})
            return row["id"], fence

    def _guard(self, conn, task_id, fence):
        # Row lock serializes cancellation, heartbeat and result commits on PostgreSQL.
        row = conn.execute(select(tasks).where(tasks.c.id == task_id).with_for_update()).mappings().one()
        if row["fence"] != fence or row["lease_until"] < time.time() or row["status"] != "running":
            raise LeaseLost("Worker 租约已失效")
        return row

    def heartbeat(self, task_id, fence):
        with self.engine.begin() as conn:
            self._guard(conn, task_id, fence)
            conn.execute(update(tasks).where(tasks.c.id == task_id).values(lease_until=time.time() + 90))

    def worker_alive(self, worker_id):
        with self.engine.begin() as conn:
            if not conn.execute(update(workers).where(workers.c.id == worker_id).values(heartbeat=time.time())).rowcount:
                conn.execute(insert(workers).values(id=worker_id, heartbeat=time.time()))

    def worker_ready(self):
        with self.engine.connect() as conn:
            return bool(conn.execute(select(workers.c.id).where(workers.c.heartbeat > time.time() - 30)).first())

    def cancelled(self, task_id, fence):
        row = self.get(task_id)
        return not row or bool(row["cancel"]) or row["fence"] != fence or row["lease_until"] < time.time()

    def cancel(self, task_id):
        with self.engine.begin() as conn:
            row = conn.execute(select(tasks).where(tasks.c.id == task_id).with_for_update()).mappings().one()
            if row["status"] in TERMINAL:
                return
            status = "cancelled" if row["status"] in {"queued", "waiting_input"} else row["status"]
            conn.execute(update(tasks).where(tasks.c.id == task_id).values(cancel=1, status=status, version=tasks.c.version + 1))
            self._event(conn, task_id, "task.cancel_requested", {})

    def finish(self, task_id, fence, status, result):
        with self.engine.begin() as conn:
            row = self._guard(conn, task_id, fence)
            if row["cancel"]:
                status, result = "cancelled", {**result, "result_level": "unverified", "reason": "cancelled"}
            conn.execute(update(tasks).where(tasks.c.id == task_id).values(status=status, result=result,
                version=tasks.c.version + 1, updated_at=time.time(), lease_until=0, resume_value=None))
            self._event(conn, task_id, "input.required" if status == "waiting_input" else "task.finished", result)

    def resume(self, task_id, value):
        from code_assistant.service.contracts import TaskRequest
        with self.engine.begin() as conn:
            row = conn.execute(select(tasks).where(tasks.c.id == task_id).with_for_update()).mappings().one()
            if row["status"] != "waiting_input" or row["version"] != value.version or row["result"].get("interrupt_id") != value.interrupt_id:
                raise Conflict("任务状态或中断版本已改变")
            request = TaskRequest.model_validate(row["request"])
            request.entrypoint, request.cases = value.entrypoint, value.cases
            conn.execute(update(tasks).where(tasks.c.id == task_id).values(request=request.model_dump(mode="json"),
                status="queued", version=tasks.c.version + 1, resume_value={"confirmed": True}))
            self._event(conn, task_id, "input.received", {})

    def action(self, task_id, fence, key, function):
        with self.engine.begin() as conn:
            self._guard(conn, task_id, fence)
            prior = conn.execute(select(actions).where(actions.c.task_id == task_id, actions.c.key == key)).mappings().first()
            if prior:
                if prior["status"] == "completed":
                    return prior["result"]
                raise Conflict("unknown_action_outcome: 上次动作可能已执行，禁止自动重试计费或执行")
            conn.execute(insert(actions).values(task_id=task_id, key=key, status="started"))
            self._event(conn, task_id, "action.started", {"action_id": key})
        result = function()
        with self.engine.begin() as conn:
            self._guard(conn, task_id, fence)
            conn.execute(update(actions).where(actions.c.task_id == task_id, actions.c.key == key).values(status="completed", result=result))
            self._event(conn, task_id, "action.completed", {"action_id": key})
        return result

    def event(self, task_id, fence, kind, data):
        with self.engine.begin() as conn:
            self._guard(conn, task_id, fence)
            self._event(conn, task_id, kind, data)

    def artifact(self, task_id, fence, key, content, kind="code"):
        with self.engine.begin() as conn:
            self._guard(conn, task_id, fence)
            prior = conn.execute(select(artifacts.c.id).where(artifacts.c.task_id == task_id, artifacts.c.action_key == key)).scalar()
            if prior:
                return prior
            artifact_id = str(uuid.uuid4())
            conn.execute(insert(artifacts).values(id=artifact_id, task_id=task_id, action_key=key,
                kind=kind, content=content, sha256=hashlib.sha256(content.encode()).hexdigest()))
            return artifact_id

    def get_artifacts(self, task_id):
        with self.engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(select(artifacts).where(artifacts.c.task_id == task_id)).mappings()]
        def order(row):
            key = row["action_key"]
            return (0, 0) if key == "original" else (1, int(key.rsplit("-", 1)[-1]) if key.rsplit("-", 1)[-1].isdigit() else 0)
        return sorted(rows, key=order)


@contextmanager
def checkpoint_store(url, sqlite_path=".platform/checkpoints.sqlite"):
    if url.startswith("postgresql"):
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.conninfo import make_conninfo
        conninfo = make_conninfo(url.replace("postgresql+psycopg://", "postgresql://"), connect_timeout=5)
        with PostgresSaver.from_conn_string(conninfo) as saver:
            saver.setup()
            yield saver
    else:
        from pathlib import Path
        from langgraph.checkpoint.sqlite import SqliteSaver
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        with SqliteSaver.from_conn_string(sqlite_path) as saver:
            yield saver
