"""确定性的失败指纹、有限上下文与条件反思策略。"""
import ast
import hashlib
import json
import re


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = "\n[已裁剪]\n"
    if limit <= len(marker):
        return text[:limit]
    head = (limit - len(marker)) * 2 // 3
    return text[:head] + marker + text[-(limit - len(marker) - head):]


def fingerprint(error_type: str, message: str, case_name: str = "") -> str:
    # 去除运行环境噪声，保留异常类、符号名及失败用例，避免把所有逻辑错误合并。
    normalized = re.sub(r'File "[^"]+", line \d+', 'File <code>, line <n>', message)
    normalized = re.sub(r"line \d+", "line <n>", normalized)
    normalized = re.sub(r"0x[0-9a-fA-F]+", "<address>", normalized)
    normalized = re.sub(r"sandbox-[a-zA-Z0-9_-]+", "<sandbox>", normalized)
    normalized = " ".join(normalized.split())
    return hashlib.sha256(json.dumps([error_type, case_name, normalized], ensure_ascii=False).encode()).hexdigest()[:20]


def code_fingerprint(generation: dict) -> str:
    source = generation.get("imports", "") + "\n" + generation.get("code", "")
    try:
        normalized = ast.dump(ast.parse(source), include_attributes=False)
    except (SyntaxError, ValueError, RecursionError):
        normalized = source.strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:20]


def strategy(state: dict) -> str:
    if state.get("repeated_error_count", 0) >= 2:
        return "change_approach"
    return {
        "syntax_error": "fix_syntax", "import_error": "fix_import",
        "security_error": "respect_offline_contract", "timeout_error": "reduce_complexity",
        "resource_error": "reduce_resources", "test_failure": "fix_logic",
    }.get(state.get("exec_error_type"), "fix_runtime")


def feedback(state: dict) -> str:
    limit = state["run_config"]["feedback_chars"]
    if not state.get("generation_history"):
        return "首次生成。"
    parts = [f"修复策略：{state.get('repair_strategy', 'fix_runtime')}",
             f"本轮错误：{state.get('exec_error_type', '')}：{clip(state.get('exec_error', ''), 1200)}"]
    if state.get("repair_strategy") == "change_approach":
        parts.append("同一失败已重复：更换实现思路，不要只改注释或变量名；优先采用更简单的确定性实现。")
    # 最新建议及当前代码优先；更早的整段代码不再反复注入。
    for field in ("reflection", "review"):
        if state.get(field):
            parts.append(field + ": " + clip(json.dumps(state[field], ensure_ascii=False), limit // 5))
    generation = state["generation_history"][-1]
    parts.append("最近代码：\n" + clip(generation["imports"] + "\n" + generation["code"], limit // 2))
    parts.append("最近失败摘要：\n" + summary(state))
    return clip("\n\n".join(parts), limit)


def summary(state: dict) -> str:
    records = state.get("failure_trail", [])[-state["run_config"]["history_window"]:]
    return clip("\n".join(f"第 {r['iteration']} 轮 [{r['error_type']}] {clip(r['error_message'], 200)}"
                          for r in records), state["run_config"]["feedback_chars"] // 3)
