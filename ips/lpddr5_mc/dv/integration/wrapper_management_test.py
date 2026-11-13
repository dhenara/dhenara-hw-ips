"""Owning phase, source-ledger and entry/recovery sensitivity; no RTL run."""

import copy
import unittest

from system.host import Address, Violation
from wrapper_management import BUSES, Run, Source, profile_binding, qualify_phases, remaining_budget


def sample(**changes):
    e = dict(
        kind="sample",
        ps=1250,
        channel=0,
        tick=0,
        reset_n=1,
        memory_reset=0,
        destructive=0,
        confirmed=1,
        reset_request=0,
        settled=1,
        enabled=0,
        host_fault=0,
        fault=0,
        trusted=1,
        owner=2,
        boot=0,
        config=1,
        config_valid=1,
        trained=1,
        training_epoch=0,
        state=1,
        drained=1,
        pin_drained=1,
        ecc_drained=1,
        data_committed=0,
        admission=1,
        phy_update=0,
        master=0,
        rank_valid=1,
        rank_sr=0,
        refresh_request=0,
        refresh_done=0,
        phyupd_req=1,
        phymstr_req=0,
        phyupd_type=0,
        phymstr_type=0,
        phymstr_cs_state=0,
        phymstr_state_sel=0,
        error=0,
        mr4=9,
        mr43=0,
        cs=0,
        ca=0,
        wck=0,
        write=0,
        read=0,
        profile_image="0x1",
    )
    e["return"] = 0
    return e | changes


def entered(master=False):
    run = Run(profile_binding())
    run.case = dict(id=0, failed=0, request=0, entries=0)
    run.sample(sample(phyupd_req=int(not master), phymstr_req=int(master)))
    return run


class PhaseTests(unittest.TestCase):
    def setUp(self):
        self.header = dict(
            sample_phase="negedge_after_prepare_before_sample",
            return_source="memory_output_after_prepare",
            handshake_phase="posedge_before_NBA",
            ck_ps=1250,
        )
        self.records = []
        for c in range(2):
            for tick in range(3):
                s = sample(channel=c, tick=tick, ps=1250 + tick * 1250)
                self.records += [
                    s,
                    dict(kind="edge", channel=c, tick=tick, ps=s["ps"] + 625, **{k: s[k] for k in BUSES}),
                ]

    def test_positive_independent_phases(self):
        self.assertEqual(qualify_phases(self.records, self.header), 6)

    def test_c2_offset_cannot_be_copied(self):
        for shift in (-625, 625, 1250):
            mutant = copy.deepcopy(self.records)
            mutant[1]["ps"] += shift
            with self.assertRaisesRegex(Violation, "displacement"):
                qualify_phases(mutant, self.header)

    def test_missing_edge_tail(self):
        with self.assertRaisesRegex(Violation, "phase tail"):
            qualify_phases(self.records[:-1], self.header)

    def test_each_raw_bus_is_bound(self):
        for field in BUSES:
            mutant = copy.deepcopy(self.records)
            mutant[1][field] = 1
            with self.assertRaisesRegex(Violation, "raw buses"):
                qualify_phases(mutant, self.header)

    def test_return_preparation_origin_required(self):
        for origin in (None, "integration_net_in_prepare_delta"):
            with self.assertRaisesRegex(Violation, "unqualified"):
                qualify_phases(self.records, self.header | {"return_source": origin})

    def test_prior_delta_return_cannot_match_receiver(self):
        # The failed first run sampled the prior integration-net return in
        # the prepare delta, while the receiver got newly prepared valid data.
        mutant = copy.deepcopy(self.records)
        mutant[1]["return"] = "0x300000000"
        with self.assertRaisesRegex(Violation, "raw buses"):
            qualify_phases(mutant, self.header)

    def test_missing_ck_and_wrong_phase(self):
        with self.assertRaisesRegex(Violation, "missing CK"):
            qualify_phases(self.records[:2] + self.records[4:], self.header)
        with self.assertRaisesRegex(Violation, "unqualified"):
            qualify_phases(self.records, self.header | {"sample_phase": "posedge"})


