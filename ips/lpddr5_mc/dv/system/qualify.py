"""Retain every executed environment test and its actual disposition."""

import argparse
import json
import time
import unittest
from pathlib import Path

from system.coverage import qualify


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.observations = []
        self.starts = {}

    def startTest(self, test):
        self.starts[test.id()] = time.monotonic()
        super().startTest(test)

    def addSuccess(self, test):
        self.observations.append(
            {"test": test.id(), "verdict": "passed", "elapsed_seconds": time.monotonic() - self.starts[test.id()]}
        )
        super().addSuccess(test)

    def addFailure(self, test, err):
        self.observations.append({"test": test.id(), "verdict": "failed", "error": str(err[1])})
        super().addFailure(test, err)

    def addError(self, test, err):
        self.observations.append({"test": test.id(), "verdict": "error", "error": str(err[1])})
        super().addError(test, err)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args()
    if args.report.exists():
        p.error("preserve earlier evidence; choose a new report path")
    tests = unittest.defaultTestLoader.discover(str(Path(__file__).with_name("tests")))
    result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(tests)
    report = {
        "kind": "environment_fixture_qualification",
        "seed": 2095,
        "controller_qualified": False,
        "tests_run": result.testsRun,
        "passed": result.wasSuccessful(),
        "tests": result.observations,
        "failures": [str(test) for test, _ in result.failures],
        "errors": [str(test) for test, _ in result.errors],
        "skipped": result.skipped,
    }
    try:
        report["coverage"] = qualify(result.observations)
    except ValueError as error:
        report["coverage_error"] = str(error)
        report["passed"] = False
    with args.report.open("x") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    if not report["passed"] or result.testsRun == 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
