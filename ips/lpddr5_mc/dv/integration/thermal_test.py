"""Complete physical returns must activate the hot timing/retention checks."""

import json
import unittest
from pathlib import Path

from contracts.commands import access, activate, maintenance
from system.dram import DramModel
from system.host import Violation
from thermal import ThermalReturns


def slices(code):
    return [(w, lane, code * 257 if w < 4 and lane == 0 else 0xDADA) for w in range(8) for lane in range(2)]


class ThermalReturnTests(unittest.TestCase):
    def setUp(self):
        profile = json.loads((Path(__file__).resolve().parents[2] / "config/contracts/profiles.json").read_text())[
            "profiles"
        ][0]
        self.model = DramModel(profile | {"ranks": 1})
        self.observer = ThermalReturns(self.model.maintenance)

    def complete(self, code=13):
        self.observer.begin(1, 10, 1, 4)
        for w, lane, value in slices(code):
            self.observer.receive(1, w, lane, value, 30, 37500)

    def test_only_complete_mrr4_changes_temperature(self):
        self.observer.begin(1, 10, 1, 4)
        for w, lane, value in slices(13)[:-1]:
            self.observer.receive(1, w, lane, value, 30, 37500)
        self.assertEqual(self.model.maintenance.thermal, 9)
        with self.assertRaisesRegex(Violation, "incomplete thermal return tail"):
            self.observer.finish()
        self.observer.receive(1, *slices(13)[-1], 31, 38750)
        result = self.observer.finish()
        self.assertEqual((result[0]["mr4"], result[0]["completed_ck"], result[0]["interval_ck"]), (13, 31, 781))
        self.assertEqual(result[0]["derating_ck"]["trc"], 3)
        self.observer.begin(2, 40, 1, 43)
        for w, lane, value in slices(9):
            self.observer.receive(2, w, lane, value, 60, 75000)
        self.assertEqual(self.model.maintenance.thermal, 13, "MR43 cannot change temperature")

    def test_duplicate_and_inconsistent_slices_are_rejected(self):
        self.observer.begin(1, 10, 1, 4)
        self.observer.receive(1, 0, 0, 0x0D0D, 30, 37500)
        with self.assertRaisesRegex(Violation, "duplicate thermal return slice"):
            self.observer.receive(1, 0, 0, 0x0D0D, 31, 38750)
        for w, lane, value in slices(13)[1:-1]:
            self.observer.receive(1, w, lane, value, 30, 37500)
        with self.assertRaisesRegex(Violation, "inconsistent complete thermal return"):
            self.observer.receive(1, 7, 1, 0xDADB, 31, 38750)

    def test_actual_hot_return_enables_derating_and_refresh_sensitivity(self):
        self.complete()
        p = self.model.p["dram_ck"]
        self.model.command(100, 1, activate(0, 0)[0])
        self.model.command(101, 1, activate(0, 0)[1])
        with self.assertRaisesRegex(Violation, "access without ready open row"):
            self.model.command(100 + p["trcd"], 1, access(False, 32, 0, 0))
        self.model.command(102 + p["trcd"], 1, access(False, 32, 0, 0))
        with self.assertRaisesRegex(Violation, "PRE/REF recovery"):
            self.model.command(100 + p["tras_min"], 1, maintenance("PRE", 0, False))
        self.model.command(102 + p["tras_min"], 1, maintenance("PRE", 0, False))
        with self.assertRaisesRegex(ValueError, "missed periodic refresh"):
            self.model.command(782, 0, 0)

    def test_reserved_code_cannot_be_applied(self):
        with self.assertRaisesRegex(ValueError, "temperature out of range"):
            self.complete(0)


if __name__ == "__main__":
    unittest.main()
