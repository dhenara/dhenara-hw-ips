"""Synthetic traces qualify the accountant; actual RTL evidence is separate."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from fairness import check, qualify
from system.host import Violation


def observations(mode):
    events = [
        {"kind": "header", "version": 1, "mode": mode, "divisor": 2, "depth": 64, "age": 32, "tick": 8, "gap": 64}
    ]
    directions = (0, 1) if mode == 2 else (mode,)
    cycle = 0
    for direction in directions:
        for n in range(128):
            for channel in range(2):
                item = {
                    "tag": direction * 128 + n,
                    "generation": 1,
                    "write": direction,
                    "port": n % 2,
                    "qos": n // 2 % 4,
                    "bytes": 64,
                    "address": n * 64,
                }
                events.append(
                    {
                        "kind": "cycle",
                        "ck": cycle,
                        "channel": channel,
                        "admit": item,
                        "issue": item,
                        "pair": 0,
                        "maintenance": 0,
                        "ownership": 0,
                        "response": 0,
                        "pause": 0,
                    }
                )
            cycle += 1
            for channel in range(2):
                events.append(
                    {
                        "kind": "cycle",
                        "ck": cycle,
                        "channel": channel,
                        "admit": None,
                        "issue": None,
                        "pair": 0,
                        "maintenance": 0,
                        "ownership": 0,
                        "response": 0,
                        "pause": 0,
                    }
                )
            cycle += 1
    events.append({"kind": "end", "ck": cycle})
    return events, {"read_bytes": 16384 if 0 in directions else 0, "write_bytes": 16384 if 1 in directions else 0}


class CombinedFairnessTests(unittest.TestCase):
    def test_modes_and_active_mutations(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            path = Path(temporary) / "observations.jsonl"
            for mode in (0, 1, 2):
                with self.subTest(mode=mode):
                    events, physical = observations(mode)
                    path.write_text("".join(json.dumps(e) + "\n" for e in events))
                    result = qualify(path, Path(temporary) / "mutant.jsonl", physical)
                    self.assertEqual(result["measured_issue_gap_ck"], [1, 1])
                    self.assertEqual(len(result["negative_cases"]), 4)

    def test_unsettled_tail_and_wrong_payload_fail(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            path = Path(temporary) / "observations.jsonl"
            events, physical = observations(2)
            path.write_text("".join(json.dumps(e) + "\n" for e in events))
            with self.assertRaisesRegex(Violation, "physical payload"):
                check(path, {**physical, "read_bytes": 0})
            events[max(i for i, event in enumerate(events) if event.get("issue") is not None)]["issue"] = None
            path.write_text("".join(json.dumps(e) + "\n" for e in events))
            with self.assertRaisesRegex(Violation, "unserved combined fairness tail"):
                check(path, physical)


if __name__ == "__main__":
    unittest.main()
