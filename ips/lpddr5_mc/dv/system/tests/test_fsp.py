"""Standard-derived MR16 boundaries; no RTL or hardware simulation runs here."""

import copy
import json
import unittest
from pathlib import Path

from contracts import commands
from contracts.lifecycle import fsp_budget
from system.dram import DramModel
from system.host import Violation

IP = Path(__file__).resolve().parents[3]
PROFILES = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"]
PROFILE = PROFILES[0]


def mrw(dram, at, address, value, rank=0):
    for i, ca in enumerate(commands.mode_write(address, value)):
        dram.command(at + i, rank + 1, ca)


class FspTests(unittest.TestCase):
    def test_staging_and_unchanged_mr16_use_ordinary_mrw_spacing(self):
        for value in (0, 1, 2):
            for gap in (11, 12, 13):
                with self.subTest(value=value, gap=gap):
                    dram = DramModel(PROFILE)
                    mrw(dram, 0, 16, value)
                    self.assertEqual(dram.des_until[0], 1)
                    self.assertEqual(dram.mr[0][16] >> 2 & 3, 0)
                    if gap < 12:
                        with self.assertRaisesRegex(Violation, "MR command recovery"):
                            mrw(dram, 1 + gap, 1, PROFILE["mr"]["1"])
                    else:
                        mrw(dram, 1 + gap, 1, PROFILE["mr"]["1"])
            # Non-changing high-current MR16 must not restart enable recovery.
        dram = DramModel(PROFILE)
        mrw(dram, 0, 16, 0x40)
        mrw(dram, 121, 16, 0x40)
        mrw(dram, 134, 1, 0)
        self.assertEqual(dram.des_until[0], 122)

    def test_fsp_wr_and_op_remain_independent(self):
        dram = DramModel(PROFILE)
        mrw(dram, 0, 16, 2)  # WR2, OP0
        mrw(dram, 13, 16, 0x46)  # WR2, OP1, VRCG high
        self.assertEqual(dram.mr[0][16] & 15, 6)
        mrw(dram, 215, 16, 6)  # Keep WR2/OP1, restore normal VRCG.
        self.assertEqual(dram.des_until[0], 296)
        self.assertEqual(dram.mr[0][16] & 15, 6)

    def test_missing_vrcg_and_reserved_fsp_reject(self):
        for value, error in (
            (5, "without high-current VRCG"),
            (10, "without high-current VRCG"),
            (3, "reserved FSP"),
            (12, "reserved FSP"),
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(Violation, error):
                    mrw(DramModel(PROFILE), 0, 16, value)

    def test_switch_restore_and_traffic_before_at_after(self):
        # Figure 200: first sample is MRW1; MRW2 at 11 starts tFC=201CK.
        # Restore MRW1 must be >=212; its MRW2 starts 80CK disable.
        for switch_gap in (200, 201, 202):
            for restore_gap in (79, 80, 81):
                with self.subTest(switch=switch_gap, restore=restore_gap):
                    dram = DramModel(PROFILE)
                    mrw(dram, 10, 16, 0x45)
                    self.assertEqual(dram.des_until[0], 212)
                    if switch_gap < 201:
                        with self.assertRaisesRegex(Violation, "DES-only recovery"):
                            mrw(dram, 11 + switch_gap, 16, 5)
                        continue
                    restore_at = 11 + switch_gap
                    mrw(dram, restore_at, 16, 5)
                    release = restore_at + 1 + restore_gap
                    if restore_gap < 80:
                        with self.assertRaisesRegex(Violation, "DES-only recovery"):
                            dram.command(release, 1, commands.activate(0, 0)[0])
                    else:
                        for i, ca in enumerate(commands.activate(0, 0)):
                            dram.command(release + i, 1, ca)

    def test_vrcg_only_enable_disable_and_des_vs_nop(self):
        for high, ns in ((True, 150000), (False, 100000)):
            for gap in (ns // 1250 - 1, ns // 1250, ns // 1250 + 1):
                for ca in (0, commands.mode_write(1, 0)[0], commands.maintenance("PRE")):
                    dram = DramModel(PROFILE)
                    dram.mr[0][16] = 0 if high else 64
                    mrw(dram, 0, 16, 64 if high else 0)
                    dram.command(gap, 0, 0)  # DES is always allowed.
                    if gap < ns // 1250:
                        with self.assertRaisesRegex(Violation, "DES-only recovery"):
                            dram.command(1 + gap, 1, ca)
                    else:
                        dram.command(1 + gap, 1, ca)

    def test_recovery_does_not_block_other_rank_or_reset_into_stale_state(self):
        dram = DramModel(PROFILE)
        mrw(dram, 0, 16, 0x45)
        mrw(dram, 2, 16, 2, rank=1)
        self.assertEqual(dram.des_until, [202, 3])
        fresh = DramModel(PROFILE)
        self.assertEqual(fresh.des_until, [0, 0])
        mrw(fresh, 0, 16, 1)
        mrw(fresh, 13, 1, 0)

    def test_clock_guards_are_contained_but_never_skipped(self):
        profile = copy.deepcopy(PROFILE)
        profile["dram_ck"].update(tckfspe=120, tckfspx=100)
        # Arithmetic stress of the model's representation; RTL does not admit
        # this changed timing profile. Continuous CK needs both clock windows.
        for at in (221, 222, 223):
            dram = DramModel(profile)
            mrw(dram, 0, 16, 0x45)
            if at < 222:
                with self.assertRaisesRegex(Violation, "DES-only recovery"):
                    mrw(dram, at, 16, 5)
            else:
                mrw(dram, at, 16, 5)

    def test_original_actual_ca_projection_still_rejects_illegal_vrcg(self):
        original = json.loads((Path(__file__).parent / "fixtures/fsp-original-vrcg.json").read_text())
        dram = DramModel(PROFILE)
        for sample in original["samples"]:
            if sample["tick"] == 909:
                with self.assertRaisesRegex(Violation, "FSP-OP change without high-current VRCG"):
                    dram.command(sample["cycles"], sample["cs"], sample["ca"])
                self.assertEqual(dram.mr[0][16], 1)
            else:
                dram.command(sample["cycles"], sample["cs"], sample["ca"])
        self.assertEqual(original["samples"][-1]["ca"], 648)  # Original raw MRW2(0x05).

    def test_budget_includes_restore_and_current_target_timing(self):
        # Independent inventory: 20 pairs (40CK), 18*12CK staging recovery,
        # 201CK switching, 80CK restore, 2CK pin tail, 4/128CK DFI, 8 margin.
        self.assertEqual(fsp_budget(PROFILE, PROFILE), 679)
        for field, increase in (("tmrd", 100), ("tfc", 31), ("tckfspx", 200)):
            target = copy.deepcopy(PROFILE)
            target["dram_ck"][field] += increase
            expected = 711 if field == "tmrd" else 710 if field == "tfc" else 690
            self.assertEqual(fsp_budget(PROFILE, target), expected)
        current = copy.deepcopy(PROFILE)
        current["dram_ck"]["tmrd"] += 20
        self.assertEqual(fsp_budget(current, PROFILE), 679 + 18 * 20)
        # Bounds are finite, widened before accumulation; no 16-bit sum wraps.
        for profile in (current, target):
            for field in ("tmrd", "tmrw", "tfc", "tckfspe", "tckfspx"):
                profile["dram_ck"][field] = 65535
            profile["management_ck"].update(tinit_start=65535, tinit_complete=65535)
        reserve = fsp_budget(current, target)
        self.assertEqual(reserve, 1507355)
        self.assertLess(reserve + 2, 1 << 32)

    def test_budget_rejects_overflow_missing_timing_and_unadmitted_modes(self):
        for field, value in (("tmrw", 65536), ("tfc", 0), ("tckfspx", -1)):
            profile = copy.deepcopy(PROFILE)
            profile["dram_ck"][field] = value
            with self.assertRaises(ValueError):
                fsp_budget(PROFILE, profile)
        for mutation in (
            lambda p: p.update(ck_ps=2500),
            lambda p: p["mr"].update({"16": 64}),
            lambda p: p["dram_ck"].pop("tfc"),
        ):
            profile = copy.deepcopy(PROFILE)
            mutation(profile)
            with self.assertRaises(ValueError):
                fsp_budget(PROFILE, profile)


if __name__ == "__main__":
    unittest.main()
