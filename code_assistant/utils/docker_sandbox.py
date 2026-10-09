"""离线一次性容器；预检、资源限制、输出限额、超时及有界清理。"""
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from schemas.models import ExecutionResult
from utils.static_validation import validate_code

MAX_OUTPUT = 64 * 1024
MAX_INPUT = 64 * 1024


def find_docker():
    def is_file(path):
        try:
            return Path(path).is_file()
        except OSError:
            return False
    explicit = os.getenv("DOCKER_EXECUTABLE")
    if explicit:
        return explicit if is_file(explicit) else None
    found = shutil.which("docker")
    if found:
        return found
    if os.name == "nt":
        candidates = [Path(os.getenv("LOCALAPPDATA", "")) / "Programs/DockerDesktop/resources/bin/docker.exe",
                      Path(os.getenv("ProgramFiles", "C:/Program Files")) / "Docker/Docker/resources/bin/docker.exe"]
        return next((str(path) for path in candidates if is_file(path)), None)
    return None


def run_bounded(cmd, input_bytes, timeout):
    """持续排空两条管道，单管道最多保留 64 KiB，超限立即终止 CLI。"""
    with tempfile.TemporaryFile() as source:
        source.write(input_bytes)
        source.seek(0)
        with subprocess.Popen(cmd, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            output = [bytearray(), bytearray()]
            exceeded = threading.Event()

            def drain(pipe, index):
                try:
                    while chunk := pipe.read(4096):
                        room = MAX_OUTPUT - len(output[index])
                        output[index].extend(chunk[:room])
                        if len(chunk) > room:
                            exceeded.set()
                finally:
                    pipe.close()

            readers = [threading.Thread(target=drain, args=(pipe, i), daemon=True)
                       for i, pipe in enumerate((proc.stdout, proc.stderr))]
            for reader in readers:
                reader.start()
            deadline = time.monotonic() + timeout
            reason = ""
            try:
                while proc.poll() is None:
                    if exceeded.wait(0.02):
                        reason = "output_limit"
                        break
                    if time.monotonic() >= deadline:
                        reason = "timeout"
                        break
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=5)
                for reader in readers:
                    reader.join(timeout=5)
            if exceeded.is_set():
                reason = "output_limit"
            return proc.returncode, *(bytes(buf).decode("utf-8", errors="replace") for buf in output), reason


