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


def natural_fixture(p, write, stop_delta=None, beats=32, rank=0, mrr=False):
    """Independent idle-tail waveform with optional stop near natural release.

    Natural disable originates at the access, explicit stop at its own CAS.
    Rank stays selected/static through postamble and the full disable interval.
    """
    rows, enable, post = fixture(p, write, beats, rank, mrr=mrr)
    access = 6
    natural = access + p["wck"]["twck_dis"] // p["ratio"]
    off = None if stop_delta is None else natural + stop_delta
    release = natural if off is None or off >= natural else off + p["wck"]["twck_dis"] // p["ratio"]
    result = []
    for ck in range(max(release + 3, (off or 0) + 1)):
        cs, ca, packed = 0, 0, 0
        if ck == 5:
            cs, ca = 1 << rank, commands.cas("write" if write else "read")
        if ck == access:
            cs, ca = 1 << rank, commands.mode_read(4) if mrr else commands.access(write, beats, 0, 0)
        if ck == off:
            cs, ca = 1 << rank, commands.cas("off")
        if 5 < ck < release:
            for phase in range(p["ratio"]):
                for byte in range(2):
                    lane = 2 * phase + byte
                    packed |= (1 << rank) << (16 + 2 * lane)
                    packed |= int(ck >= enable) << (32 + lane)
                    toggle = 0 if ck < enable + 4 or ck >= post else 2 if ck < enable + 5 else 3
                    packed |= toggle << (2 * lane)
        result.append([ck, cs, ca, packed])
    return result, natural, release


class WckObservationTests(unittest.TestCase):
    def replay(self, profile, rows):
        model = WckObservations(profile)
        for row in rows:
            model.sample(*row)
        return model.finish()

    def test_natural_disable_stop_before_at_after_and_fresh_owners(self):
        for index in (0, 24):
            p = copy.deepcopy(PROFILES[index])
            p["ranks"] = 2
            for write, beats, mrr in ((True, 32, False), (False, 32, False), (False, 16, True)):
                for delta in (None, -1, 0, 1):
                    rows, natural, release = natural_fixture(p, write, delta, beats, mrr=mrr)
                    with self.subTest(profile=index, write=write, mrr=mrr, delta=delta):
                        if delta in (0, 1):
                            with self.assertRaisesRegex(Violation, "WS_OFF ownership"):
                                self.replay(p, rows)
                            continue
                        self.assertEqual(self.replay(p, rows)["disabled_epochs"], 1)
                        # Fresh opposite-rank read/write/MRR owner only after
                        # actual full disable; no old-rank/static resurrection.
                        next_rows, _, _ = natural_fixture(p, not write, beats=16, rank=1, mrr=write)
                        origin = release + 1
                        combined = rows[:origin] + [
                            [ck + origin - 5, cs, ca, w] for ck, cs, ca, w in next_rows if ck >= 5
                        ]
                        self.assertEqual(self.replay(p, combined)["accesses"], 2)
                        altered = copy.deepcopy(rows)
                        altered[release + 1][3] = rows[release - 1][3]
                        with self.assertRaisesRegex(Violation, "without decoded CAS"):
                            self.replay(p, altered)

    def test_ws_off_command_recovery_independent_of_rank_release(self):
        p = copy.deepcopy(PROFILES[0])
        rows, _, release = natural_fixture(p, True, -1)
        # Artificially end the serialized selection one CK after WS_OFF.
        # Postamble is complete, but Table363 still prohibits a fresh CAS.
        off = next(ck for ck, cs, ca, _ in rows if cs and ca == commands.cas("off"))
        rows[off + 1][3] = 0
        rows[off + 1][1:3] = [1, commands.cas("read")]
        with self.assertRaisesRegex(Violation, "WS_OFF command recovery"):
            self.replay(p, rows[: off + 2])

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

    def test_new_cas_waits_for_the_actual_final_disable_phase(self):
        p = PROFILES[4]
        rows, _, _ = fixture(p, True)
        release = len(rows) - 3
        # Keep both byte slices selected in phase zero, releasing only at
        # phase one. A CAS sampled at this CK's start is still too early.
        rows[release][3] = rows[release - 1][3] & ((3 << 32) | (15 << 16) | 15)
        rows[release][1:3] = [1, commands.cas("read")]
        with self.assertRaisesRegex(Violation, "precedes final phase disable"):
            self.replay(p, rows)
