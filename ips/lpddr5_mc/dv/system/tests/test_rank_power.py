"""Shared CA power commands still apply every selected rank's constraints."""

import json
import unittest
from pathlib import Path

from system.dram import DramModel
from system.host import Violation
from system.phy import DigitalPhy

IP = Path(__file__).resolve().parents[3]
PROFILE = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"][0] | {"ranks": 2}


class RankPowerTests(unittest.TestCase):
    def test_simultaneous_self_refresh_holds_and_recovers_both_ranks(self):
        model = DramModel(PROFILE)
        model.command(0, 3, 104)
        self.assertEqual(model.maintenance.self_refresh, {0, 1})
        model.command(5000, 0, 0)
        model.command(5001, 3, 40)
        self.assertEqual(model.power, [None, None])
        self.assertEqual(model.maintenance.self_refresh, set())
        self.assertEqual(model.rank_ready, [5001 + PROFILE["dram_ck"]["txsr"]] * 2)
        with self.assertRaisesRegex(Violation, "rank recovery"):
            model.command(5002, 2, 7)

    def test_each_selected_rank_must_meet_residence_and_closed_bank_checks(self):
        model = DramModel(PROFILE)
        model.command(0, 1, 104)
        model.command(10, 2, 104)
        with self.assertRaisesRegex(Violation, "minimum self-refresh residence"):
            model.command(20, 3, 40)
        model = DramModel(PROFILE)
        model.banks[1][0].row = 1
        with self.assertRaisesRegex(Violation, "SRE open bank"):
            model.command(0, 3, 104)

    def test_two_rank_power_down_exit_advances_one_sample_once(self):
        phy = DigitalPhy(PROFILE)
        phy.command(0, 3, 64)
        for ck in range(1, 20):
            phy.command(ck, 0, 0)
        for ck in range(20, 23):
            phy.command(ck, 3, 0)
        for ck in range(23, 28):
            phy.command(ck, 0, 0)
        physical = 27 + PROFILE["management_ck"]["tctrl_delay"]
        self.assertEqual(phy.dram.last_ck, physical)
        self.assertEqual(phy.dram.power, [None, None])
        self.assertEqual(phy.dram.rank_ready, [physical + PROFILE["dram_ck"]["txp"]] * 2)
        with self.assertRaisesRegex(Violation, "PDX without power-down"):
            phy.dram.power_exit(physical + 1, 0, cs_high=20, cs_low=23, ca_low=1)

    def test_short_exit_pulse_still_fails_with_two_ranks(self):
        phy = DigitalPhy(PROFILE)
        phy.command(0, 3, 64)
        for ck in range(1, 20):
            phy.command(ck, 0, 0)
        phy.command(20, 3, 0)
        with self.assertRaisesRegex(Violation, "PDX CS pulse"):
            for ck in range(21, 26):
                phy.command(ck, 0, 0)

    def test_bad_second_exit_preserves_both_pending_ranks(self):
        model = DramModel(PROFILE)
        model.command(0, 3, 64)
        with self.assertRaisesRegex(Violation, "PDX CS pulse"):
            model.power_exits(
                27,
                {
                    0: {"cs_high": 20, "cs_low": 23, "ca_low": 1},
                    1: {"cs_high": 22, "cs_low": 23, "ca_low": 1},
                },
            )
        self.assertEqual(model.power, [("PDE", 0), ("PDE", 0)])
        self.assertEqual(model.last_ck, 0)

    def test_absent_rank_and_non_power_broadcast_remain_illegal(self):
        with self.assertRaisesRegex(Violation, "absent rank"):
            DramModel(PROFILE | {"ranks": 1}).command(0, 3, 104)
        for ca in (7, 24, 56, 120, 5):
            with self.subTest(ca=ca), self.assertRaisesRegex(Violation, "multi-rank"):
                DramModel(PROFILE).command(0, 3, ca)


if __name__ == "__main__":
    unittest.main()
