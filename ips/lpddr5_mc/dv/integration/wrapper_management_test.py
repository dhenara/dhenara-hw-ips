"""Owning phase, source-ledger and entry/recovery sensitivity; no RTL run."""

import copy
import os
import tempfile
import unittest
from pathlib import Path

from system.host import Address, Violation
from wrapper_management import (
    BUSES,
    MANAGEMENT_FIELDS,
    Addresses,
    ManagementWitness,
    Run,
    Source,
    final_tail_budget,
    profile_binding,
    qualify_phases,
    remaining_budget,
)
from wrapper_management_host_test import completion as host_completion
from wrapper_management_host_test import edge as host_edge
from wrapper_management_host_test import request as host_request
from wrapper_management_host_test import row as host_row


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


def witness(s, **changes):
    return (
        dict(
            kind="management_edge",
            channel=s["channel"],
            tick=s["tick"],
            ps=s["ps"] + 625,
            **{k: s[k] for k in MANAGEMENT_FIELDS},
        )
        | changes
    )


def phase_records(s):
    return [
        s,
        dict(kind="edge", channel=s["channel"], tick=s["tick"], ps=s["ps"] + 625, **{k: s[k] for k in BUSES}),
        witness(s),
    ]


def advance(run, s, **consumed):
    old = run.last[s["channel"]]
    if old is not None:
        run.event(witness(old, **consumed))
    run.sample(s)


def entered(master=False):
    run = Run(profile_binding())
    run.case = dict(id=0, failed=0, request=0, entries=0)
    run.sample(sample(phyupd_req=int(not master), phymstr_req=int(master)))
    return run


class PhaseTests(unittest.TestCase):
    def setUp(self):
        self.header = dict(
            version=4,
            host_phase="posedge_before_NBA_next_edge_counter",
            host_reset_phase="negedge_core_reset_before_NBA",
            sample_phase="negedge_after_prepare_before_sample",
            return_source="memory_output_after_prepare",
            handshake_phase="posedge_before_NBA",
            management_phase="posedge_before_NBA",
            ck_ps=1250,
        )
        self.records = []
        for c in range(1):
            for tick in range(3):
                s = sample(channel=c, tick=tick, ps=1250 + tick * 1250)
                self.records += phase_records(s)
                self.records.append(host_edge(tick) | dict(ps=s["ps"] + 625))
        self.records.sort(key=lambda e: e["ps"])

    def test_positive_independent_phases(self):
        both = sorted(
            self.records + [e | {"channel": 1} for e in self.records if e["kind"] != "host_edge"], key=lambda e: e["ps"]
        )
        self.assertEqual(qualify_phases(both, self.header), 6)

    def test_c2_offset_cannot_be_copied(self):
        for shift in (-625, 625, 1250):
            mutant = copy.deepcopy(self.records)
            mutant[1]["ps"] += shift
            with self.assertRaisesRegex(Violation, "displacement"):
                qualify_phases(mutant, self.header)

    def test_missing_edge_tail(self):
        with self.assertRaisesRegex(Violation, "phase tail"):
            qualify_phases([e for e in self.records if not (e["kind"] == "edge" and e["tick"] == 2)], self.header)

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
            qualify_phases([e for e in self.records if e["tick"] != 1], self.header)
        with self.assertRaisesRegex(Violation, "unqualified"):
            qualify_phases(self.records, self.header | {"sample_phase": "posedge"})

    def test_witness_missing_wrong_duplicate_reordered_and_old_phase(self):
        original = copy.deepcopy(self.records)
        mutants = []
        mutants.append(([e for e in original if e["kind"] != "management_edge"], "old trace unqualified"))
        changed = copy.deepcopy(original)
        changed[2]["ps"] += 1
        mutants.append((changed, "timestamp"))
        changed = copy.deepcopy(original)
        changed.insert(3, changed[2])
        mutants.append((changed, "duplicate"))
        changed = copy.deepcopy(original)
        changed[1], changed[2] = changed[2], changed[1]
        mutants.append((changed, "reordered"))
        changed = copy.deepcopy(original)
        changed[2]["tick"] = 100
        mutants.append((changed, "missing rising"))
        for rows, reason in mutants:
            with self.subTest(reason=reason), self.assertRaisesRegex(Violation, reason):
                qualify_phases(rows, self.header)
        with self.assertRaisesRegex(Violation, "unqualified"):
            qualify_phases(original, self.header | {"management_phase": None})
        self.assertEqual(self.records, original)

    def test_version4_requires_complete_atomic_host_edges_and_explicit_phases(self):
        for kind in ("missing", "duplicate", "channel", "field", "timestamp"):
            records = copy.deepcopy(self.records)
            hosts = [e for e in records if e["kind"] == "host_edge"]
            if kind == "missing":
                records.remove(hosts[-1])
            elif kind == "duplicate":
                records.append(hosts[-1])
            elif kind == "channel":
                hosts[0]["channels"].pop()
            elif kind == "field":
                del hosts[0]["count"]
            else:
                hosts[0]["ps"] += 1
            with self.subTest(kind=kind), self.assertRaises(Violation):
                qualify_phases(records, self.header)
        for fields in (dict(version=3), dict(host_phase=None), dict(host_reset_phase=None)):
            with self.assertRaisesRegex(Violation, "unqualified"):
                qualify_phases(self.records, self.header | fields)


