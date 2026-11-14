"""Physical replay checks survive retirement of expired adapter windows."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from connected import check
from continuous_physical_test import fixture
from system.host import Violation


def trace_fixture():
    """Two real model transfers with source addresses and an idle final tail.

    This is an independent digital-PHY fixture, not captured RTL evidence.
    Header/source times and physical frames share picoseconds explicitly.
    """
    f = fixture()
    f.transfer(0, 1, bytes(64), 1)
    write_end = len(f.frames) - 2
    f.transfer(0, 0, bytes(64), 2)
    events = [dict(kind="header", version=1, ports=1, channels=1, bus_bytes=16)]
    events.append(dict(kind="aw", ps=10, port=0, id=1, address=0, length=3, size=4, burst=1, lock=0))
    for word in range(4):
        events.append(dict(kind="w", ps=20 + word, port=0, data=0, strobe=65535, last=int(word == 3)))
    for frame, _, _ in f.frames[1:]:
        ck = frame["tick"] - 1
        events.append(
            dict(
                kind="phy",
                ps=625 + ck * 1250,
                ck=ck,
                channel=0,
                **{k: frame[k] for k in ("cs", "ca", "wck", "write", "read", "return")},
            )
        )
    write_ps = 625 + write_end * 1250 + 1
    end_ps = events[-1]["ps"] + 1
    events.extend(
        [
            dict(kind="b", ps=write_ps, port=0, id=1, response=0, ready=1),
            dict(kind="ar", ps=write_ps + 1, port=0, id=2, address=0, length=3, size=4, burst=1, lock=0),
        ]
    )
    for word in range(4):
        events.append(dict(kind="r", ps=end_ps + word, port=0, id=2, data=0, response=0, last=int(word == 3), ready=1))
    events.append(dict(kind="end", ps=end_ps + 10))
    return f.p, events


def skew_one_slice(events, ratio):
    """Delay slice one by one CK without changing slice zero or lane rotation."""
    events = copy.deepcopy(events)
    selected = sum((1 << (32 + 2 * lane + 1)) | (0xFF00FF00 << (40 + 32 * lane)) for lane in range(ratio))
    held = 0
    for event in events:
        if event["kind"] != "phy":
            continue
        raw = event["return"]
        event["return"] = (raw & ~selected) | held
        held = raw & selected
    assert held == 0, "fixture needs an idle return tail"
    return events


class ConnectedScalingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"])
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "raw.jsonl"

    def replay(self, events, profile, **options):
        self.path.write_text("".join(json.dumps(e) + "\n" for e in events))
        return check(self.path, profile, ranks=1, depth=16, **options)

    def test_retired_windows_preserve_complete_result_and_fault_payload_counts(self):
        profile, events = trace_fixture()
        result = self.replay(events, profile)
        self.assertEqual((result["physical_write_words"], result["returned_slices"]), (16, 32))
        self.assertEqual((result["write_bytes"], result["read_bytes"]), (64, 64))
        fault_ps = events[-1]["ps"] - 1
        events.insert(-1, dict(kind="fault", ps=fault_ps))
        fault = self.replay(events, profile, fault_ps=fault_ps)
        self.assertEqual(fault["physical_payload_bytes_by_direction"], [64, 64])
        self.assertEqual(fault["failed_requests_by_direction"], [0, 0])

    def test_late_write_enable_with_data_and_late_cs_or_read_enable_reject(self):
        profile, original = trace_fixture()
        for field, value, marker in (
            ("write", 3 << 24 | 0x55 << 48, "write enables"),
            ("write", 1 << 16, "write CS"),
            ("read", 3 << 16, "read enables"),
            ("read", 5, "read CS"),
        ):
            events = copy.deepcopy(original)
            next(e for e in reversed(events) if e["kind"] == "phy")[field] = value
            with self.subTest(field=field, marker=marker), self.assertRaisesRegex(Violation, marker):
                self.replay(events, profile)

    def test_last_scheduled_write_data_is_still_checked(self):
        profile, events = trace_fixture()
        active = [e for e in events if e["kind"] == "phy" and e["write"] >> 24 & 255]
        last = active[-1]
        lane = max(lane for lane in range(profile["ratio"]) if last["write"] >> (24 + 2 * lane) & 3)
        target = last["ck"] + (lane + profile["dfi_wck"]["tphy_wrdata"]) // profile["ratio"]
        target_lane = (lane + profile["dfi_wck"]["tphy_wrdata"]) % profile["ratio"]
        next(e for e in events if e["kind"] == "phy" and e["ck"] == target)["write"] ^= 1 << (48 + target_lane * 32)
        with self.assertRaisesRegex(Violation, "unexpected physical write byte"):
            self.replay(events, profile)

    def test_duplicate_and_missing_returns_reject(self):
        profile, original = trace_fixture()
        returns = [e for e in original if e["kind"] == "phy" and e["return"] >> 32 & 255]
        events = copy.deepcopy(original)
        last = returns[-1]
        next(e for e in events if e["kind"] == "phy" and e["ck"] == last["ck"] + 1)["return"] = last["return"]
        with self.assertRaisesRegex(Violation, "returned word hole/identity"):
            self.replay(events, profile)
        events = copy.deepcopy(original)
        first = returns[0]
        next(e for e in events if e["kind"] == "phy" and e["ck"] == first["ck"])["return"] = 0
        with self.assertRaisesRegex(ValueError, "missing|rotation|return|deadline"):
            self.replay(events, profile)

    def test_independent_slice_skew_preserves_complete_result(self):
        profile, original = trace_fixture()
        expected = self.replay(original, profile)
        self.assertEqual(self.replay(skew_one_slice(original, profile["ratio"]), profile), expected)

    def test_snapshot_holes_and_dnv_are_still_rejected(self):
        profile, original = trace_fixture()
        first = next(e for e in original if e["kind"] == "phy" and e["return"] >> 32 & 255)
        for mask, marker in ((1 << 32, "returned word hole/identity"), (1 << 16, "unexpected DNV")):
            events = copy.deepcopy(original)
            next(e for e in events if e["kind"] == "phy" and e["ck"] == first["ck"])["return"] ^= mask
            with self.subTest(mask=mask), self.assertRaisesRegex(ValueError, marker):
                self.replay(events, profile)


if __name__ == "__main__":
    unittest.main()
