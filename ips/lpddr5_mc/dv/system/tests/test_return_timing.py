"""Qualify the physical lower bound separately from DFI latency deadlines."""

import copy
import json
import unittest
from pathlib import Path

from contracts import commands, profiles
from system.dram import Burst
from system.host import Violation
from system.phy import DigitalPhy
from system.return_timing import ReturnTiming

IP = Path(__file__).resolve().parents[3]
PROFILES = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"]
ROWS = json.loads((IP / "config/reference/link_ecc_matrix.json").read_text())


def observations(profile, beats=32, skew=(0, 1), mrr=False):
    """Generate returns from the independent full physical waveform/PHY model."""
    phy = DigitalPhy(profile, ROWS)
    wave = profiles.waveform(profile, "read", beats)
    burst = Burst(
        1,
        0,
        0,
        0,
        0,
        profile["management_ck"]["tctrl_delay"],
        False,
        False,
        beats,
        tuple(at - 2 * profile["ratio"] for at in wave.data),
    )
    phy.dram.bursts.append(burst)
    phy.schedule_read(burst, skew_ck=skew)
    events = []
    for ck in range(80):
        returned = phy.returned(ck)
        valid = sum(
            1 << (lane * 2 + byte) for lane, items in enumerate(returned) for byte, item in enumerate(items) if item
        )
        events.append(
            [
                ck,
                int(ck == 0),
                (commands.mode_read(4) if mrr else commands.access(False, beats, 0, 0)) if ck == 0 else 0,
                valid,
            ]
        )
    phy.finish()
    return events


def replay(profile, events):
    timing = ReturnTiming(profile)
    for event in events:
        timing.sample(*event)
    return timing.finish()


class ReturnTimingTests(unittest.TestCase):
    def test_all_profiles_bursts_and_mrr(self):
        for profile in PROFILES:
            for beats in (16, 32):
                with self.subTest(profile=profile["name"], beats=beats):
                    result = replay(profile, observations(profile, beats))
                    self.assertEqual(result["returned_slices"], beats)
            self.assertEqual(
                replay(profile, observations(profile, 16, mrr=True))["returned_slices"],
                16,
            )

    def test_early_late_hole_missing_tail_and_upper_lane(self):
        for profile in (PROFILES[0], PROFILES[24]):
            original = observations(profile, skew=(0, 0))
            first = next(i for i, event in enumerate(original) if event[3])
            for mutation in ("early", "late", "hole", "tail", "upper"):
                events = copy.deepcopy(original)
                if mutation == "early":
                    events[first - 1][3], events[first][3] = events[first][3], 0
                elif mutation == "late":
                    for event in events:
                        event[3] = 0
                elif mutation == "hole":
                    events[first][3] &= ~1
                elif mutation == "tail":
                    events = events[:first]
                else:
                    events[first][3] |= 1 << 8
                with (
                    self.subTest(profile=profile["name"], mutation=mutation),
                    self.assertRaises(Violation),
                ):
                    replay(profile, events)

    def test_rotation_continues_across_commands(self):
        profile = PROFILES[0]
        events = observations(profile, 16)
        second = observations(profile, 32)
        offset = len(events)
        events.extend([[ck + offset, cs, ca, valid] for ck, cs, ca, valid in second])
        self.assertEqual(replay(profile, events)["returned_slices"], 48)

    def test_interleaved_halves_keep_physical_word_order(self):
        # Two BL32 commands separated by two CK fit their first halves into
        # each other's split gaps. A deserialized CK straddles both commands.
        for profile in (p for p in PROFILES if p["ratio"] == 4):
            for delayed_first in (0, 1):
                phy = DigitalPhy(profile, ROWS)
                timing = ReturnTiming(profile)
                wave = profiles.waveform(profile, "read", 32)
                observed, pin_order = [[], []], []
                for index, command_ck in enumerate((0, 2)):
                    times = tuple(at - 8 + command_ck * 8 for at in wave.data)
                    burst = Burst(index, 0, index, 0, 0, command_ck + 2, False, False, 32, times)
                    phy.dram.bursts.append(burst)
                    phy.schedule_read(burst, skew_ck=(delayed_first if index == 0 else 0, 0))
                    pin_order.extend((times[word * 2 + 1], index, word) for word in range(16))
                for ck in range(80):
                    pointers = list(phy.pointer)
                    returned = phy.returned(ck)
                    valid = 0
                    for byte in range(2):
                        for ordinal in range(4):
                            lane = (pointers[byte] + ordinal) % 4
                            item = returned[lane][byte]
                            if item is not None:
                                observed[byte].append((item.burst, item.word))
                                valid |= 1 << (lane * 2 + byte)
                    timing.sample(
                        ck,
                        int(ck in (0, 2)),
                        commands.access(False, 32, 0, 0) if ck in (0, 2) else 0,
                        valid,
                    )
                with self.subTest(profile=profile["name"], delayed_first=delayed_first):
                    expected = [(index, word) for _, index, word in sorted(pin_order)]
                    self.assertEqual(observed, [expected, expected])
                    self.assertEqual(timing.finish()["returned_slices"], 64)
                    phy.finish()


if __name__ == "__main__":
    unittest.main()
