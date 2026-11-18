import re
import subprocess
from code_assistant.utils.docker_sandbox import DockerSandbox, find_docker


def library_version():
    """Probe installed candidate library, not the host SDK. No candidate/user input."""
    docker = find_docker()
    if not docker:
        return ""
    try:
        result = subprocess.run([docker, "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534",
            "--memory", "64m", "--pids-limit", "32", "--entrypoint", "python",
            DockerSandbox().image, "-I", "-c",
            "from importlib.metadata import version; print(version('langchain-core'))"],
            capture_output=True, text=True, timeout=15)
        value = result.stdout.strip()
        return value if result.returncode == 0 and re.fullmatch(r"[0-9A-Za-z.+_-]{1,60}", value) else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""
