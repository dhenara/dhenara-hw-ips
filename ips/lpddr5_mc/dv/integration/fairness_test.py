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
        {
            "kind": "header",
            "version": 2,
            "profile": 4,
            "mode": mode,
            "divisor": 2,
            "depth": 64,
            "age": 32,
            "tick": 8,
            "gap": 64,
        }
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
            path.write_text(
                "".join(json.dumps({**e, "profile": 0} if e["kind"] == "header" else e) + "\n" for e in events)
            )
            with self.assertRaisesRegex(Violation, "fairness configuration"):
                check(path, physical)
            events[max(i for i, event in enumerate(events) if event.get("issue") is not None)]["issue"] = None
            path.write_text("".join(json.dumps(e) + "\n" for e in events))
            with self.assertRaisesRegex(Violation, "unserved combined fairness tail"):
                check(path, physical)

    def test_row_churn_requires_aged_grants_in_every_group(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            path = Path(temporary) / "observations.jsonl"
            events, physical = observations(0)
            events[0].update(version=3, rows=1)
            path.write_text("".join(json.dumps(e) + "\n" for e in events))
            with self.assertRaisesRegex(Violation, "missing row-churn aged source/class grants"):
                check(path, physical)

            # Fill a bounded queue, then grant every eight eligible CK. The
            # tail contains aged grants from every class without exceeding G.
            events = [events[0]]
            for ck in range(1080):
                issued = (ck - 63) // 8 if ck >= 63 and (ck - 63) % 8 == 0 else None
                admitted = ck if ck < 64 else issued + 63 if issued is not None and issued + 63 < 128 else None

                def item(n):
                    return (
                        None
                        if n is None
                        else {
                            "tag": n,
                            "generation": 1,
                            "write": 0,
                            "port": n % 2,
                            "qos": n // 2 % 4,
                            "bytes": 64,
                            "address": n * 64,
                        }
                    )

                for channel in range(2):
                    events.append(
                        {
                            "kind": "cycle",
                            "ck": ck,
                            "channel": channel,
                            "admit": item(admitted),
                            "issue": item(issued),
                            "pair": 0,
                            "maintenance": 0,
                            "ownership": 0,
                            "response": 0,
                            "pause": 0,
                        }
                    )
            events.append({"kind": "end", "ck": 1080})
            path.write_text("".join(json.dumps(e) + "\n" for e in events))
            result = check(path, physical)
            self.assertEqual(len(result["aged_grants_by_channel_direction_port_class"]), 16)
            self.assertEqual(result["maximum_live_by_channel"], [64, 64])


if __name__ == "__main__":
    unittest.main()
