"""Source-only automatic-power witness and persistent-profile sensitivity."""

import copy
import unittest

from contracts import commands
from physical_stream import PhysicalStream
from system.host import Violation
from wrapper_management import profile_binding
from wrapper_power import PowerWitness, qualify_power_edges


def edge(tick=0, **changes):
    return {
        "tick": tick,
        "ps": 625 + 1250 * tick,
        "channel": 0,
        "policy": 1,
        "idle_ck": 64,
        "ctrl_req": 0,
        "data_req": 0,
        "ctrl_ack": 0,
        "data_ack": 0,
        "ctrlupd_req": 0,
        "wake": 0,
    } | changes


def consume(witness, sample, *, source=True, physical=True, inline=True):
    witness.consume(sample, source_empty=source, physical_ready=physical, inline_empty=inline)


def raw(tick, ca=0, cs=0):
    return dict(
        tick=tick,
        trusted=1,
        cs=cs,
        ca=ca,
        wck=0,
        write=0,
        read=0,
        **{"return": 0, "mr4": 9, "mr43": 0},
    )


class PowerTests(unittest.TestCase):
    def test_held_source_blocks_request_independently(self):
        w = PowerWitness(1)
        for t in range(128):
            consume(w, edge(t), source=False)
        self.assertEqual(w.deferred, 128)
        with self.assertRaisesRegex(Violation, "independent source/pin/ECC drain"):
            consume(w, edge(128, ctrl_req=1, data_req=1), source=False)

    def test_each_independent_owner_blocks_entry(self):
        for blocked in ("source", "physical", "inline"):
            with self.subTest(blocked=blocked):
                kwargs = {blocked: False}
                with self.assertRaisesRegex(Violation, "independent source/pin/ECC drain"):
                    consume(PowerWitness(1), edge(ctrl_req=1, data_req=1), **kwargs)

    def test_split_stale_and_incomplete_controls_fail(self):
        with self.assertRaisesRegex(Violation, "split low-power"):
            consume(PowerWitness(1), edge(ctrl_req=1))
        with self.assertRaisesRegex(Violation, "unbound power policy"):
            consume(PowerWitness(1), edge(policy=2))
        w = PowerWitness(1)
        consume(w, edge())
        with self.assertRaisesRegex(Violation, "power edge gap"):
            consume(w, edge(2))

    def test_pin_entry_needs_actual_sampled_acceptance(self):
        w = PowerWitness(1)
        consume(w, edge(ctrl_req=1, data_req=1))
        with self.assertRaisesRegex(Violation, "before sampled acceptance"):
            w.physical(sleeping=True, source_empty=True, was_drained=True, inline_empty=True)

    def test_pin_entry_rechecks_physical_tail(self):
        w = PowerWitness(1)
        consume(w, edge(ctrl_req=1, data_req=1, ctrl_ack=1, data_ack=1))
        with self.assertRaisesRegex(Violation, "before independent drain"):
            w.physical(sleeping=True, source_empty=True, was_drained=False, inline_empty=True)

    def test_power_exit_needs_consumed_ack_withdrawal(self):
        w = PowerWitness(1)
        consume(w, edge(ctrl_req=1, data_req=1, ctrl_ack=1, data_ack=1))
        w.physical(sleeping=True, source_empty=True, was_drained=True, inline_empty=True)
        with self.assertRaisesRegex(Violation, "before sampled ACK withdrawal"):
            w.physical(sleeping=False, source_empty=True, was_drained=True, inline_empty=True)

    def test_self_refresh_requires_a_new_update_per_exit(self):
        w = PowerWitness(2)
        consume(w, edge(policy=2, ctrl_req=1, data_req=1, ctrl_ack=1, data_ack=1))
        w.physical(sleeping=True, source_empty=True, was_drained=True, inline_empty=True)
        consume(w, edge(1, policy=2))
        with self.assertRaisesRegex(Violation, "SRX without actual update"):
            w.physical(sleeping=False, source_empty=True, was_drained=True, inline_empty=True)
        consume(w, edge(2, policy=2, ctrlupd_req=1))
        w.physical(sleeping=False, source_empty=True, was_drained=True, inline_empty=True)
        self.assertEqual(w.exited, 1)
        consume(w, edge(3, policy=2, ctrl_req=1, data_req=1, ctrl_ack=1, data_ack=1))
        w.physical(sleeping=True, source_empty=True, was_drained=True, inline_empty=True)
        consume(w, edge(4, policy=2))
        with self.assertRaisesRegex(Violation, "SRX without actual update"):
            w.physical(sleeping=False, source_empty=True, was_drained=True, inline_empty=True)

    def test_legal_deferral_entry_and_exit(self):
        w = PowerWitness(1)
        for t in range(128):
            consume(w, edge(t), source=False)
        consume(w, edge(128, ctrl_req=1, data_req=1, ctrl_ack=1, data_ack=1))
        w.physical(sleeping=True, source_empty=True, was_drained=True, inline_empty=True)
        consume(w, edge(129))
        w.physical(sleeping=False, source_empty=True, was_drained=True, inline_empty=True)
        self.assertEqual(w.finish()["entries"], 1)

    def test_terminal_tail_cannot_erase_sleep_or_deferral(self):
        with self.assertRaisesRegex(Violation, "entry/wake tail"):
            PowerWitness(1).finish()
        w = PowerWitness(1)
        w.entered = w.exited = 1
        with self.assertRaisesRegex(Violation, "deferral interval"):
            w.finish()

    def test_power_phase_coverage_rejects_missing_duplicate_reorder_and_timestamp(self):
        records = []
        for t in range(2):
            for c in range(2):
                records += [
                    edge(t, channel=c, kind="management_edge"),
                    edge(t, channel=c, kind="power_edge"),
                ]
        self.assertEqual(qualify_power_edges(records), 4)
        for mutation in ("missing", "duplicate", "reorder", "timestamp", "field"):
            altered = copy.deepcopy(records)
            if mutation == "missing":
                altered.pop()
            elif mutation == "duplicate":
                altered[-1] = altered[3]
            elif mutation == "reorder":
                altered[1], altered[5] = altered[5], altered[1]
            elif mutation == "timestamp":
                altered[-1]["ps"] += 1
            else:
                del altered[-1]["ctrl_ack"]
            with (
                self.subTest(mutation=mutation),
                self.assertRaises((Violation, KeyError)),
            ):
                qualify_power_edges(altered)

    def test_independent_power_down_cs_pulse_and_recovery(self):
        p = PhysicalStream(profile_binding())
        for t in range(10):
            p.sample(raw(t))
        p.sample(raw(10, 64, 1))
        for t in range(11, 20):
            p.sample(raw(t))
        for t in range(20, 23):
            p.sample(raw(t, 0, 1))
        for t in range(23, 28):
            p.sample(raw(t))
        self.assertIsNone(p.phy.dram.power[0])
        with self.assertRaisesRegex(Violation, "rank recovery"):
            p.sample(raw(28, commands.activate(0, 0)[0], 1))

    def test_independent_physical_self_refresh_hold_and_return(self):
        p = PhysicalStream(profile_binding())
        for t in range(10):
            p.sample(raw(t))
        p.sample(raw(10, 104, 1))
        self.assertEqual(p.phy.dram.power[0][0], "SRE")
        for t in range(11, 3511):
            p.sample(raw(t))
        self.assertFalse(p.phy.dram.maintenance.fault)
        p.sample(raw(3511, 40, 1))
        self.assertIsNone(p.phy.dram.power[0])
        with self.assertRaisesRegex(Violation, "rank recovery"):
            p.sample(raw(3512, commands.activate(0, 0)[0], 1))


if __name__ == "__main__":
    unittest.main()
