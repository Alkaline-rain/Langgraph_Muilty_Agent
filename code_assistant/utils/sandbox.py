"""不可信代码只进入 Docker；auto 不再允许降级。"""
import os
from schemas.models import ExecutionResult
from utils.docker_sandbox import DockerSandbox
from utils.static_validation import validate_code


def execute_code(imports: str, code: str, timeout: int = 10,
                 memory: str = "256m", cpus: str = "0.5", pids: int = 64,
                 *, stdin: str = "null") -> ExecutionResult:
    validation = validate_code(imports, code)
    if not validation.success:
        return validation
    if os.getenv("SANDBOX_MODE", "docker").strip().lower() not in {"docker", "auto"}:
        return ExecutionResult(success=False, error_type="sandbox_error",
                               error_message="不可信代码仅支持 docker/auto（均不降级），不支持 safe 模式")
    return DockerSandbox().execute(imports, code, timeout, memory, cpus, pids, stdin=stdin)