class ManagementWitnessTests(unittest.TestCase):
    def test_both_registered_releases_allow_adjacent_falling_11_then_00(self):
        for master in (False, True):
            m = ManagementWitness()
            fields = dict(
                master=int(master), phy_update=int(not master), phymstr_req=int(master), phyupd_req=int(not master)
            )
            s = sample(**fields)
            m.sample(s)
            if master:
                m.consume(witness(s, rank_valid=0))
                s = sample(ps=2500, tick=1, **fields)
                m.sample(s)
            # The live rising request withdraws AFTER the falling snapshot.
            # Fresh returned state is consumed on this rising edge as well.
            m.consume(witness(s, phymstr_req=0, phyupd_req=0))
            result = sample(ps=s["ps"] + 1250, tick=s["tick"] + 1, master=0, phy_update=0, phymstr_req=0, phyupd_req=0)
            self.assertTrue(m.sample(result))
            self.assertEqual(m.releases, 1)

    def test_high_request_early_release_and_missing_registered_release(self):
        for master in (False, True):
            fields = dict(
                master=int(master), phy_update=int(not master), phymstr_req=int(master), phyupd_req=int(not master)
            )
            for stays in (False, True):
                m = ManagementWitness()
                s = sample(**fields)
                m.sample(s)
                m.consume(
                    witness(s, phymstr_req=0 if stays else int(master), phyupd_req=0 if stays else int(not master))
                )
                with self.assertRaisesRegex(Violation, "registered ACK"):
                    m.sample(
                        sample(
                            ps=2500,
                            tick=1,
                            master=int(stays and master),
                            phy_update=int(stays and not master),
                            phymstr_req=0,
                            phyupd_req=0,
                        )
                    )

    def test_falling_only_low_rank_does_not_establish_fresh_report(self):
        m = ManagementWitness()
        s = sample(master=1, phyupd_req=0, phymstr_req=1, rank_valid=0)
        m.sample(s)
        m.consume(witness(s, rank_valid=1, phymstr_req=0))
        with self.assertRaisesRegex(Violation, "fresh sampled rank"):
            m.sample(sample(ps=2500, tick=1, phyupd_req=0))

    def test_entry_requires_consumed_request_and_actual_drain(self):
        for mutation, reason in (({"phyupd_req": 0}, "sampled request"), ({"drained": 0}, "consumed drain")):
            m = ManagementWitness()
            s = sample()
            m.sample(s)
            m.consume(witness(s, **mutation))
            with self.assertRaisesRegex(Violation, reason):
                m.sample(sample(ps=2500, tick=1, phy_update=1))

    def test_live_type_mutation_and_unsolicited_refresh_rejected(self):
        m = ManagementWitness()
        s = sample(phy_update=1)
        m.sample(s)
        m.consume(witness(s))
        s = sample(ps=2500, tick=1, phy_update=1)
        m.sample(s)
        with self.assertRaisesRegex(Violation, "unstable consumed"):
            m.consume(witness(s, phyupd_type=1))
        m = ManagementWitness()
        s = sample(master=1, phyupd_req=0, phymstr_req=1)
        m.sample(s)
        with self.assertRaisesRegex(Violation, "unsolicited sampled refresh"):
            m.consume(witness(s, refresh_done=1))

    def test_missing_duplicate_and_wrong_rising_edge(self):
        m = ManagementWitness()
        s = sample()
        m.sample(s)
        for e in (witness(s, tick=1), witness(s, ps=1800), witness(s, master=1)):
            with self.assertRaises(Violation):
                m.consume(e)
        with self.assertRaisesRegex(Violation, "missing management"):
            m.sample(sample(ps=2500, tick=1))
        m = ManagementWitness()
        m.sample(s)
        m.consume(witness(s))
        with self.assertRaisesRegex(Violation, "duplicate"):
            m.consume(witness(s))