class ComposedAdapterTests(unittest.TestCase):
    def test_populated_pin_phase_owner_and_source_bridge(self):
        # Reuse the reviewed independent CA/PHY generator, assigning the new
        # phases explicitly. This is a synthetic adapter test, not RTL evidence.
        from observations_test import Fixture
        from system.phy import DigitalPhy

        f = Fixture()
        f.p = profile_binding()
        f.gen = DigitalPhy(f.p)
        f.dirty()
        r = Run(profile_binding())
        records = []
        for tick, (raw, _, events) in enumerate(f.frames):
            e = sample(**{k: v for k, v in raw.items() if k in sample()})
            e.update(ps=1250 + tick * 1250, tick=tick)
            records += [e, dict(kind="edge", channel=0, tick=tick, ps=e["ps"] + 625, **{k: e[k] for k in BUSES})]
            r.sample(e)
            for event in events:
                if event["kind"] == "inline_admit":
                    r.source.event(dict(kind="aw", port=0, id=1, address=0, length=3, size=4, burst=1, lock=0))
                    for n in range(4):
                        r.source.event(
                            dict(
                                kind="w",
                                port=0,
                                data=int.from_bytes(bytes(range(n * 16, n * 16 + 16)), "little"),
                                strobe=65535,
                                last=int(n == 3),
                            )
                        )
                if event["kind"].startswith("inline_"):
                    r.event(event | {"channel": 0, "tick": tick, "ps": e["ps"] + 625})
                if event["kind"] == "inline_complete":
                    r.source.event(dict(kind="b", port=0, id=1, response=0, ready=1))
        self.assertEqual(
            qualify_phases(
                records,
                dict(
                    sample_phase="negedge_after_prepare_before_sample",
                    return_source="memory_output_after_prepare",
                    handshake_phase="posedge_before_NBA",
                    ck_ps=1250,
                ),
            ),
            len(f.frames),
        )
        self.assertTrue(r.source.settled())
        self.assertEqual(r.physical[0].summary()["write_words"], 32)
        self.assertEqual(r.inline[0].completed, 1)


class SourceTests(unittest.TestCase):
    def write(self):
        s = Source()
        s.event(dict(kind="aw", port=0, id=1, address=0, length=3, size=4, burst=1, lock=0))
        return s

    def test_trailing_w_and_all_b_are_owned_after_failure(self):
        s = self.write()
        s.host.fail()
        for i in range(4):
            self.assertFalse(s.settled())
            s.event(dict(kind="w", port=0, data=1, strobe=65535, last=int(i == 3)))
        self.assertFalse(s.settled())
        s.event(dict(kind="b", port=0, id=1, response=2, ready=0))
        self.assertFalse(s.settled())
        s.event(dict(kind="b", port=0, id=1, response=2, ready=1))
        self.assertTrue(s.settled())

    def test_b_before_w(self):
        s = self.write()
        s.host.fail()
        with self.assertRaisesRegex(Violation, "W count"):
            s.event(dict(kind="b", port=0, id=1, response=2, ready=1))

    def test_held_r_and_b_are_stable(self):
        for write in (False, True):
            s = self.write() if write else Source()
            if write:
                for i in range(4):
                    s.event(dict(kind="w", port=0, data=0, strobe=65535, last=int(i == 3)))
            else:
                s.event(dict(kind="ar", port=0, id=1, address=0, length=3, size=4, burst=1, lock=0))
            s.host.fail()
            event = dict(kind="b" if write else "r", port=0, id=1, response=2, ready=0, data=0, last=int(write))
            s.event(event)
            with self.assertRaisesRegex(Violation, "payload changed"):
                s.event(event | {"response": 0})
            with self.assertRaisesRegex(Violation, "VALID withdrawn"):
                s.event(dict(kind="axi", port=0, flags=0))

    def test_all_read_beats_required(self):
        s = Source()
        s.event(dict(kind="ar", port=0, id=1, address=0, length=3, size=4, burst=1, lock=0))
        s.host.fail()
        for i in range(4):
            self.assertFalse(s.settled())
            s.event(dict(kind="r", port=0, id=1, response=2, data=0, last=int(i == 3), ready=1))
        self.assertTrue(s.settled())

    def test_success_requires_physical_and_check_outcome(self):
        s = self.write()
        for i in range(4):
            s.event(dict(kind="w", port=0, data=0, strobe=65535, last=int(i == 3)))
        with self.assertRaisesRegex(Violation, "physical/integrity"):
            s.event(dict(kind="b", port=0, id=1, response=0, ready=1))

    def test_nonzero_source_owner_and_completion(self):
        s = self.write()
        payload = bytes(range(64))
        for n in range(4):
            s.event(
                dict(
                    kind="w",
                    port=0,
                    data=int.from_bytes(payload[n * 16 : n * 16 + 16], "little"),
                    strobe=65535,
                    last=int(n == 3),
                )
            )
        q = dict(row=0, bank=0, column=0, write=1, bytes=64, mask=(1 << 64) - 1, data=int.from_bytes(payload, "little"))
        tag = s.owner(q, 0)
        result = dict(failed=0, physical=1, integrity=1)
        for field in ("physical", "integrity"):
            with self.assertRaisesRegex(Violation, "independent result"):
                s.completed(tag, q, result | {field: 0}, payload)
        s.completed(tag, q, result, payload)
        s.event(dict(kind="b", port=0, id=1, response=0, ready=1))
        self.assertTrue(s.settled())


