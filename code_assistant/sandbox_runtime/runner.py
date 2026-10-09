"""仅在容器中运行；候选代码实现 solve(payload)。"""
import contextlib
import importlib.util
import json
import sys


def main():
    payload = json.load(sys.stdin)
    with contextlib.redirect_stdout(sys.stderr):
        spec = importlib.util.spec_from_file_location("candidate", "/code/candidate.py")
        candidate = importlib.util.module_from_spec(spec)
        sys.modules["candidate"] = candidate
        spec.loader.exec_module(candidate)
        result = candidate.solve(payload)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