class FinalTailTests(unittest.TestCase):
    def test_generated_bound_is_scoped_to_wrapper_include(self):
        from vectors import generate

        with tempfile.TemporaryDirectory(dir=os.environ.get("DHENARA_AGENT_TOOL_STORAGE_ROOT")) as directory:
            destination = Path(directory)
            generate(destination)
            self.assertEqual(
                {p.name for p in destination.iterdir()},
                {"expected_profiles.svh", "images.txt", "read_link_encoder.svh", "wrapper_tail.svh"},
            )
            bound = final_tail_budget(profile_binding())["total_ck"]
            self.assertIn(f"WM_FINAL_TAIL_CK={bound}", (destination / "wrapper_tail.svh").read_text())
            self.assertNotIn("WM_FINAL_TAIL", (destination / "expected_profiles.svh").read_text())

    def test_selected_bound_covers_refresh_recovery_and_staggered_owners(self):
        p = profile_binding()
        b = final_tail_budget(p)
        self.assertEqual(b["total_ck"], 2152)
        self.assertGreater(b["maintenance_ck"], 2 * p["dram_ck"]["trfc_ab"])
        self.assertLess(b["total_ck"], b["renewal_floor_ck"])
        self.assertEqual(remaining_budget(p)["total_ck"], 83)

    def test_bound_changes_with_profile_and_rejects_nonfinite_workload(self):
        p = profile_binding()
        b = final_tail_budget(p)
        p["dram_ck"]["trfc_ab"] += 1
        self.assertGreater(final_tail_budget(p)["total_ck"], b["total_ck"])
        for page_idle in (0, 3000):
            with self.assertRaises(Violation):
                final_tail_budget(profile_binding(), page_idle)
        p = profile_binding()
        p["dram_ck"]["tzqlat"] = 3000
        with self.assertRaisesRegex(Violation, "recurring maintenance"):
            final_tail_budget(p)

    def test_exact_deadline_and_late_tail_and_false_aggregate(self):
        # Real independent PhysicalStream instances; zero raw idle samples
        # establish their drained state. A pending request cannot be erased
        # with internal aggregate or pin/ECC flags.
        def ready():
            r = Run(profile_binding())
            for c in range(2):
                r.sample(sample(channel=c, phyupd_req=0))
            r.case = dict(
                id=0,
                failed=False,
                fresh=True,
                fresh_ps=1250,
                entries=1,
                ack_ps=1,
                handback=True,
                closed={0: 1, 1: 1},
                closure_probes={0: 1, 1: 1},
                probes_accepted=True,
            )
            return r

        deadline = 1250 + final_tail_budget(profile_binding())["total_ck"] * 1250
        r = ready()
        r.marker(dict(name="end_case", ps=deadline, count=0))
        r = ready()
        with self.assertRaisesRegex(Violation, "timeout"):
            r.marker(dict(name="end_case", ps=deadline + 1))
        for aggregate in (False, True):
            r = ready()
            if aggregate:
                r.physical[0].expect(dict(tag=1, generation=0, boot=0, config=1, write=1, bytes=64))
            else:
                r.last[0]["drained"] = 0
            with self.assertRaisesRegex(Violation, "all-owner/independent"):
                r.marker(dict(name="end_case", ps=deadline))


