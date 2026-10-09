"""宿主侧可信验收：候选代码只获得输入，不能自行宣告测试通过。"""
import json
import time
from collections.abc import Callable

from schemas.models import AcceptanceResult, CaseResult, ExecutionResult, TaskSpec
from utils.sandbox import execute_code
from utils.static_validation import validate_code


def _reject_constant(value):
    raise ValueError(f"不支持非有限 JSON 数值 {value}")


def _equal(actual, expected):
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(_equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def evaluate(imports: str, code: str, task: TaskSpec,
             executor: Callable[..., ExecutionResult] = execute_code) -> AcceptanceResult:
    start = time.monotonic()
    validation = validate_code(imports, code)
    if not validation.success:
        return AcceptanceResult(success=False, error_type=validation.error_type,
                                error_message=validation.error_message)
    results = []
    for case in task.cases:
        execution = executor(imports, code, stdin=json.dumps(case.input, ensure_ascii=False, allow_nan=False))
        error_type, message = execution.error_type, execution.error_message
        if execution.success:
            try:
                actual = json.loads(execution.stdout, parse_constant=_reject_constant)
                if not _equal(actual, case.expected):
                    error_type, message = "test_failure", f"用例 {case.name} 返回值与可信预期不一致"
            except (ValueError, RecursionError):
                error_type, message = "test_failure", f"用例 {case.name} 未返回单个有效 JSON 值"
        elif not error_type:
            error_type, message = "runtime_error", "执行器返回失败"
        results.append(CaseResult(name=case.name, passed=not error_type,
                                  error_type=error_type, error_message=message,
                                  stdout=execution.stdout[:4096], stderr=execution.stderr[-4096:],
                                  duration_ms=execution.duration_ms))
        if error_type:
            return AcceptanceResult(success=False, error_type=error_type, error_message=message,
                                    cases=results, duration_ms=int((time.monotonic() - start) * 1000))
    return AcceptanceResult(success=True, cases=results,
                            duration_ms=int((time.monotonic() - start) * 1000))
