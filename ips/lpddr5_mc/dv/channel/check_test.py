"""Source provenance must survive relocation beneath excluded directory names."""

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from check import IP, source_manifest


class SourceManifestTests(unittest.TestCase):
    def test_actual_source_manifest_matches_under_evidence_ancestor(self) -> None:
        expected = source_manifest()
        self.assertTrue(expected)
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            ip = Path(temporary) / "evidence/checkout/ips/lpddr5_mc"
            for entry in expected:
                relative = entry["path"].removeprefix("ips/lpddr5_mc/")
                destination = ip / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(IP / relative, destination)
            self.assertEqual(source_manifest(ip), expected)

    def test_relocated_checkout_keeps_source_and_excludes_only_owned_outputs(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            root = Path(temporary)
            manifests = []
            for ancestor in ("ordinary", "evidence", "__pycache__"):
                ip = root / ancestor / "checkout/ips/lpddr5_mc"
                for name in (
                    "rtl/channel/controller.sv",
                    "rtl/channel/files.f",
                    "dv/contracts/model.py",
                    "dv/channel/check.py",
                    "config/contracts/profile.json",
                    "constraints/channel/clock.sdc",
                    "dv/channel/evidence/captured.json",
                    "dv/channel/__pycache__/cached.py",
                    "dv/channel/readme.md",
                ):
                    path = ip / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(name + "\n")
                manifests.append(source_manifest(ip))
            self.assertEqual(manifests[0], manifests[1])
            self.assertEqual(manifests[0], manifests[2])
            self.assertEqual(len(manifests[0]), 6)
            for entry in manifests[0]:
                relative = entry["path"].removeprefix("ips/lpddr5_mc/")
                self.assertEqual(entry["sha256"], hashlib.sha256((relative + "\n").encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
