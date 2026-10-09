"""旧入口兼容层：禁止在宿主进程内执行生成代码。"""
from schemas.models import ExecutionResult


def execute_code(imports: str, code: str, timeout: int = 10) -> ExecutionResult:
    return ExecutionResult(success=False, error_type="sandbox_error", sandbox="disabled",
                           error_message="进程内执行器已禁用，请通过 utils.sandbox 使用 Docker")