class EntryRecoveryTests(unittest.TestCase):
    def test_bounded_schedule(self):
        budget = remaining_budget(profile_binding())
        self.assertLess(budget["total_ck"], 96)
        p = profile_binding()
        p["management_ck"]["tphyupd_resp"] = 40
        with self.assertRaisesRegex(Violation, "exceeds"):
            remaining_budget(p)

    def test_positive_drained_entry(self):
        r = entered()
        r.sample(sample(ps=2500, tick=1, owner=1, phy_update=1))
        self.assertEqual(r.case["entries"], 1)

    def test_internal_settled_cannot_erase_source(self):
        for write in (False, True):
            r = entered()
            r.source.host.admit(Address(0, 1, 0, 3), write)
            with self.assertRaisesRegex(Violation, "actual W/R/B"):
                r.sample(sample(ps=2500, tick=1, owner=1, phy_update=1))

    def test_missing_physical_tail(self):
        r = entered()
        r.physical[0].expect(dict(tag=1, generation=0, boot=0, config=1, write=1, bytes=64))
        with self.assertRaisesRegex(Violation, "physical/row/WCK"):
            r.sample(sample(ps=2500, tick=1, owner=1, phy_update=1))

    def test_stale_recovery_and_training(self):
        r = entered()
        with self.assertRaisesRegex(Violation, "unconfirmed/stale"):
            r.sample(sample(ps=2500, tick=1, destructive=1))
        r = entered()
        with self.assertRaisesRegex(Violation, "stale trusted epoch"):
            r.sample(sample(ps=2500, tick=1, training_epoch=1))

    def test_missing_wck_tail(self):
        r = entered()
        r.physical[0].wck.epoch = {"fixture": True}
        # The drain predicate is also checked directly: internal empty bits
        # cannot override an independent live WCK epoch.
        self.assertFalse(r.physical[0].drained())

    def test_new_admission_after_crossing_latency(self):
        r = entered()
        for i in range(3):
            r.event(dict(kind="axi", port=0, ps=100 + i, flags=0))
        with self.assertRaisesRegex(Violation, "admission after closure"):
            r.event(dict(kind="axi", port=0, ps=104, flags=1 << 8))

    def test_master_return_requires_fresh_rank(self):
        r = entered(True)
        r.case["id"] = 2
        r.sample(sample(ps=2500, tick=1, owner=1, master=1, phyupd_req=0, phymstr_req=1))
        r.sample(sample(ps=3750, tick=2, owner=1, master=1, phyupd_req=0, phymstr_req=0))
        with self.assertRaisesRegex(Violation, "fresh rank"):
            r.sample(sample(ps=5000, tick=3, owner=2, phyupd_req=0, phymstr_req=0))


if __name__ == "__main__":
    unittest.main()
