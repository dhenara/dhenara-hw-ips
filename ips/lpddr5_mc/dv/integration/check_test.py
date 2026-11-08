"""An integration run must never overwrite another run's raw observation files."""

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from check import reserve_observations


class EvidenceIsolationTests(unittest.TestCase):
    def test_report_names_do_not_isolate_shared_traces(self):
        for family, benchmark, caps in (
            ("datapath", False, True),
            ("controller", False, False),
            ("controller", True, False),
        ):
            with self.subTest(family=family, benchmark=benchmark):
                with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
                    root = Path(temporary)
                    paths = reserve_observations(root / "first.json", family, benchmark, caps)
                    for path in paths:
                        path.write_bytes(b"original retained trace\n")
                    with self.assertRaisesRegex(FileExistsError, "fresh report directory"):
                        reserve_observations(root / "second.json", family, benchmark, caps)
                    self.assertTrue(all(p.read_bytes() == b"original retained trace\n" for p in paths))

    def test_existing_unreserved_trace_is_preserved(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            root = Path(temporary)
            trace = root / "cap-observations.jsonl"
            trace.write_text("earlier evidence\n")
            with self.assertRaises(FileExistsError):
                reserve_observations(root / "report.json", "datapath", False, True)
            self.assertEqual(trace.read_text(), "earlier evidence\n")
            self.assertFalse((root / "observations.jsonl").exists())

    def test_fairness_trace_is_reserved_before_compilation(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            root = Path(temporary)
            paths = reserve_observations(root / "report.json", "datapath", False, False, True)
            self.assertIn(root / "fairness-observations.jsonl", paths)
            with self.assertRaisesRegex(FileExistsError, "fresh report directory"):
                reserve_observations(root / "other.json", "datapath", False, False, True)

    def test_concurrent_writers_have_one_owner(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            root = Path(temporary)

            def attempt(index):
                try:
                    return reserve_observations(root / f"report-{index}.json", "controller", False, False)
                except FileExistsError:
                    return None

            with ThreadPoolExecutor(max_workers=2) as pool:
                attempts = list(pool.map(attempt, range(2)))
            self.assertEqual(sum(paths is not None for paths in attempts), 1)
            self.assertTrue((root / "observations.jsonl").exists())
            self.assertTrue((root / "recovery-observations.jsonl").exists())

    def test_distinct_directories_keep_both_trace_sets(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            root = Path(temporary)
            for name in ("min", "max"):
                directory = root / name
                directory.mkdir()
                for path in reserve_observations(directory / "report.json", "datapath", False, False):
                    path.write_text(name)
            self.assertEqual((root / "min/observations.jsonl").read_text(), "min")
            self.assertEqual((root / "max/observations.jsonl").read_text(), "max")


if __name__ == "__main__":
    unittest.main()
