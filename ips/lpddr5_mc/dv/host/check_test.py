"""Check aggregate failure/evidence handling without running HDL regressions."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import check as owning_check


class AggregateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"], prefix="lpddr5-host-check-test-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_command(self, script: str, *, timeout: float = 5, report: Path | None = None) -> dict:
        phase = owning_check.Phase("fixture", [sys.executable, "-c", script], timeout, report)
        return owning_check.run_phase(phase, self.root / "phase.log", dict(os.environ))

    def test_original_failure_exit_and_output_survive(self) -> None:
        record = self.run_command("import sys; print('fixture error', flush=True); sys.exit(7)")
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["exit_code"], 7)
        self.assertEqual(owning_check.exit_status(record), 7)
        self.assertIn("fixture error", (self.root / "phase.log").read_text())

    def test_success_requires_retained_valid_report(self) -> None:
        report = self.root / "child.json"
        for contents in (
            None,
            "not JSON",
            '{"result":"failed"}',
            '{"result":"passed","commands":[{"exit_code":7}]}',
            "{}",
        ):
            with self.subTest(contents=contents):
                if contents is not None:
                    report.write_text(contents)
                record = self.run_command("pass", report=report)
                self.assertEqual(record["exit_code"], 0)
                self.assertEqual(record["status"], "failed")
                self.assertIn("evidence_error", record)
                self.assertEqual(owning_check.exit_status(record), 1)
        report.write_text('{"result":"passed","commands":[{"exit_code":0}]}')
        self.assertEqual(self.run_command("pass", report=report)["status"], "passed")

    def test_timeout_retains_signal_and_distinct_timeout_status(self) -> None:
        record = self.run_command("import time; time.sleep(30)", timeout=0.05)
        self.assertTrue(record["timed_out"])
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["exit_code"], -9)
        self.assertEqual(owning_check.exit_status(record), 124)

    def test_plan_retains_all_owning_gates_and_root_boundaries(self) -> None:
        plan = owning_check.phases(self.root / "summary.json", "fixture", self.root)
        self.assertEqual(
            [phase.name for phase in plan],
            [
                "runner",
                "python-lint",
                "python-format",
                "foundation",
                "cdc",
                "apb",
                "adapters",
                "core-64-16-strict",
                "core-128-16-strict",
                "core-256-64-strict",
                "core-128-16-early",
                "axi",
                "assembly",
                "host-lint-minimum",
                "host-lint-default",
                "host-lint-maximum",
            ],
        )
        for phase in plan:
            self.assertGreater(phase.timeout_seconds, 0)
        minimum, default, maximum = plan[-3:]
        self.assertIn("-GID_BITS=4", minimum.argv)
        self.assertFalse(any(arg.startswith("-G") for arg in default.argv))
        self.assertIn("-GDATA_BITS=256", maximum.argv)
        for phase in (minimum, default, maximum):
            self.assertIn("--assert", phase.argv)
            self.assertIn("-Wall", phase.argv)
            self.assertNotIn("-Wno-fatal", phase.argv)

    def test_aggregate_stops_after_failure_and_retains_source_manifest(self) -> None:
        report = self.root / "dv/host/out/summary.json"
        snapshot = {"commit": "fixture", "source_dirty": False, "source_files": {"fixture.sv": {"sha256": "abcd"}}}
        plan = [
            owning_check.Phase("fail", [sys.executable, "-c", "raise SystemExit(7)"], 5),
            owning_check.Phase("unreached", [sys.executable, "-c", "raise SystemExit(99)"], 5),
        ]
        with (
            mock.patch.object(owning_check, "IP", self.root),
            mock.patch.object(owning_check, "snapshot", return_value=snapshot),
            mock.patch.object(owning_check, "phases", return_value=plan),
            mock.patch.object(sys, "argv", ["check.py", "--report", str(report)]),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as outcome,
        ):
            owning_check.main()
        self.assertEqual(outcome.exception.code, 7)
        result = json.loads(report.read_text())
        self.assertEqual(result["result"], "failed")
        self.assertEqual(result["phases"][1]["status"], "not_run")
        self.assertEqual(result["source_before"], snapshot)
        self.assertFalse(result["source_changed_during_run"])

    def test_source_change_invalidates_otherwise_passing_phases(self) -> None:
        report = self.root / "dv/host/out/summary.json"
        before = {"commit": "fixture", "source_dirty": False, "source_files": {"fixture.sv": {"sha256": "before"}}}
        after = {"commit": "fixture", "source_dirty": False, "source_files": {"fixture.sv": {"sha256": "after"}}}
        with (
            mock.patch.object(owning_check, "IP", self.root),
            mock.patch.object(owning_check, "snapshot", side_effect=[before, after]),
            mock.patch.object(owning_check, "phases", return_value=[]),
            mock.patch.object(sys, "argv", ["check.py", "--report", str(report)]),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as outcome,
        ):
            owning_check.main()
        self.assertEqual(outcome.exception.code, 1)
        result = json.loads(report.read_text())
        self.assertEqual(result["result"], "failed")
        self.assertTrue(result["source_changed_during_run"])
        self.assertEqual(result["changed_paths"], ["fixture.sv"])


if __name__ == "__main__":
    unittest.main()
