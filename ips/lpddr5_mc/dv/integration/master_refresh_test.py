"""Focused external REF observer tests; synthetic fixtures are not RTL evidence."""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from master_refresh_check import replay
from system.dram import DramModel
from system.host import Violation


def profile():
    return json.loads((Path(__file__).resolve().parents[2] / "config/contracts/profiles.json").read_text())["profiles"][
        0
    ]


def fixture():
    rows = []
    for ck in range(194):
        edge = dict(
            kind="edge",
            ck=ck,
            ack=int(2 <= ck <= 191),
            request=3 if ck == 21 else 2 if ck == 22 else 0,
            done=1 if ck == 21 else 2 if ck == 22 else 0,
            external=1 if ck == 22 else 2 if ck == 23 else 0,
            pin_cs=1 if ck == 21 else 2 if ck == 22 else 0,
            pin_ca=0x2038 if ck in (21, 22) else 0,
            pin_owner="phy" if ck in (21, 22) else "controller",
            phy_request=int(ck < 191),
            reports=0 if ck == 2 else 3,
            settled=1,
            closed=1,
            wck_off=1,
            admission=0,
            issue=0,
            fault=0,
            temperature_valid=0,
        )
        # ACK entry is a registered sample at CK1, withdrawal is consumed
        # at CK191 after both banks' independent REF recovery has expired.
        edge["ack"] = int(2 <= ck <= 191)
        sample = edge | dict(
            kind="sample",
            ack=int(1 <= ck < 191),
            request=3 if ck == 20 else 2 if ck == 21 else 0,
            external=edge["done"],
        )
        rows.extend((edge, sample))
    return rows


class MasterRefreshTest(unittest.TestCase):
    def test_legal_execution_recovery_memory_and_tail(self):
        checker = replay(fixture(), profile())
        self.assertEqual([e["rank"] for e in checker.finish()], [0, 1])
        self.assertEqual(checker.dram.maintenance.last_refresh, [21, 22])
        self.assertEqual([checker.dram.banks[r][0].ready for r in range(2)], [189, 190])

    def test_sensitivity(self):
        original = fixture()
        variants = {}

        def change(ck, phase, **fields):
            return [e | fields if e["ck"] == ck and e["kind"] == phase else dict(e) for e in original]

        variants["missing_execution"] = change(21, "edge", pin_cs=0, pin_ca=0)
        variants["wrong_rank_execution"] = change(21, "edge", pin_cs=2)
        variants["wrong_rank_done"] = change(21, "edge", done=2)
        variants["absent_rank_done"] = change(21, "edge", done=4)
        variants["missing_done"] = change(21, "edge", done=0)
        variants["stale_done"] = change(5, "edge", done=1)
        variants["unsolicited_done"] = change(0, "edge", done=1)
        variants["duplicate_done"] = change(23, "edge", done=1)
        variants["duplicate_execution"] = change(23, "edge", pin_cs=1, pin_ca=0x2038, pin_owner="phy")
        variants["normal_ACT_under_PHY"] = change(10, "edge", pin_cs=1, pin_ca=7)
        variants["normal_REF_under_PHY"] = change(10, "edge", pin_cs=1, pin_ca=0x2038)
        variants["unconsumed_done_pulse"] = change(21, "sample", external=0)
        variants["unsampled_done_credited"] = change(10, "sample", external=1)
        variants["source_unsettled"] = change(2, "edge", settled=0)
        variants["open_rows"] = change(2, "edge", closed=0)
        variants["WCK_live"] = change(2, "edge", wck_off=0)
        variants["premature_service"] = change(22, "edge", issue=1)
        variants["premature_admission"] = change(22, "edge", admission=1)
        variants["stale_rank_report"] = [e | dict(reports=3) for e in original]
        variants["request_not_withdrawn"] = change(191, "edge", phy_request=1)
        variants["premature_handback"] = [
            e
            | dict(
                phy_request=int(e["ck"] < 188),
                ack=int(2 <= e["ck"] <= 188) if e["kind"] == "edge" else int(1 <= e["ck"] < 188),
            )
            for e in original
        ]
        variants["missing_phase"] = [e for e in original if not (e["ck"] == 21 and e["kind"] == "edge")]
        variants["duplicate_phase"] = original[:43] + [dict(original[42])] + original[43:]
        variants["unfinished_tail"] = [e for e in original if e["ck"] < 191]
        late = copy.deepcopy(original)
        for e in late:
            if 20 <= e["ck"] <= 22:
                e.update(
                    request=3 if e["ck"] > 20 or e["kind"] == "sample" else 0, done=0, external=0, pin_cs=0, pin_ca=0
                )
            if e["ck"] == 23 and e["kind"] == "edge":
                e.update(request=3, done=1, pin_cs=1, pin_ca=0x2038, pin_owner="phy")
        variants["late_execution_and_done"] = late
        for name, rows in variants.items():
            with self.subTest(name=name), self.assertRaises((Violation, ValueError)):
                replay(rows, profile()).finish()
        print(f"MASTER_REFRESH_SENSITIVITY rejected={len(variants)}")

    def test_unsampled_transient_is_not_consumption(self):
        rows = [e | dict(done=1) if e["kind"] == "sample" and e["ck"] == 10 else dict(e) for e in fixture()]
        self.assertEqual(len(replay(rows, profile()).finish()), 2)

    def test_REF_RAA_and_recovery_are_physical(self):
        dram = DramModel(profile())
        dram.memory[0, 0, 0, 0] = 0x5A
        # ACT1/2 then a legal PRE creates real row/paired RAA state; REF
        # closes recovery and decrements RAA while preserving the same image.
        dram.command(0, 1, 7)
        dram.command(1, 1, 3)
        self.assertEqual(dram.maintenance.raa[0][0], 1)
        dram.command(100, 1, 120 | 64 << 7)
        dram.command(200, 1, 0x2038)
        self.assertEqual(dram.maintenance.raa[0][0], 0)
        self.assertEqual(dram.maintenance.last_refresh, [200, 0])
        self.assertEqual(dram.banks[0][0].ready, 368)
        self.assertEqual(dram.memory[0, 0, 0, 0], 0x5A)
        with self.assertRaises(Violation):
            dram.command(201, 1, 7)

    def test_RFM_does_not_discharge_retention(self):
        dram = DramModel(profile())
        dram.command(0, 1, 7)
        dram.command(1, 1, 3)
        dram.command(100, 1, 120 | 64 << 7)
        dram.command(200, 1, 0x2438)
        self.assertEqual(dram.maintenance.raa[0][0], 0)
        self.assertEqual(dram.maintenance.last_refresh, [0, 0])


if __name__ == "__main__":
    unittest.main()
