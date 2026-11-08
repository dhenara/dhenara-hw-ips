"""Fault admission remains explicit and cannot replace host settlement."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from connected import check
from system.host import Violation


class ConnectedFaultTests(unittest.TestCase):
    def test_selected_fault_settles_full_read_and_write_without_physical_credit(self):
        events = [
            {"kind": "header", "version": 1, "ports": 1, "channels": 1, "bus_bytes": 16},
            {"kind": "aw", "ps": 10, "port": 0, "id": 1, "address": 0, "length": 0, "size": 4, "burst": 1, "lock": 0},
            {"kind": "ar", "ps": 11, "port": 0, "id": 2, "address": 64, "length": 1, "size": 4, "burst": 1, "lock": 0},
            {"kind": "w", "ps": 12, "port": 0, "data": 0, "strobe": 65535, "last": 1},
            {"kind": "fault", "ps": 20},
            {"kind": "b", "ps": 30, "port": 0, "id": 1, "response": 2, "ready": 1},
            {"kind": "r", "ps": 31, "port": 0, "id": 2, "data": 0, "response": 2, "last": 0, "ready": 1},
            {"kind": "r", "ps": 32, "port": 0, "id": 2, "data": 0, "response": 2, "last": 1, "ready": 1},
            {"kind": "end", "ps": 40},
        ]
        profile = json.loads((Path(__file__).resolve().parents[2] / "config/contracts/profiles.json").read_text())[
            "profiles"
        ][0]
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            path = Path(temporary) / "raw.jsonl"

            def replay(altered, **selection):
                path.write_text("".join(json.dumps(e) + "\n" for e in altered))
                return check(path, profile, ranks=1, depth=16, **selection)

            result = replay(events, fault_ps=20)
            self.assertEqual(result["failed_requests_by_direction"], [1, 1])
            self.assertEqual(result["physical_payload_bytes_by_direction"], [0, 0])
            self.assertEqual([result["read_bytes"], result["write_bytes"]], [0, 0])
            for altered, selection, marker in (
                (events, {}, "unexpected fault witness"),
                (events, {"fault_ps": 21}, "unexpected fault witness"),
                (events[:5] + [events[4]] + events[5:], {"fault_ps": 20}, "unexpected fault witness"),
                (events[:4] + events[5:], {"fault_ps": 20}, "before final physical pin"),
                (events[:7] + events[8:], {"fault_ps": 20}, "unsettled host transaction"),
                (events[:5] + [{**events[5], "response": 0}] + events[6:], {"fault_ps": 20}, "response code"),
                (events[:-1], {"fault_ps": 20}, "truncated raw observation"),
            ):
                with self.subTest(marker=marker, selection=selection):
                    with self.assertRaisesRegex(Violation, marker):
                        replay(altered, **selection)


if __name__ == "__main__":
    unittest.main()
