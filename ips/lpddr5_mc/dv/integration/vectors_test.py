"""Regenerated fixture inputs must not invalidate compatible simulator builds."""

import os
import tempfile
import unittest
from pathlib import Path

from vectors import generate


class FixtureGenerationTests(unittest.TestCase):
    def test_unchanged_inputs_keep_their_contents_and_timestamps(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as directory:
            destination = Path(directory)
            generate(destination)
            original = {p.name: p.read_bytes() for p in destination.iterdir()}
            self.assertEqual(len(original), 4)
            for name in original:
                os.utime(destination / name, ns=(1_000_000_000, 1_000_000_000))
            generate(destination)
            for name, content in original.items():
                with self.subTest(name=name):
                    self.assertEqual((destination / name).read_bytes(), content)
                    self.assertEqual((destination / name).stat().st_mtime_ns, 1_000_000_000)

    def test_modified_or_missing_inputs_are_regenerated(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as directory:
            destination = Path(directory)
            generate(destination)
            original = {p.name: p.read_bytes() for p in destination.iterdir()}
            for name, content in original.items():
                with self.subTest(name=name):
                    path = destination / name
                    path.write_text("invalid fixture\n")
                    generate(destination)
                    self.assertEqual(path.read_bytes(), content)
                    path.unlink()
                    generate(destination)
                    self.assertEqual(path.read_bytes(), content)
