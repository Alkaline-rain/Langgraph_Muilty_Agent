"""Queue platform tasks through the same service as HTTP clients."""
import argparse
from pathlib import Path
from backend.settings import database_url, checkpoint_path
from code_assistant.persistence.database import Repository, checkpoint_store
from code_assistant.service.contracts import TaskRequest
from code_assistant.service.tasks import TaskService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="领取并执行队列任务；否则仅提交")
    args = parser.parse_args()
    request = TaskRequest.model_validate_json(args.task.read_text(encoding="utf-8"))
    repo = Repository(database_url())
    task_id = repo.create(request.model_copy(update={"source_type": "cli"}))
    print(task_id)
    if args.run:
        from backend.worker.main import worker_lock
        with worker_lock(repo), checkpoint_store(database_url(), checkpoint_path()) as saver:
            claimed = repo.claim("cli", task_id=task_id)
            if claimed:
                TaskService(repo, saver).run(*claimed)
        print(repo.public(repo.get(task_id)))


if __name__ == "__main__":
    main()
