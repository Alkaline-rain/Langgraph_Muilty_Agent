"""Run unittest and preserve a machine-readable result. No model calls."""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code_assistant"))


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def record(self, test, status, message=""):
        self.records.append({"test": test.id(), "status": status, "message": message,
                             "duration_ms": int((time.monotonic() - self.started) * 1000)})

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, "passed")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.record(test, "failed", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self.record(test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, "skipped", reason)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err:
            self.record(subtest, "failed", self._exc_info_to_string(err, test))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "test_results")
    args = parser.parse_args()
    if args.docker:
        os.environ["RUN_DOCKER_TESTS"] = "1"
    else:
        os.environ.pop("RUN_DOCKER_TESTS", None)
    started = datetime.now(timezone.utc)
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(suite)
    directory = args.output / (started.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    report = {"started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
              "python": platform.python_version(), "docker_requested": args.docker,
              "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped), "success": result.wasSuccessful(),
              "packages": {n: importlib.metadata.version(n) for n in ("langgraph", "langchain-core", "langchain-deepseek")},
              "tests": result.records}
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Test report: {directory / 'report.json'}")
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
