"""逐次调用预算和 usage 记录。未知 usage 单独标记，不伪造 Token 实测值。"""
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import time

from config import get_llm
from utils.errors import OutputParseError, model_error_code


class BudgetExceeded(Exception):
    pass


@dataclass
class CallRuntime:
    remaining_tokens: int
    output_limit: int
    prompt_chars: int
    ledger: dict = field(default_factory=dict)

    def reserve(self, prompt: str, schema: dict):
        if len(prompt) > self.prompt_chars:
            raise BudgetExceeded("完整提示词超过字符上限；不会静默裁掉任务要求")
        # UTF-8 字节数 + schema + framing 余量，为保守准入估计，不是厂商 tokenizer。
        estimate = len(prompt.encode()) + len(json.dumps(schema, ensure_ascii=False).encode()) + 512
        reservation = estimate + self.output_limit
        if reservation > self.remaining_tokens:
            raise BudgetExceeded("剩余 Token 预算不足以预留本次输入及最大输出")
        self.ledger.update(input_estimate=estimate, reservation=reservation)


CURRENT = ContextVar("agent_call_runtime", default=None)


def structured_call(prompt, schema, values, temperature):
    runtime = CURRENT.get()
    if runtime is None:
        raise RuntimeError("Agent 必须通过工作流预算入口调用")
    messages = prompt.format_messages(**values)
    serialized = "\n".join(str(message.content) for message in messages)
    runtime.reserve(serialized, schema.model_json_schema())
    llm = get_llm(temperature=temperature, max_tokens=runtime.output_limit)
    chain = llm.with_structured_output(schema, include_raw=True)
    runtime.ledger["started"] = True
    reply = chain.invoke(messages)
    usage = getattr(reply.get("raw"), "usage_metadata", None)
    if usage and isinstance(usage.get("total_tokens"), int):
        runtime.ledger["usage"] = dict(usage)
    if reply.get("parsing_error") or reply.get("parsed") is None:
        raise OutputParseError("模型结构化输出解析失败")
    return reply["parsed"]


def call_agent(state: dict, role: str, function, values: dict):
    config = state["run_config"]
    if state["llm_calls"] >= config["max_calls"]:
        return None, {}, "call_budget", "模型调用次数预算耗尽"
    runtime = CallRuntime(config["max_total_tokens"] - state["budget_tokens"],
                          config["max_output_tokens"], config["prompt_chars"])
    token = CURRENT.set(runtime)
    started = time.monotonic()
    result, reason, error = None, "", ""
    try:
        # 对测试替身也做准入检查；真实模型入口按完整模板再次精确计算字节估计。
        runtime.reserve(json.dumps(values, ensure_ascii=False), {})
        result = function(**values)
    except BudgetExceeded as exc:
        reason, error = "token_budget", str(exc)
    except Exception as exc:
        # 不持久化厂商原始异常文本，避免带入 URL / 认证信息。
        reason, error = "model_error", f"模型调用失败：{type(exc).__name__}"
        runtime.ledger["error_code"] = model_error_code(exc)
    finally:
        CURRENT.reset(token)
    if reason == "token_budget" and not runtime.ledger.get("started"):
        return None, {}, reason, error
    usage = runtime.ledger.get("usage")
    charge = usage["total_tokens"] if usage else runtime.ledger["reservation"]
    event = {
        "role": role, "status": "error" if reason else "ok",
        "duration_ms": int((time.monotonic() - started) * 1000),
        "usage": usage, "usage_source": "provider" if usage else "unknown",
        "request_started": runtime.ledger.get("started", False),
        "error_code": runtime.ledger.get("error_code", ""),
        "input_estimate": runtime.ledger["input_estimate"],
        "budget_charge": charge,
    }
    update = {"llm_calls": state["llm_calls"] + 1,
              "budget_tokens": state["budget_tokens"] + charge,
              "call_history": state["call_history"] + [event]}
    return result, update, reason, error
