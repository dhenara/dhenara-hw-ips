"""A successful CLI replay must satisfy the selected full-performance gates."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import check_benchmark as owning_check
from system.host import Violation


class BenchmarkCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"], prefix="lpddr5-benchmark-cli-test-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.trace = self.root / "observations.jsonl"
        self.log = self.root / "simulation.log"
        self.trace.write_text('{"kind":"header"}\n')
        self.log.write_text("retained simulation log\n")
        self.inputs = {p: p.read_bytes() for p in (self.trace, self.log)}
        self.case = 0

    @staticmethod
    def baseline(direction):
        return {
            "direction": direction,
            "smoke_only": False,
            "threshold_passed": True,
            "steady_backlog_endpoint_passed": direction == "read",
        } | ({"write_stability": {"passed": True}} if direction == "write" else {})

    def invoke(self, baseline, *, smoke=False, replay_error=None):
        self.case += 1
        self.report = self.root / f"report-{self.case}.json"
        self.output = io.StringIO()
        argv = [
            "check_benchmark.py",
            str(self.trace),
            "--log",
            str(self.log),
            "--report",
            str(self.report),
            "--direction",
            baseline["direction"],
            "--service",
            "2",
            *(["--smoke"] if smoke else []),
        ]
        # Only expensive physical replay is replaced; the CLI and full gate run.
        with (
            mock.patch.object(owning_check, "check", return_value=baseline, side_effect=replay_error),
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(self.output),
        ):
            owning_check.main()

    def assert_failed(self, baseline, message, error=Violation):
        with self.assertRaisesRegex(error, message):
            self.invoke(baseline)
        report = json.loads(self.report.read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["baseline"], baseline)
        self.assertRegex(report["error"], message)
        self.assertEqual(report["negative_cases"], {})
        self.assertNotIn("passed:", self.output.getvalue())
        for path, original in self.inputs.items():
            self.assertEqual(path.read_bytes(), original)

    def test_full_read_and_write_reject_low_throughput(self):
        for direction in ("read", "write"):
            with self.subTest(direction=direction):
                self.assert_failed(self.baseline(direction) | {"threshold_passed": False}, "99% threshold")

    def test_full_read_rejects_growing_backlog(self):
        self.assert_failed(self.baseline("read") | {"steady_backlog_endpoint_passed": False}, "steady backlog grew")

    def test_full_read_rejects_missing_endpoint_evidence(self):
        baseline = self.baseline("read")
        del baseline["steady_backlog_endpoint_passed"]
        self.assert_failed(baseline, "steady_backlog_endpoint_passed", KeyError)

    def test_full_write_rejects_failed_or_missing_recurrence(self):
        for recurrence in ({"passed": False}, None):
            with self.subTest(recurrence=recurrence):
                baseline = self.baseline("write")
                if recurrence is None:
                    del baseline["write_stability"]
                else:
                    baseline["write_stability"] = recurrence
                self.assert_failed(baseline, "write stability failed or lacks observed evidence")

    def test_valid_full_read_and_write_pass(self):
        for direction in ("read", "write"):
            with self.subTest(direction=direction):
                baseline = self.baseline(direction)
                self.invoke(baseline)
                report = json.loads(self.report.read_text())
                self.assertEqual(report["status"], "passed")
                self.assertEqual(report["baseline"], baseline)
                self.assertNotIn("error", report)
                self.assertIn("passed:", self.output.getvalue())

    def test_smoke_bypasses_full_rate_and_stability_verdicts(self):
        for direction in ("read", "write"):
            with self.subTest(direction=direction):
                baseline = {
                    "direction": direction,
                    "smoke_only": True,
                    "threshold_passed": None,
                    "steady_backlog_endpoint_passed": None,
                }
                self.invoke(baseline, smoke=True)
                report = json.loads(self.report.read_text())
                self.assertEqual(report["status"], "passed")
                self.assertEqual(report["baseline"], baseline)
                self.assertIsNone(report["baseline"]["threshold_passed"])

    def test_physical_replay_failure_keeps_failed_report_and_inputs(self):
        with self.assertRaisesRegex(Violation, "physical replay failed"):
            self.invoke(self.baseline("read"), replay_error=Violation("physical replay failed"))
        report = json.loads(self.report.read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error"], "physical replay failed")
        self.assertNotIn("baseline", report)
        self.assertNotIn("passed:", self.output.getvalue())
        for path, original in self.inputs.items():
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
