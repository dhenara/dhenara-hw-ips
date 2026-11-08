"""Synthetic traces qualify the accountant; actual RTL evidence is separate."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from contracts.commands import access, activate, maintenance
from fairness import check, qualify
from system.dram import DramModel
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
    def test_profile_four_write_row_transition_cannot_meet_g64(self):
        profile = json.loads((Path(__file__).resolve().parents[2] / "config/contracts/profiles.json").read_text())[
            "profiles"
        ][4]
        profile = profile | {"ranks": 1}
        model = DramModel(profile)
        model.command(0, 1, activate(0, 0)[0])
        model.command(1, 1, activate(0, 0)[1])
        model.command(15, 1, access(True, 32, 0, 0))
        # WL9 + split span8 + one CK + tWR28 = 46 before PRE.
        # tRP15 and tRCD15 then make the minimum WR-to-WR gap 76.
        with self.assertRaisesRegex(Violation, "PRE/REF recovery"):
            copy.deepcopy(model).command(60, 1, maintenance("PRE", 0, False))
        model.command(61, 1, maintenance("PRE", 0, False))
        model.command(76, 1, activate(1, 0)[0])
        model.command(77, 1, activate(1, 0)[1])
        with self.assertRaisesRegex(Violation, "access without ready open row"):
            copy.deepcopy(model).command(90, 1, access(True, 32, 0, 0))
        self.assertIsNotNone(model.command(91, 1, access(True, 32, 0, 0)))

    def test_explicit_mixed_gap_and_legacy_failure(self):
        """New mixed costs never reinterpret the original G64 counterexample."""
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            path = Path(temporary) / "observations.jsonl"
            for delay in (80, 129):
                events, physical = observations(2)
                first = {e["channel"]: e["admit"] for e in events[1:3]}
                delayed = [events[0]]
                for ck in range(delay):
                    for channel in range(2):
                        delayed.append(
                            {
                                **events[channel + 1],
                                "ck": ck,
                                "admit": first[channel] if ck == 0 else None,
                                "issue": None,
                            }
                        )
                for event in events[1:]:
                    delayed.append({**event, "ck": event["ck"] + delay})
                    if event.get("ck") == 0:
                        delayed[-1]["admit"] = None
                path.write_text("".join(json.dumps(e) + "\n" for e in delayed))
                with self.assertRaisesRegex(Violation, "issue gap G including trace tail"):
                    check(path, physical)
                delayed[0].update(version=4, rows=0, gap=128)
                path.write_text("".join(json.dumps(e) + "\n" for e in delayed))
                if delay == 80:
                    result = check(path, physical)
                    self.assertEqual(result["maximum_issue_gap_ck"], 128)
                    self.assertEqual(result["measured_issue_gap_ck"], [81, 81])
                    self.assertEqual(result["conditional_bound_ck"], 8456)
                else:
                    with self.assertRaisesRegex(Violation, "issue gap G including trace tail"):
                        check(path, physical)

    def test_low_age_churn_still_requires_every_class(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            path = Path(temporary) / "observations.jsonl"
            events, physical = observations(0)
            events[0].update(version=4, rows=1, age=1)
            path.write_text("".join(json.dumps(e) + "\n" for e in events))
            with self.assertRaisesRegex(Violation, "missing row-churn aged source/class grants"):
                check(path, physical)
            # Single-direction reference G64 cannot be widened by the header.
            events[0].update(gap=128)
            path.write_text("".join(json.dumps(e) + "\n" for e in events))
            with self.assertRaisesRegex(Violation, "fairness configuration"):
                check(path, physical)

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
            events[0].update(version=4, age=1)
            path.write_text("".join(json.dumps(e) + "\n" for e in events))
            result = check(path, physical)
            self.assertEqual(result["conditional_bound_ck"], 4112)
            self.assertEqual(len(result["aged_grants_by_channel_direction_port_class"]), 16)

            # Version5 gives only same-bank write churn its separate G96
            # workload ceiling. Keep all original G64 failures observable.
            shifted = [{**events[0], "version": 5, "mode": 1, "gap": 96}]
            for event in events[1:]:
                if event["kind"] == "cycle" and event["ck"] == 63 and event["channel"] == 0:
                    for ck in range(63, 83):
                        for channel in range(2):
                            shifted.append({**event, "ck": ck, "channel": channel, "admit": None, "issue": None})
                altered = {**event, "ck": event["ck"] + (20 if event["ck"] >= 63 else 0)}
                for field in ("admit", "issue"):
                    if altered.get(field) is not None:
                        altered[field] = {**altered[field], "write": 1}
                shifted.append(altered)
            physical = {"read_bytes": 0, "write_bytes": 16384}
            path.write_text("".join(json.dumps(e) + "\n" for e in shifted))
            result = qualify(path, Path(temporary) / "mutant.jsonl", physical)
            self.assertEqual(result["conditional_bound_ck"], 6160)
            self.assertEqual(result["measured_issue_gap_ck"], [84, 84])
            for header, marker in (
                ({"version": 4, "gap": 64}, "issue gap G including trace tail"),
                ({"version": 4, "gap": 96}, "fairness configuration"),
                ({"mode": 0}, "fairness configuration"),
                ({"rows": 0, "age": 32}, "fairness configuration"),
            ):
                shifted[0] = shifted[0] | header
                path.write_text("".join(json.dumps(e) + "\n" for e in shifted))
                with self.assertRaisesRegex(Violation, marker):
                    check(path, physical)
                shifted[0] = {**events[0], "version": 5, "mode": 1, "gap": 96}


if __name__ == "__main__":
    unittest.main()