class DockerSandbox:
    DEFAULT_IMAGE = "code-assistant-sandbox:offline-v1"

    def __init__(self, image=None):
        self.image = image or os.getenv("SANDBOX_IMAGE") or self.DEFAULT_IMAGE

    def preflight(self):
        docker = find_docker()
        if not docker:
            return "未找到 Docker CLI，请安装并启动 Docker Desktop/Linux Docker"
        try:
            for cmd in ([docker, "info", "--format", "{{.OSType}}"],
                        [docker, "image", "inspect", self.image]):
                result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
                if result.returncode:
                    return f"Docker 服务或本地镜像不可用：{result.stderr[-1000:]}；镜像 {self.image} 需先构建"
                if cmd[1] == "info" and result.stdout.strip() != "linux":
                    return "需要 Linux 容器模式"
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"Docker 预检失败：{exc}"
        return ""

    def available(self):
        return not self.preflight()

    def image_identity(self):
        docker = find_docker()
        if not docker:
            return None
        try:
            proc = subprocess.run([docker, "image", "inspect", self.image, "--format", "{{.Id}}"],
                                  capture_output=True, text=True, timeout=10)
            return proc.stdout.strip() if proc.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    def execute(self, imports, code, timeout=10, memory="256m", cpus="0.5", pids=64, *, stdin="null"):
        validation = validate_code(imports, code)
        if not validation.success:
            return validation
        if timeout <= 0 or pids <= 0:
            return ExecutionResult(success=False, error_type="task_error", error_message="超时和进程数必须为正数")
        data = stdin.encode("utf-8")
        if len(data) > MAX_INPUT:
            return ExecutionResult(success=False, error_type="task_error", error_message="用例输入超过 64 KiB")
        problem = self.preflight()
        if problem:
            return ExecutionResult(success=False, error_type="sandbox_error", error_message=problem)
        name = f"sandbox-{uuid.uuid4().hex}"
        start = time.monotonic()
        result = None
        with tempfile.TemporaryDirectory(prefix="sandbox_") as workdir:
            candidate = Path(workdir) / "candidate.py"
            candidate.write_text(imports + "\n" + code + "\n", encoding="utf-8")
            # Linux 临时目录默认 0700；容器非 root 用户需要可遍历目录及读取脚本。
            Path(workdir).chmod(0o755)
            candidate.chmod(0o644)
            cmd = [find_docker() or "docker", "run", "--pull", "never", "--name", name, "--rm", "-i",
                   "--network", "none", "--memory", memory, "--memory-swap", memory,
                   "--cpus", cpus, "--pids-limit", str(pids), "--read-only",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "--user", "65534:65534", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                   "--log-driver", "none", "-e", "PYTHONDONTWRITEBYTECODE=1",
                   "-e", "PYTHONUNBUFFERED=1", "-e", "LANGSMITH_TRACING=false", "-e", "HOME=/tmp",
                   "-v", f"{Path(workdir).as_posix()}:/code:ro", "-w", "/code", self.image,
                   "timeout", "--kill-after=1", str(timeout), "python", "-I", "/opt/runner.py"]
            try:
                rc, out, err, reason = run_bounded(cmd, data, timeout + 5)
                elapsed = int((time.monotonic() - start) * 1000)
                if reason:
                    result = ExecutionResult(success=False,
                        error_type="timeout_error" if reason == "timeout" else "resource_error",
                        error_message="执行超时" if reason == "timeout" else "输出超过 64 KiB 限制",
                        duration_ms=elapsed)
                else:
                    result = self._classify(rc, out, err, elapsed)
            except (OSError, subprocess.SubprocessError) as exc:
                result = ExecutionResult(success=False, error_type="sandbox_error", error_message=str(exc)[:1000])
            finally:
                cleanup_error = self._cleanup(name)
            if cleanup_error:
                result = ExecutionResult(success=False, error_type="sandbox_error",
                                         error_message=f"容器清理未确认，请检查 {name}：{cleanup_error}")
        return result

    @staticmethod
    def _cleanup(name):
        try:
            proc = subprocess.run([find_docker() or "docker", "rm", "-f", name], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=10)
            if proc.returncode and "No such container" not in proc.stderr:
                return proc.stderr[-1000:]
        except (OSError, subprocess.SubprocessError) as exc:
            return str(exc)
        return ""

    @staticmethod
    def _classify(returncode, stdout, stderr, duration_ms):
        kind = ""
        message = stderr[-1500:]
        if returncode == 0:
            return ExecutionResult(success=True, stdout=stdout, stderr=stderr, returncode=returncode, duration_ms=duration_ms)
        if returncode in (125, 126, 127):
            kind = "sandbox_error"
        elif returncode == 124:
            kind = "timeout_error"
        elif returncode == 137:
            kind = "resource_error"
            message = "进程收到 SIGKILL（可能为资源限制或容器内超时），不能仅凭退出码确认 OOM"
        elif "SyntaxError" in stderr or "IndentationError" in stderr:
            kind = "syntax_error"
        elif "No module named 'langchain_core'" in stderr:
            kind = "sandbox_error"
        elif "ModuleNotFoundError" in stderr or "ImportError" in stderr:
            kind = "import_error"
        else:
            kind = "runtime_error"
        return ExecutionResult(success=False, error_type=kind, error_message=message,
                               stdout=stdout, stderr=stderr, returncode=returncode, duration_ms=duration_ms)
