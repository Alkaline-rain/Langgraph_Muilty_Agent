"""不调用 LLM 的环境诊断；--probe 运行固定的容器烟雾测试。"""
import argparse
import importlib.metadata
import json
import platform
import subprocess

from utils.docker_sandbox import DockerSandbox, find_docker


def diagnose(probe=False):
    docker = find_docker()
    sandbox = DockerSandbox()
    report = {"python": platform.python_version(), "docker_cli": docker,
              "image": sandbox.image, "image_id": None,
              "packages": {name: importlib.metadata.version(name) for name in ("langgraph", "langchain-core", "langchain-deepseek")}}
    report["preflight_error"] = sandbox.preflight()
    report["ready"] = not report["preflight_error"]
    if report["ready"]:
        report["image_id"] = sandbox.image_identity()
    if probe and report["ready"]:
        result = sandbox.execute("", "def solve(payload):\n    return payload + 1", stdin="41")
        report["probe"] = result.model_dump()
        report["ready"] = result.success and result.stdout.strip() == "42"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    report = diagnose(args.probe)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ready"] else 2)


if __name__ == "__main__":
    main()
