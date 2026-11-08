"""External WCK phase boundaries, reuse and active violation sensitivity."""

import copy
import json
import unittest
from pathlib import Path

from check_observations import wck_sensitivity
from contracts import commands
from system.host import Violation
from wck_observations import WckObservations

PROFILES = json.loads((Path(__file__).resolve().parents[2] / "config/contracts/profiles.json").read_text())["profiles"]


def fixture(profile, write, beats=32, rank=0, reuse=False, mrr=False):
    ratio, half_ck = profile["ratio"], 2 * profile["ratio"]
    cas, accesses = 5, [6, 14] if reuse else [6]
    latency = profile["wl_ck" if write else "rl_ck"]
    enable = cas + ((4 if ratio == 4 else 3) if write else (7 if ratio == 4 else 5))
    last_data_end = (
        accesses[-1] * half_ck
        + profile["phy_half_wck"]["command"]
        + latency * half_ck
        + profile["phy_half_wck"]["dq_input" if write else "dq_output"]
        + beats
        + (16 if ratio == 4 and beats == 32 else 0)
    )
    stop = (last_data_end + 5 - profile["phy_half_wck"]["wck"] + half_ck - 1) // half_ck
    off = stop + 2
    release = off + (5 if profile["always_on"] else 8)
    rows = []
    for ck in range(release + 3):
        ca, cs, packed = 0, 0, 0
        if ck == cas:
            ca, cs = commands.cas("write" if write else "read"), 1 << rank
        elif ck in accesses:
            ca = commands.mode_read(4) if mrr else commands.access(write, beats, 0, 0)
            cs = 1 << rank
        elif ck == off:
            ca, cs = commands.cas("off"), 1 << rank
        if cas < ck < release:
            for phase in range(ratio):
                for byte_slice in range(2):
                    lane = phase * 2 + byte_slice
                    packed |= (1 << rank) << (16 + 2 * lane)
                    packed |= int(ck >= enable) << (32 + lane)
                    toggle = 0 if ck < enable + 4 else 2 if ck < enable + 5 else 3
                    if ck >= (off + 5 if profile["always_on"] else stop):
                        toggle = 0
                    packed |= toggle << (lane * 2)
        rows.append([ck, cs, ca, packed])
    return rows, enable, stop


class WckObservationTests(unittest.TestCase):
    def replay(self, profile, rows):
        model = WckObservations(profile)
        for row in rows:
            model.sample(*row)
        return model.finish()

    def test_both_ratios_modes_directions_burst_lengths_ranks_and_reuse(self):
        for index in (0, 4, 24, 28):
            p = copy.deepcopy(PROFILES[index])
            p["ranks"] = 2
            for write in (False, True):
                for beats in (16, 32):
                    for rank in (0, 1):
                        rows, _, _ = fixture(p, write, beats, rank, reuse=True)
                        with self.subTest(profile=index, write=write, beats=beats, rank=rank):
                            result = self.replay(p, rows)
                            self.assertEqual(result["accesses"], 2)
                            self.assertEqual(result["disabled_epochs"], 1)

    def test_mrr_read_preamble_and_tail(self):
        p = PROFILES[0]
        rows, _, _ = fixture(p, False, 16, mrr=True)
        self.assertEqual(self.replay(p, rows)["accesses"], 1)
        with self.assertRaisesRegex(Violation, "postamble"):
            self.replay(p, rows[:18])

    def test_each_byte_slice_and_phase_rejects_missing_fast_edge(self):
        p = PROFILES[4]
        rows, enable, _ = fixture(p, False)
        for lane in range(8):
            altered = copy.deepcopy(rows)
            altered[enable + 5][3] ^= 1 << (lane * 2)
            with self.subTest(lane=lane), self.assertRaisesRegex(Violation, "edge pattern"):
                self.replay(p, altered)

    def test_early_late_enable_static_half_rate_and_early_release(self):
        p = PROFILES[0]
        rows, enable, _ = fixture(p, True)
        changes = [
            (enable - 1, 1 << 32, "enable timing"),
            (enable, 1 << 32, "enable timing"),
            (enable + 3, 2, "edge pattern"),
            (enable + 4, 1, "edge pattern"),
            (enable + 5, 1 << 16, "without selected rank"),
            (enable + 5, 3 << 16, "rank change"),
        ]
        for at, mask, reason in changes:
            altered = copy.deepcopy(rows)
            altered[at][3] ^= mask
            with self.subTest(at=at, mask=mask), self.assertRaisesRegex(Violation, reason):
                self.replay(p, altered)

    def test_always_on_requires_ws_off_and_recovery_before_stopping(self):
        p = PROFILES[4]
        rows, _, stop = fixture(p, True)
        for at in (stop, stop + 3):
            altered = copy.deepcopy(rows)
            altered[at][3] &= ~3
            with self.assertRaisesRegex(Violation, "postamble/WS_OFF"):
                self.replay(p, altered)

    def test_upper_phase_rewind_unsynchronized_and_restart_rejected(self):
        p = PROFILES[24]
        rows, _, stop = fixture(p, True)
        changed = copy.deepcopy(rows)
        changed[1][3] = 1 << 8
        with self.assertRaisesRegex(Violation, "upper phase"):
            self.replay(p, changed)
        changed = copy.deepcopy(rows)
        changed[0][3] = 1 << 16
        with self.assertRaisesRegex(Violation, "without decoded CAS"):
            self.replay(p, changed)
        changed = copy.deepcopy(rows)
        changed[stop + 1][3] |= 3
        with self.assertRaisesRegex(Violation, "restarted"):
            self.replay(p, changed)
        model = WckObservations(p)
        model.sample(*rows[0])
        with self.assertRaisesRegex(Violation, "missing WCK"):
            model.sample(*rows[2])

    def test_early_ws_off_duplicate_sync_and_direction_change_rejected(self):
        p = PROFILES[0]
        rows, _, _ = fixture(p, True)
        for ca, reason in (
            (commands.cas("off"), "before physical postamble"),
            (commands.cas("write"), "before full disable"),
            (commands.access(False, 32, 0, 0), "without matching synchronization"),
        ):
            changed = copy.deepcopy(rows)
            changed[8][1:3] = [1, ca]
            with self.subTest(reason=reason), self.assertRaisesRegex(Violation, reason):
                self.replay(p, changed)

    def test_clean_active_tail_is_distinct_from_full_disable(self):
        p = PROFILES[4]
        rows, _, stop = fixture(p, True)
        result = self.replay(p, rows[: stop + 1])
        self.assertTrue(result["active_at_trace_end"])
        self.assertEqual(result["disabled_epochs"], 0)

    def test_raw_wck_mutations_reject_without_changing_original(self):
        p = PROFILES[4]
        rows, _, _ = fixture(p, True)
        events = [{"kind": "phy", "ck": ck, "cs": cs, "ca": ca, "wck": hex(w)} for ck, cs, ca, w in rows]
        before = copy.deepcopy(events)
        variants = wck_sensitivity(events)
        self.assertEqual(events, before)
        self.assertEqual(len(variants), 5)
        for name, changed in variants.items():
            samples = [[e["ck"], e["cs"], e["ca"], int(e["wck"], 16)] for e in changed]
            with self.subTest(name=name), self.assertRaises(Violation):
                self.replay(p, samples)
