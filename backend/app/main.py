import asyncio
import json
import secrets
import time
from collections import defaultdict, deque
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse, PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from backend.settings import database_url, token
from code_assistant.persistence.database import Repository, Conflict, TERMINAL
from code_assistant.service.contracts import TaskRequest, ResumeRequest
from code_assistant.skills.registry import SkillRegistry
from code_assistant.memory.store import MemoryStore
from code_assistant.utils.docker_sandbox import DockerSandbox


def create_app(repository=None, access_token=None):
    repo = repository or Repository(database_url())
    secret = access_token or token()
    app = FastAPI(title="Code Repair Agent", version="2.0.0")
    origins = {"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"}
    app.add_middleware(CORSMiddleware, allow_origins=list(origins), allow_credentials=False,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "Last-Event-ID"])
    submissions = defaultdict(deque)

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        origin = request.headers.get("origin", "")
        if origin and origin not in origins and not origin.startswith("chrome-extension://"):
            return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
        if request.method in {"POST", "PUT", "PATCH"}:
            # Limit streamed/chunked bodies as well as Content-Length.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 1024 * 1024:
                    return JSONResponse({"detail": "body_too_large"}, status_code=413)
            request._body = bytes(body)
        return await call_next(request)

    def authenticate(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, "Bearer " + secret):
            raise HTTPException(401, "需要本地平台访问令牌")
        return "local"

    def owned(task_id, owner):
        row = repo.get(task_id, owner)
        if row is None:
            raise HTTPException(404, "任务不存在")
        return row

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/health/live")
    def live():
        return {"alive": True}

    @app.get("/health/ready")
    def ready(owner=Depends(authenticate)):
        try:
            with repo.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            worker = repo.worker_ready()
            docker = not DockerSandbox().preflight()
            return JSONResponse({"database": True, "worker": worker, "docker": docker}, status_code=200 if worker and docker else 503)
        except Exception:
            return JSONResponse({"database": False}, status_code=503)

    @app.post("/api/v1/tasks", status_code=202)
    def create_task(body: TaskRequest, owner=Depends(authenticate), idempotency_key: str | None = Header(default=None)):
        if idempotency_key and len(idempotency_key) > 128:
            raise HTTPException(422, "幂等键过长")
        now = time.monotonic()
        recent = submissions[owner]
        while recent and recent[0] < now - 60:
            recent.popleft()
        if len(recent) >= 20:
            raise HTTPException(429, "每分钟最多提交 20 个任务")
        recent.append(now)
        return {"task_id": repo.create(body, owner, idempotency_key)}

    @app.get("/api/v1/tasks")
    def list_tasks(owner=Depends(authenticate)):
        return repo.list_tasks(owner)

    @app.get("/api/v1/tasks/{task_id}")
    def get_task(task_id: str, owner=Depends(authenticate)):
        return repo.public(owned(task_id, owner))

    @app.post("/api/v1/tasks/{task_id}/cancel")
    def cancel(task_id: str, owner=Depends(authenticate)):
        owned(task_id, owner)
        repo.cancel(task_id)
        return {"accepted": True}

    @app.post("/api/v1/tasks/{task_id}/resume", status_code=202)
    def resume(task_id: str, body: ResumeRequest, owner=Depends(authenticate)):
        owned(task_id, owner)
        repo.resume(task_id, body)
        return {"accepted": True}

    @app.get("/api/v1/tasks/{task_id}/events")
    async def stream(task_id: str, request: Request, after_seq: int = 0,
                     last_event_id: str = Header(default="0"), owner=Depends(authenticate)):
        owned(task_id, owner)
        try:
            cursor = max(after_seq, int(last_event_id), 0)
        except ValueError:
            raise HTTPException(422, "无效事件序号")
        async def events():
            nonlocal cursor
            while not await request.is_disconnected():
                batch = await asyncio.to_thread(repo.task_events, task_id, cursor)
                for item in batch:
                    cursor = item["seq"]
                    yield f"id: {cursor}\nevent: {item['type']}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                row = await asyncio.to_thread(repo.get, task_id)
                if cursor >= row["seq"] and (row["status"] in TERMINAL or row["status"] == "waiting_input"):
                    break
                yield ": keepalive\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/v1/tasks/{task_id}/artifacts")
    def artifact_list(task_id: str, owner=Depends(authenticate)):
        owned(task_id, owner)
        return [{k: v for k, v in a.items() if k != "content"} for a in repo.get_artifacts(task_id)]

    @app.get("/api/v1/artifacts/{artifact_id}")
    def artifact(artifact_id: str, owner=Depends(authenticate)):
        from sqlalchemy import select
        from code_assistant.persistence.database import artifacts
        with repo.engine.connect() as conn:
            row = conn.execute(select(artifacts).where(artifacts.c.id == artifact_id)).mappings().first()
        if not row:
            raise HTTPException(404)
        owned(row["task_id"], owner)
        return PlainTextResponse(row["content"], headers={"Content-Disposition": f'attachment; filename="{artifact_id}.py"'})

    @app.get("/api/v1/skills")
    def skills(owner=Depends(authenticate)):
        return SkillRegistry().catalog()

    @app.get("/api/v1/knowledge")
    def knowledge_status(owner=Depends(authenticate)):
        from sqlalchemy import inspect
        from code_assistant.knowledge.repository import KnowledgeRepository
        if not inspect(repo.engine).has_table("knowledge_snapshots"):
            return []
        return KnowledgeRepository(repo.engine).status()

    @app.get("/api/v1/tasks/{task_id}/retrievals")
    def retrievals(task_id: str, owner=Depends(authenticate)):
        owned(task_id, owner)
        from sqlalchemy import inspect
        from code_assistant.knowledge.repository import KnowledgeRepository
        if not inspect(repo.engine).has_table("retrieval_runs"):
            return []
        return KnowledgeRepository(repo.engine).history(task_id)

    @app.get("/api/v1/memories")
    def memories(owner=Depends(authenticate)):
        return MemoryStore(repo).list(owner)

    @app.delete("/api/v1/memories/{memory_id}")
    def delete_memory(memory_id: str, owner=Depends(authenticate)):
        if not MemoryStore(repo).delete(memory_id, owner):
            raise HTTPException(404)
        return {"deleted": True}

    return app


def app_factory():
    return create_app()
