"""Exercise the controller runner's public result contract using real subprocesses."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import check_controller


class StepEvidenceTests(unittest.TestCase):
    def test_all_selected_steps_are_accounted_for_after_a_failure(self) -> None:
        cases = [
            ("failed", "import json,sys; json.dump({'status':'failed'}, open(sys.argv[1],'w')); sys.exit(1)", 5),
            ("timed_out", "import time; time.sleep(10)", 0.2),
            ("infrastructure_error", "print('no result produced')", 5),
        ]
        for expected, script, limit in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as scratch:
                evidence = Path(scratch) / "attempt"
                child_report = evidence / "original.json"
                steps = [
                    {
                        "name": name,
                        "argv": [sys.executable, "-c", script, str(child_report)],
                        "report": str(child_report),
                        "timeout_seconds": limit,
                    }
                    for name in ("first", "second")
                ]
                with (
                    mock.patch.object(check_controller, "plan", return_value=steps),
                    mock.patch.object(check_controller, "source", return_value={"dirty": False}),
                    mock.patch.dict("os.environ", DHENARA_AGENT_TOOL_STORAGE_ROOT=scratch),
                    mock.patch.object(
                        sys, "argv", ["check_controller", "--evidence", str(evidence), "--build-name", "t"]
                    ),
                    self.assertRaises(SystemExit) as error,
                ):
                    check_controller.main()
                self.assertNotEqual(error.exception.code, 0)
                aggregate = json.loads((evidence / "report.json").read_text())
                self.assertEqual(aggregate["status"], "failed")
                self.assertEqual([r["status"] for r in aggregate["steps"]], [expected, "not_run"])
                for record in aggregate["steps"]:
                    path = evidence / record["name"] / "step.json"
                    self.assertEqual(json.loads(path.read_text()), record)
                self.assertFalse((evidence / "second.log").exists())

    def test_source_change_invalidates_the_step_and_preserves_the_original_report(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            evidence = Path(scratch) / "attempt"
            child = evidence / "child.json"
            steps = [
                {
                    "name": "one",
                    "argv": [
                        sys.executable,
                        "-c",
                        'from pathlib import Path; import sys; Path(sys.argv[1]).write_text(\'{"status":"passed"}\')',
                        str(child),
                    ],
                    "report": str(child),
                    "timeout_seconds": 5,
                }
            ]
            with (
                mock.patch.object(check_controller, "plan", return_value=steps),
                mock.patch.object(check_controller, "source", side_effect=[{"dirty": False}, {"dirty": True}]),
                mock.patch.dict("os.environ", DHENARA_AGENT_TOOL_STORAGE_ROOT=scratch),
                mock.patch.object(sys, "argv", ["check_controller", "--evidence", str(evidence), "--build-name", "t"]),
                self.assertRaises(SystemExit) as error,
            ):
                check_controller.main()
            self.assertNotEqual(error.exception.code, 0)
            aggregate = json.loads((evidence / "report.json").read_text())
            self.assertFalse(aggregate["source_unchanged"])
            self.assertEqual(aggregate["steps"][0]["status"], "infrastructure_error")
            self.assertEqual(json.loads((evidence / "one/step.json").read_text()), aggregate["steps"][0])
            self.assertEqual(json.loads(child.read_text())["status"], "passed")

    def test_sigterm_stops_simulator_descendants_and_publishes_the_step_outcome(self) -> None:
        # The child launcher exits on TERM. Its simulator ignores TERM and would
        # outlive both the launcher and the aggregate unless the group is stopped.
        launcher = """
import json, os, signal, subprocess, sys
from pathlib import Path
child = subprocess.Popen(
    [sys.executable, '-c', 'import os,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);'
     'print(os.getpid(),flush=True);time.sleep(20)'],
    stdout=subprocess.PIPE, text=True,
)
assert child.stdout is not None
Path(sys.argv[1]).write_text(json.dumps([os.getpid(), int(child.stdout.readline())]))
os.kill(os.getppid(), signal.SIGTERM)
child.wait()
"""
        driver = """
import json,sys
from pathlib import Path
from unittest import mock
sys.path.insert(0, sys.argv[1])
import check_controller
evidence, pid_file, launcher = sys.argv[2:]
steps = [
    dict(name=name, argv=[sys.executable, '-c', launcher, pid_file], report=evidence+'/child.json', timeout_seconds=10)
    for name in ['active', 'next']
]
sys.argv = ['check_controller', '--evidence', evidence, '--build-name', 'signal']
with (
    mock.patch.object(check_controller, 'plan', return_value=steps),
    mock.patch.object(check_controller, 'source', return_value={'dirty': False}),
):
    check_controller.main()
"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence, pids = root / "attempt", root / "processes.json"
            try:
                parent = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        driver,
                        str(Path(check_controller.__file__).parent),
                        str(evidence),
                        str(pids),
                        launcher,
                    ],
                    env={**os.environ, "DHENARA_AGENT_TOOL_STORAGE_ROOT": temp},
                    timeout=12,
                    check=False,
                )
                self.assertEqual(parent.returncode, 128 + signal.SIGTERM)
                report = json.loads((evidence / "report.json").read_text())
                self.assertEqual(report["status"], "failed")
                self.assertIn("SIGTERM", report["error"])
                step = json.loads((evidence / "active/step.json").read_text())
                self.assertEqual(step["status"], "infrastructure_error")
                self.assertEqual(step["exit_code"], 128 + signal.SIGTERM)
                self.assertEqual(json.loads((evidence / "next/step.json").read_text())["status"], "not_run")
                for pid in json.loads(pids.read_text()):
                    path = Path(f"/proc/{pid}/stat")
                    if path.exists():
                        self.assertIn(path.read_text().split()[2], ("Z", "X"), f"Process {pid} survived SIGTERM")
            finally:
                if pids.is_file():
                    group, _ = json.loads(pids.read_text())
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
