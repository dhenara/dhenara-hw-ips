"""The owning runner must isolate and precisely select each boundary case."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check


class PlanningStopped(Exception):
    """End planning before any compiler, simulator or test process starts."""


class BoundaryRunnerTests(unittest.TestCase):
    def test_all_boundary_cases_plan_exact_precision_and_isolated_streams(self):
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=check.REPO, text=True)

        def source_observation(argv, **kwargs):
            if argv[:3] == ["git", "status", "--porcelain"]:
                return "" if kwargs.get("text") else b""
            if argv == ["git", "rev-parse", "HEAD"]:
                return commit if kwargs.get("text") else commit.encode()
            raise AssertionError(f"Unexpected source observation: {argv}")

        for case in range(12):
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
                    root = Path(temporary)
                    report = root / "proof/report.json"
                    argv = [
                        "check.py",
                        "--family",
                        "controller",
                        "--profile-number",
                        "8",
                        "--ecc-boundary-case",
                        str(case),
                        "--report",
                        str(report),
                        "--build-name",
                        "plan",
                    ]
                    with (
                        patch.object(sys, "argv", argv),
                        patch.object(sys, "pycache_prefix", sys.pycache_prefix),
                        patch.dict(os.environ, DHENARA_AGENT_TOOL_STORAGE_ROOT=temporary),
                        patch.object(subprocess, "check_output", side_effect=source_observation),
                        patch.object(subprocess, "run", side_effect=PlanningStopped),
                        self.assertRaises(PlanningStopped),
                    ):
                        check.main()
                    planned = json.loads(report.read_text())
                    compiler, runtime = planned["planned_commands"][1:]
                    self.assertEqual(compiler[compiler.index("--timescale-override") + 1], "1ns/1fs")
                    self.assertIn("-GECC_BOUNDARY=1'b1", compiler)
                    self.assertIn("-GINLINE_LIFECYCLE=1'b1", compiler)
                    self.assertIn("-GPROFILE_NUMBER=8", compiler)
                    self.assertIn(f"+ecc_boundary_kind={case // 6}", runtime)
                    self.assertIn(f"+ecc_boundary_delta={case // 2 % 3 - 1}", runtime)
                    self.assertIn(f"+ecc_boundary_posted={case % 2}", runtime)
                    names = {Path(p).name for p in planned["reserved_observations"]}
                    self.assertTrue(
                        {"observations.jsonl", "continuous-observations.jsonl", "boundary-observations.jsonl"} <= names
                    )
                    self.assertEqual(planned["generated_inputs"], planned["generated_inputs_after"])
                    self.assertEqual(len(planned["generated_inputs"]), 4)
                    self.assertEqual(planned["commands"], [])

    def test_incompatible_selections_fail_before_creating_evidence(self):
        for extra in (
            ["--profile-number", "0"],
            ["--ranks", "2"],
            ["--family", "datapath"],
            ["--wrapper-fsp-case", "1"],
            ["--runtime-reset-case", "1"],
            ["--power-policy", "2"],
            ["--ecc-composition"],
            ["--axi-stress-seed", "2095"],
        ):
            with self.subTest(extra=extra):
                with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
                    report = Path(temporary) / "report.json"
                    argv = [
                        "check.py",
                        "--family",
                        "controller",
                        "--profile-number",
                        "8",
                        "--ecc-boundary-case",
                        "0",
                        "--report",
                        str(report),
                        *extra,
                    ]
                    with patch.object(sys, "argv", argv), contextlib.redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as caught:
                            check.main()
                    self.assertEqual(caught.exception.code, 2)
                    self.assertFalse(report.exists())

    def test_boundary_trace_cannot_be_overwritten_under_another_report_name(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            root = Path(temporary)
            trace = root / "boundary-observations.jsonl"
            trace.write_text("retained boundary evidence\n")
            with self.assertRaises(FileExistsError):
                check.reserve_observations(root / "other.json", "controller", False, False, ecc_boundary=True)
            self.assertEqual(trace.read_text(), "retained boundary evidence\n")
            self.assertFalse((root / "observations.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
