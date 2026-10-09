"""逐节点可恢复的审计产物；不宣称提供自动续跑或 LangGraph checkpoint。"""
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import time
import uuid

from config import MODEL_NAME
from utils.trace import atomic_json, snapshot, record_node, record_route

ROOT = Path(__file__).resolve().parents[2]


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class RunStore:
    def __init__(self, root, state, metadata=None):
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        self.directory = Path(root) / self.run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        (self.directory / "code").mkdir()
        self.code_count = 0
        self.started = time.monotonic()
        task_hash = hashlib.sha256(json.dumps(state.get("task", {}), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        sources = hashlib.sha256()
        for path in sorted((ROOT / "code_assistant").rglob("*.py")):
            sources.update(str(path.relative_to(ROOT)).encode())
            sources.update(path.read_bytes())
        mode = (metadata or {}).get("mode", "live")
        self.manifest = {
            "schema_version": 1, "run_id": self.run_id, "started_at": utc_now(), "status": "running",
            "python": platform.python_version(), "platform": platform.system(),
            "model": "scripted-fixture" if mode in {"simulate", "scripted_agents_real_docker"} else MODEL_NAME,
            "source_sha256": sources.hexdigest(), "task_sha256": task_hash,
            "task_case_count": len(state.get("task", {}).get("cases", [])),
            "requirements_sha256": hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest(),
            "packages": {name: importlib.metadata.version(name) for name in ("langgraph", "langchain-core", "langchain-deepseek")},
            "run_config": state.get("run_config", {}), "max_iteration": state.get("max_iteration"),
            "metadata": metadata or {},
        }
        atomic_json(self.directory / "manifest.json", self.manifest)
        self.event("workflow_start")
        self.save(state)

    def event(self, kind, **detail):
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"timestamp": utc_now(), "kind": kind, **detail}, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def save(self, state):
        history = state.get("generation_history", [])
        while self.code_count < len(history):
            gen = history[self.code_count]
            filename = self.directory / "code" / f"attempt_{self.code_count + 1:03d}.py"
            filename.write_text(gen["imports"] + "\n" + gen["code"] + "\n", encoding="utf-8")
            self.code_count += 1
        atomic_json(self.directory / "state.json", snapshot(state))

    def finish(self, state):
        self.save(state)
        reason = state.get("termination_reason")
        self.manifest.update(status="succeeded" if state.get("task_success") else "failed",
                             finished_at=utc_now(), elapsed_ms=int((time.monotonic() - self.started) * 1000),
                             termination_reason=reason)
        if reason == "interrupted":
            self.manifest["status"] = "interrupted"
        atomic_json(self.directory / "manifest.json", self.manifest)
        self.event("workflow_end", status=self.manifest["status"], reason=reason)


def mark_failure(state, reason, error_type, message):
    result = {**state, "task_success": False, "exec_success": False,
              "termination_reason": reason, "exec_error_type": error_type, "exec_error": message}
    trace = record_node(state.get("trace", []), "workflow", "fail", reason=reason, error_type=error_type)
    result["trace"] = record_route(trace, "workflow", "END", reason)
    return result


def run_recorded(graph, state, output_root, *, store=None, metadata=None):
    """每个完成节点立即快照；异常及 Ctrl+C 保存最后已知状态。"""
    store = store or RunStore(output_root, state, metadata)
    current = dict(state)
    try:
        for chunk in graph.stream(current, config={"recursion_limit": state["max_iteration"] * 4 + 10}, stream_mode="updates"):
            for node, update in chunk.items():
                if not isinstance(update, dict):
                    continue
                current.update(update)
                store.event("node_completed", node=node, iteration=current["iteration"],
                            llm_calls=current.get("llm_calls", 0), error_type=current.get("exec_error_type", ""))
                store.save(current)
    except KeyboardInterrupt:
        current = mark_failure(current, "interrupted", "interrupted", "用户中断；保留最后完成节点")
    except Exception as exc:
        current = mark_failure(current, "internal_error", "internal_error", f"工作流内部异常：{type(exc).__name__}")
    store.finish(current)
    return current, store.directory