class AddressTests(unittest.TestCase):
    def sample(self, ready=0, **changes):
        e = dict(kind="address", ps=100, port=0)
        for kind in ("aw", "ar"):
            e.update(
                {kind + "valid": 1, kind + "ready": ready, kind + "_bits": 17, kind + "_id": 32, kind + "_address": 512}
            )
        return e | changes

    def flags(self, e):
        return dict(
            ps=e["ps"], flags=(e["awvalid"] << 9) | (e["awready"] << 8) | (e["arvalid"] << 7) | (e["arready"] << 6)
        )

    def test_stable_pending_then_exactly_one_admission(self):
        a = Addresses()
        for ps in (100, 200):
            e = self.sample(ps=ps)
            a.sample(e, self.flags(e))
        e = self.sample(ready=1, ps=300)
        a.sample(e, self.flags(e))
        for kind in ("aw", "ar"):
            a.accept(dict(kind=kind, port=0, ps=300, id=32, address=512))
        self.assertFalse(a.held or a.offers)
        with self.assertRaisesRegex(Violation, "duplicate"):
            a.accept(dict(kind="aw", port=0, ps=300, id=32, address=512))

    def test_pending_withdrawal_or_any_packed_change_rejects(self):
        for changes in ({"awvalid": 0}, {"aw_bits": 18}, {"ar_bits": 18}):
            a = Addresses()
            e = self.sample()
            a.sample(e, self.flags(e))
            e = self.sample(ps=200, **changes)
            with self.assertRaisesRegex(Violation, "withdrawn/changed"):
                a.sample(e, self.flags(e))

    def test_missing_or_mismatched_source_edge_rejects(self):
        for flags in (None, dict(ps=99, flags=640), dict(ps=100, flags=0)):
            with self.assertRaises(Violation):
                Addresses().sample(self.sample(), flags)

    def test_probe_w_requires_all_address_handshakes(self):
        r = Run(profile_binding())
        r.case = dict(id=0, probe_handshakes={"0aw": 1})
        with self.assertRaisesRegex(Violation, "exactly one"):
            r.marker(dict(name="probes_accepted"))
        r.case["probe_handshakes"] = {f"{p}{kind}": 1 for p in range(2) for kind in ("aw", "ar")}
        r.marker(dict(name="probes_accepted"))
        self.assertTrue(r.case["probes_accepted"])


