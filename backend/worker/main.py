import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import text
from backend.settings import database_url, checkpoint_path, ROOT
from code_assistant.persistence.database import Repository, checkpoint_store
from code_assistant.service.tasks import TaskService


@contextmanager
def worker_lock(repo):
    """First release: globally one worker / one container at a time, not an unbounded pool."""
    if repo.engine.dialect.name == "postgresql":
        with repo.engine.connect() as conn:
            if not conn.execute(text("SELECT pg_try_advisory_lock(17291, 1)")).scalar():
                raise RuntimeError("已有平台 Worker 正在运行")
            try:
                yield
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(17291, 1)"))
    else:
        path = ROOT / ".platform/worker.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def main():
    repo = Repository(database_url())
    worker_id = str(uuid.uuid4())
    with worker_lock(repo), checkpoint_store(database_url(), checkpoint_path()) as saver:
        service = TaskService(repo, saver)
        print("Code Agent Worker ready; concurrency=1", flush=True)
        try:
            while True:
                repo.worker_alive(worker_id)
                claimed = repo.claim(worker_id)
                if claimed:
                    service.run(*claimed)
                else:
                    time.sleep(1)
        except KeyboardInterrupt:
            print("Worker stopped; incomplete actions remain auditable.")


if __name__ == "__main__":
    main()
