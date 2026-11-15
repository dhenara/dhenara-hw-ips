"""Admit rank-matched ordinary power runs without widening other families."""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_SPEC = importlib.util.spec_from_file_location("power_integration_runner", Path(__file__).with_name("check.py"))
assert _SPEC and _SPEC.loader
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


class PlanningStopped(Exception):
    """Stop before launching any external tooling."""


class PowerRunnerTests(unittest.TestCase):
    def test_both_rank_counts_and_policies_plan_matching_physical_runs(self):
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=runner.REPO, text=True)

        def observation(argv, **kwargs):
            if argv[:3] == ["git", "status", "--porcelain"]:
                return "" if kwargs.get("text") else b""
            if argv == ["git", "rev-parse", "HEAD"]:
                return commit if kwargs.get("text") else commit.encode()
            raise AssertionError(f"Unexpected source observation: {argv}")

        for ranks in (1, 2):
            for policy in (1, 2):
                with self.subTest(ranks=ranks, policy=policy):
                    with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
                        report = Path(temporary) / "proof/report.json"
                        argv = [
                            "check.py",
                            "--family",
                            "controller",
                            "--ranks",
                            str(ranks),
                            "--power-policy",
                            str(policy),
                            "--report",
                            str(report),
                            "--build-name",
                            "plan",
                        ]
                        with (
                            patch.object(sys, "argv", argv),
                            patch.object(sys, "pycache_prefix", sys.pycache_prefix),
                            patch.dict(os.environ, DHENARA_AGENT_TOOL_STORAGE_ROOT=temporary),
                            patch.object(subprocess, "check_output", side_effect=observation),
                            patch.object(subprocess, "run", side_effect=PlanningStopped),
                            self.assertRaises(PlanningStopped),
                        ):
                            runner.main()
                        planned = json.loads(report.read_text())
                        compiler, runtime = planned["planned_commands"][1:]
                        self.assertIn(f"-GRANKS={ranks}", compiler)
                        self.assertIn("-GPROFILE_NUMBER=0", compiler)
                        self.assertIn(f"+power_policy={policy}", runtime)
                        self.assertEqual(planned["configuration"]["RANKS"], ranks)
                        self.assertEqual(planned["commands"], [])
                        names = {Path(p).name for p in planned["reserved_observations"]}
                        self.assertEqual(names, {"observations.jsonl", "recovery-observations.jsonl"})

    def test_two_rank_variants_reject_before_evidence_creation(self):
        for extra in (
            ["--ports", "1"],
            ["--channels", "1"],
            ["--data-bits", "256"],
            ["--depth", "32"],
            ["--profile-number", "24"],
            ["--service-div", "4"],
            ["--inline-lifecycle"],
            ["--runtime-reset"],
            ["--wrapper-power-policy", "2"],
            ["--wrapper-fsp-case", "1"],
            ["--integrity-mode", "1"],
            ["--benchmark-direction", "read"],
            ["--axi-overlap"],
        ):
            with self.subTest(extra=extra):
                self.rejected(["--power-policy", "2", *extra])

    def test_normal_two_rank_controller_stays_restricted(self):
        self.rejected([])

    def rejected(self, extra):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            report = Path(temporary) / "proof/report.json"
            argv = [
                "check.py",
                "--family",
                "controller",
                "--ranks",
                "2",
                "--report",
                str(report),
                *extra,
            ]
            with (
                patch.object(sys, "argv", argv),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as caught:
                    runner.main()
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse(report.parent.exists())


if __name__ == "__main__":
    unittest.main()
