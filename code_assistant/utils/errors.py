"""对外错误分类不包含供应商异常原文，以免把认证信息写入日志。"""
from pydantic import ValidationError
from openai import (
    APIConnectionError, APITimeoutError, AuthenticationError, PermissionDeniedError,
    RateLimitError, APIStatusError,
)


class OutputParseError(ValueError):
    pass


def model_error_code(exc: Exception) -> str:
    if isinstance(exc, OutputParseError):
        return "model_output_invalid"
    if isinstance(exc, (APITimeoutError, TimeoutError)):
        return "model_timeout"
    if isinstance(exc, AuthenticationError):
        return "model_authentication"
    if isinstance(exc, PermissionDeniedError):
        return "model_permission"
    if isinstance(exc, RateLimitError):
        return "model_rate_limit"
    if isinstance(exc, APIConnectionError):
        return "model_connection"
    if isinstance(exc, APIStatusError):
        return "model_server_error" if exc.status_code >= 500 else "model_request_invalid"
    if isinstance(exc, ValidationError):
        return "model_configuration"
    return "model_internal_error"
