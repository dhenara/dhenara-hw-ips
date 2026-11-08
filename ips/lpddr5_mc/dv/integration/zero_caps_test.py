"""Synthetic observations qualify zero-cap accounting, not controller RTL."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from system.host import Violation
from zero_caps import check, qualify


def observations(zero, mode):
    limits = [64, 64]
    limits[zero] = 0
    events = [
        {
            "kind": "header",
            "version": 3,
            "channels": 2,
            "mode": mode,
            "window": 512,
            "zero_direction": zero,
            "read_cap": limits[0],
            "write_cap": limits[1],
            "granule_bytes": 64,
        }
    ]
    admitted = []
    for direction in range(2):
        for channel in range(2):
            for port in range(2):
                for qos in range(4):
                    event = {
                        "kind": "admit",
                        "ck": len(admitted),
                        "write": direction,
                        "channel": channel,
                        "port": port,
                        "qos": qos,
                        "bytes": 64,
                        "tag": len(admitted),
                        "generation": 1,
                    }
                    admitted.append(event)
    events += admitted
    issued = [e for e in admitted if e["write"] != zero]
    for index in range(8):
        for channel in range(2):
            event = issued[channel * 8 + index]
            events.append({**event, "kind": "issue", "ck": (index + 1) * 512})
    events += [
        {"kind": "hold", "ck": 4097},
        {"kind": "fault", "ck": 6145, "ps": 7_681_250},
        {"kind": "end", "ck": 6245},
    ]
    failed, payload = [0, 0], [1024, 1024]
    failed[zero], payload[zero] = 16, 0
    physical = {
        "fault_ps": 7_681_250,
        "failed_requests_by_direction": failed,
        "physical_payload_bytes_by_direction": payload,
        "read_bytes": payload[0],
        "write_bytes": payload[1],
    }
    return events, physical


class ZeroCapReplayTests(unittest.TestCase):
    def test_strict_and_borrowing_both_disabled_directions_and_active_mutations(self):
        for zero in (0, 1):
            for mode in (1, 2):
                with self.subTest(zero=zero, mode=mode):
                    with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
                        path = Path(temporary) / "observations.jsonl"
                        events, physical = observations(zero, mode)
                        path.write_text("".join(json.dumps(e) + "\n" for e in events))
                        result = qualify(path, Path(temporary) / "mutant.jsonl", physical)
                        self.assertEqual(result["held_requests"], 16)
                        self.assertEqual(len(result["negative_cases"]), 5)

    def test_settlement_and_physical_effects_cannot_be_inferred_from_no_grants(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            path = Path(temporary) / "observations.jsonl"
            events, physical = observations(0, 2)
            path.write_text("".join(json.dumps(e) + "\n" for e in events))
            for field, wrong, marker in (
                ("failed_requests_by_direction", [15, 0], "incomplete fault settlement"),
                ("physical_payload_bytes_by_direction", [64, 1024], "physical side effect"),
                ("fault_ps", 7_681_251, "fault witness disagreement"),
            ):
                with self.subTest(field=field):
                    with self.assertRaisesRegex(Violation, marker):
                        check(path, {**physical, field: wrong})


if __name__ == "__main__":
    unittest.main()
