"""Workflow 执行轨迹记录工具（对应需求 5）。

用 Pydantic 模型 TraceEvent 统一轨迹事件格式，提供两类事件：
1. node  事件：记录某个节点的执行状态（ok / fail）及关键数据（错误类型、审查结论等）。
2. route 事件：记录一次路由决策（从哪个节点来、去往哪个节点、决策原因）。

轨迹以 list[dict] 的形式存在 GraphState 的 trace / route_path 字段中，
随图一起流转，节点函数通过 record_node / record_route 追加事件。

对外还提供 summarize / dump 两个工具函数，用于流程调试与分析。
"""

import json
import os
from pathlib import Path
from datetime import datetime, timezone

from schemas.models import TraceEvent


def new_event(kind: str, node: str, status: str, **detail) -> dict:
    """构造一条轨迹事件（dict 形式，便于 LangGraph 状态合并）。"""
    return TraceEvent(
        timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        kind=kind,  # "node"：节点执行；"route"：路由决策
        node=node,
        status=status,  # "ok" | "fail"
        detail=detail,
    ).model_dump()


def record_node(trace: list[dict], node: str, status: str, **detail) -> list[dict]:
    """在当前轨迹上追加一条节点执行事件，返回新轨迹。"""
    return trace + [new_event("node", node, status, **detail)]


def record_route(trace: list[dict], from_node: str, to_node: str, reason: str) -> list[dict]:
    """在当前轨迹上追加一条路由决策事件，返回新轨迹。"""
    return trace + [new_event("route", from_node, "ok", to=to_node, reason=reason)]


def summarize(state: dict) -> str:
    """把轨迹 / 路由路径 / 修复次数渲染成可读摘要。"""
    route = " -> ".join(state.get("route_path", [])) or "（空）"
    repairs = max(0, state.get("iteration", 0) - 1)

    lines = [
        "=" * 60,
        "Workflow 执行轨迹摘要",
        "=" * 60,
        f"路由路径：{route}",
        f"代码生成次数（iteration）：{state.get('iteration', 0)}",
        f"自动修复次数（repair）：{repairs}",
        f"失败轨迹条数：{len(state.get('failure_trail', []))}",
        "",
        "节点执行明细：",
    ]

    for ev in state.get("trace", []):
        if ev["kind"] != "node":
            continue
        detail = ev.get("detail", {})
        extra = " | ".join(
            f"{k}={v}" for k, v in detail.items() if v not in ("", None)
        )
        suffix = f"  ({extra})" if extra else ""
        lines.append(f"  [{ev['status']:>4}] {ev['node']:<10} {ev['timestamp']}{suffix}")

    return "\n".join(lines)


def snapshot(state: dict) -> dict:
    """明确白名单：不保存 task.cases（含预期答案）、环境变量或认证参数。"""
    return {
        "schema_version": 1,
        "exec_error_type": state.get("exec_error_type", ""),
        "exec_error": state.get("exec_error", ""),
        "review_history": state.get("review_history", []),
        "reflection_history": state.get("reflection_history", []),
        "run_config": state.get("run_config", {}),
        "llm_calls": state.get("llm_calls", 0),
        "budget_tokens": state.get("budget_tokens", 0),
        "call_history": state.get("call_history", []),
        "attempt_history": state.get("attempt_history", []),
        "task_success": state.get("task_success", False),
        "termination_reason": state.get("termination_reason", ""),
        "acceptance": state.get("acceptance", {}),
        "generation_history": state.get("generation_history", []),
        "route_path": state.get("route_path", []),
        "iteration": state.get("iteration", 0),
        "repair_count": max(0, state.get("iteration", 0) - 1),
        "trace": state.get("trace", []),
        "failure_trail": state.get("failure_trail", []),
    }


def atomic_json(path: str | Path, payload: dict) -> None:
    target = Path(path)
    temporary = target.with_suffix(target.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)


def dump(state: dict, path: str) -> None:
    """原子替换轨迹快照，避免中途写入留下半份 JSON。"""
    atomic_json(path, snapshot(state))