class ComposedAdapterTests(unittest.TestCase):
    def test_both_channel_recoveries_require_fresh_confirmation_in_current_case(self):
        r = Run(profile_binding())
        r.marker(dict(name="begin", case=1, failed=1, ps=0))
        for channel in range(2):
            # Isolate recovery observation from physical traffic. These are
            # synthetic samples, not an execution of destructive recovery.
            fields = dict(channel=channel, reset_n=0, trusted=0, config_valid=0, trained=0)
            for tick, changes in enumerate(
                (
                    dict(reset_request=1, confirmed=0),
                    dict(reset_request=1, confirmed=1),
                    dict(destructive=1, confirmed=1),
                )
            ):
                e = sample(ps=1250 + tick * 1250, tick=tick, **changes, **fields)
                r.sample(e)
                r.event(witness(e))
        self.assertEqual(r.recovery_channels, {0, 1})
        self.assertFalse(any(r.confirmation_low + r.confirmation_high))

    def test_old_or_unrequested_confirmation_cannot_prove_channel_recovery(self):
        for channel in range(2):
            r = Run(profile_binding())
            r.confirmation_low = r.confirmation_high = [True, True]
            r.marker(dict(name="begin", case=1, failed=1, ps=0))
            fields = dict(channel=channel, reset_n=0, trusted=0, config_valid=0, trained=0)
            for tick, confirmed in enumerate((0, 1)):
                e = sample(ps=1250 + tick * 1250, tick=tick, confirmed=confirmed, **fields)
                r.sample(e)
                r.event(witness(e))
            with self.subTest(channel=channel), self.assertRaisesRegex(Violation, "fresh confirmation"):
                r.sample(sample(ps=3750, tick=2, destructive=1, **fields))

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
        pending = None
        for tick, (raw, _, events) in enumerate(f.frames):
            e = sample(**{k: v for k, v in raw.items() if k in sample()})
            e.update(ps=1250 + tick * 1250, tick=tick)
            records += phase_records(e)
            r.sample(e)
            r.event(witness(e))
            admitted = next((v for v in events if v["kind"] == "inline_admit"), None)
            completed = next((v for v in events if v["kind"] == "inline_complete"), None)
            if admitted is not None:
                pending = host_request(**{k: v for k, v in admitted.items() if k != "kind"})
            h = host_edge(
                tick,
                [host_row(pending, source_generation=pending["generation"])] if pending is not None else [],
                [(0, pending)] if admitted else [],
                [(0, host_completion(pending, **{k: v for k, v in completed.items() if k != "kind"}))]
                if completed
                else [],
                reset_n=e["reset_n"],
                config_boot=e["boot"],
                config_epoch=e["config"],
            ) | dict(ps=e["ps"] + 625)
            records.append(h)
            r.event(h)
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
                    pending = None
        self.assertEqual(
            qualify_phases(
                records,
                dict(
                    version=4,
                    host_phase="posedge_before_NBA_next_edge_counter",
                    host_reset_phase="negedge_core_reset_before_NBA",
                    sample_phase="negedge_after_prepare_before_sample",
                    return_source="memory_output_after_prepare",
                    handshake_phase="posedge_before_NBA",
                    management_phase="posedge_before_NBA",
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
        advance(r, sample(ps=2500, tick=1, owner=1, phy_update=1))
        self.assertEqual(r.case["entries"], 1)

    def test_internal_settled_cannot_erase_source(self):
        for write in (False, True):
            r = entered()
            r.source.host.admit(Address(0, 1, 0, 3), write)
            with self.assertRaisesRegex(Violation, "actual W/R/B"):
                advance(r, sample(ps=2500, tick=1, owner=1, phy_update=1))

    def test_missing_physical_tail(self):
        r = entered()
        r.physical[0].expect(dict(tag=1, generation=0, boot=0, config=1, write=1, bytes=64))
        with self.assertRaisesRegex(Violation, "physical/row/WCK"):
            advance(r, sample(ps=2500, tick=1, owner=1, phy_update=1))

    def test_stale_recovery_and_training(self):
        r = entered()
        with self.assertRaisesRegex(Violation, "fresh confirmation/source settlement"):
            advance(r, sample(ps=2500, tick=1, destructive=1))
        r = entered()
        with self.assertRaisesRegex(Violation, "stale trusted epoch"):
            advance(r, sample(ps=2500, tick=1, training_epoch=1))

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
        advance(r, sample(ps=2500, tick=1, owner=1, master=1, phyupd_req=0, phymstr_req=1))
        with self.assertRaisesRegex(Violation, "fresh sampled rank"):
            advance(r, sample(ps=3750, tick=2, owner=2, phyupd_req=0, phymstr_req=0), phymstr_req=0)


if __name__ == "__main__":
    unittest.main()
