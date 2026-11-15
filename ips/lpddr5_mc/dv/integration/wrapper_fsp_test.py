"""Bounded synthetic contract tests; no HDL or saved hardware stream replay."""

import copy
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import patch

from contracts import commands
from system.host import Violation
from wrapper_fsp import (
    FSP_FIELDS,
    INDEX,
    OFFSETS,
    PCLK_PS,
    REGISTERS,
    FspRun,
    PublicationWitness,
    image_crc,
    profile_binding,
    profile_bits,
    qualify_fsp,
    target_profile,
    target_slot,
)
from wrapper_fsp_profiles import ProfilePublication, expected_program
from wrapper_management import PhysicalStream, Run


def apb(**changes):
    return (
        dict(
            ps=7000,
            psel=1,
            penable=1,
            pready=1,
            write=1,
            address=OFFSETS["DVFS"],
            data=257,
            strobe=15,
            error=0,
            settled=1,
            quiesced=1,
            enabled=0,
            valid=1,
            epoch=5,
            sequence=10,
            busy=0,
            boot=0,
        )
        | changes
    )


def frozen(c, **changes):
    return dict(ps=7000, channel=c, sequence=11, boot=0, image=1 << (32 * INDEX["DVFS"])) | changes


def transfer(c, phase=0, **changes):
    e = frozen(c)
    return (
        e
        | dict(
            prepare=int(phase == 0), commit=int(phase == 1), abort=int(phase == 2), dest=3, crc=image_crc(e["image"])
        )
        | changes
    )


def ack(c, phase=0, **changes):
    return dict(channel=c, sequence=11, boot=0, phase=phase, accepted=int(phase != 3)) | changes


def started(*, failed_case=False):
    w = PublicationWitness(5, failed_case=failed_case)
    w.observe_apb(apb(), True)
    for c in (0, 1):
        w.freeze(frozen(c))
        w.transfer(transfer(c))
    return w


def prepared():
    w = started()
    for c in (0, 1):
        w.ack(ack(c))
        w.ack(ack(c), apb=True)
    return w


def committed(c, **changes):
    return dict(channel=c, boot=0, mb_epoch=11, mb_dest=1 << c, mb_crc=19, config_crc=19) | changes


def timeout(c, **changes):
    return dict(channel=c, boot=0, prepared_epoch=11, prepared=1, fault=1, ready=0) | changes


def failed_events():
    events = []
    for c in (0, 1):
        events.extend((("timeout", timeout(c)), ("ack", ack(c, accepted=0)), ("apb_ack", ack(c, accepted=0))))
    for c in (0, 1):
        events.extend(
            (("transfer", transfer(c, 2)), ("abort", committed(c)), ("ack", ack(c, 2)), ("apb_ack", ack(c, 2)))
        )
    events.append(("apb", apb(psel=0, valid=0, busy=0, sequence=11)))
    return events


def consume_failure(w, events):
    for kind, e in events:
        if kind == "timeout":
            w.timed_out(e)
        elif kind == "apb_ack":
            w.ack(e, apb=True)
        elif kind == "apb":
            w.observe_apb(e, True)
        else:
            getattr(w, kind)(e)
    return w


def failed_publication():
    return consume_failure(started(failed_case=True), failed_events())


class PublicationTest(unittest.TestCase):
    def test_actual_accepted_edge_does_not_finalize_old_valid_image(self):
        w = started()
        self.assertIsNotNone(w.active)
        self.assertEqual(w.history, [])

    def test_both_channel_commit_and_final_apb_required(self):
        w = prepared()
        for c in (0, 1):
            w.transfer(transfer(c, 1))
            w.commit(committed(c))
            w.ack(ack(c, 1))
            w.ack(ack(c, 1), apb=True)
        w.observe_apb(apb(psel=0, valid=1, epoch=11), True)
        self.assertIsNone(w.active)
        self.assertEqual(w.epoch, 11)
        self.assertEqual(w.history[0]["commits"], {0, 1})

    def test_all_decline_retains_old_epoch(self):
        w = started()
        for c in (0, 1):
            w.ack(ack(c, 3))
            w.ack(ack(c, 3), apb=True)
        w.observe_apb(apb(psel=0), True)
        self.assertEqual(w.epoch, 5)
        self.assertEqual(w.history[0]["outcome"], "declined")

    def test_dirty_error_requires_independent_live_work(self):
        w = PublicationWitness(5)
        e = apb(error=1, settled=0, enabled=1, quiesced=0)
        w.observe_apb(e, False)
        self.assertEqual(w.rejected, 1)
        self.assertIsNone(w.active)
        for change in ({"epoch": 6}, {"busy": 1}, {"settled": 1}):
            with self.subTest(change=change), self.assertRaises(Violation):
                PublicationWitness(5).observe_apb(e | change, False)
        with self.assertRaises(Violation):
            PublicationWitness(5).observe_apb(e, True)

    def test_rejected_publication_has_unchanged_next_edge(self):
        for key in ("sequence", "epoch", "valid", "busy"):
            w = PublicationWitness(5)
            e = apb(error=1, settled=0, enabled=1, quiesced=0)
            w.observe_apb(e, False)
            with self.subTest(key=key), self.assertRaises(Violation):
                w.observe_apb(e | {"psel": 0, key: e[key] ^ 1}, False)

    def test_acceptance_requires_source_and_crossed_quiesce(self):
        for change, source in (({}, False), ({"settled": 0}, True), ({"enabled": 1, "quiesced": 0}, True)):
            with self.subTest(change=change), self.assertRaises(Violation):
                PublicationWitness(5).observe_apb(apb(**change), source)

    def test_payload_mutations_and_duplicate_transfer_rejected(self):
        for change in ({"sequence": 10}, {"boot": 1}, {"crc": 0}, {"image": 7}, {"dest": 1}, {"abort": 1}):
            w = started()
            with self.subTest(change=change), self.assertRaises(Violation):
                w.transfer(transfer(0, **change))
        with self.assertRaises(Violation):
            started().transfer(transfer(0))

    def test_freeze_must_match_actual_apb_edge_and_target(self):
        for change in ({"ps": 7001}, {"image": 3 << (32 * INDEX["DVFS"])}, {"sequence": 12}):
            w = PublicationWitness(5)
            w.observe_apb(apb(), True)
            with self.subTest(change=change), self.assertRaises(Violation):
                w.freeze(frozen(0, **change))

    def test_stale_or_duplicate_or_unbound_acks_rejected(self):
        for change in ({"sequence": 12}, {"boot": 1}, {"phase": 2}, {"accepted": 0}):
            with self.subTest(change=change), self.assertRaises(Violation):
                started().ack(ack(0, **change))
        with self.assertRaises(Violation):
            started().ack(ack(0), apb=True)
        w = started()
        w.ack(ack(0))
        with self.assertRaises(Violation):
            w.ack(ack(0))

    def test_commit_before_all_prepares_or_wrong_identity_rejected(self):
        with self.assertRaises(Violation):
            started().transfer(transfer(0, 1))
        for change in ({"mb_epoch": 12}, {"mb_dest": 3}, {"mb_crc": 20}, {"boot": 1}):
            w = prepared()
            w.transfer(transfer(0, 1))
            with self.subTest(change=change), self.assertRaises(Violation):
                w.commit(committed(0, **change))

    def test_partial_and_mixed_publication_rejected(self):
        w = prepared()
        w.transfer(transfer(0, 1))
        w.commit(committed(0))
        w.ack(ack(0, 1))
        w.ack(ack(0, 1), apb=True)
        with self.assertRaises(Violation):
            w.observe_apb(apb(psel=0, epoch=11), True)
        w = started()
        for c, phase in ((0, 0), (1, 3)):
            w.ack(ack(c, phase))
            w.ack(ack(c, phase), apb=True)
        with self.assertRaises(Violation):
            w.observe_apb(apb(psel=0), True)


class FailedPublicationTest(unittest.TestCase):
    def test_exact_negative_prepare_abort_roundtrip_settles_without_publication(self):
        w = failed_publication()
        w.require_failed()
        self.assertEqual(w.epoch, 5)
        self.assertIsNone(w.active)
        self.assertEqual([t["outcome"] for t in w.history], ["failed"])
        self.assertEqual(w.failure["aborts"], {0, 1})
        self.assertFalse(w.recover_requested)

    def test_negative_prepare_needs_its_own_selected_sampled_timeout(self):
        for w in (started(), started(failed_case=True)):
            with self.assertRaisesRegex(Violation, "sampled selected timeout"):
                w.ack(ack(0, accepted=0))
        w = started(failed_case=True)
        w.timed_out(timeout(0))
        with self.assertRaises(Violation):
            w.ack(ack(1, accepted=0))

    def test_timeout_must_bind_its_frozen_identity_and_fault_state(self):
        for change in ({"boot": 1}, {"prepared_epoch": 12}, {"fault": 0}, {"prepared": 0}, {"ready": 1}):
            w = started(failed_case=True)
            with self.subTest(change=change), self.assertRaises(Violation):
                w.timed_out(timeout(0, **change))
        w = started(failed_case=True)
        w.timed_out(timeout(0))
        with self.assertRaises(Violation):
            w.timed_out(timeout(0))
        with self.assertRaises(Violation):
            started().timed_out(timeout(0))

    def test_abort_needs_actual_crossed_negative_prepare(self):
        w = started(failed_case=True)
        w.timed_out(timeout(0))
        w.ack(ack(0, accepted=0))
        with self.assertRaisesRegex(Violation, "crossed selected negative"):
            w.transfer(transfer(0, 2))
        with self.assertRaises(Violation):
            started().transfer(transfer(0, 2))

    def test_selected_failure_rejects_commit_decline_and_final_publication(self):
        for phase in (0, 1, 3):
            with self.subTest(phase=phase), self.assertRaises(Violation):
                started(failed_case=True).ack(ack(0, phase))
        with self.assertRaises(Violation):
            started(failed_case=True).transfer(transfer(0, 1))
        with self.assertRaises(Violation):
            started(failed_case=True).commit(committed(0))
        for change in ({"valid": 1}, {"epoch": 11}, {"sequence": 12}):
            w = consume_failure(started(failed_case=True), failed_events()[:-1])
            with self.subTest(change=change), self.assertRaises(Violation):
                w.observe_apb(apb(psel=0, valid=0, sequence=11) | change, True)

    def test_every_fault_transfer_consumption_and_ack_is_required(self):
        events = failed_events()
        for missing in range(len(events)):
            with self.subTest(missing=events[missing]), self.assertRaises(Violation):
                w = consume_failure(started(failed_case=True), events[:missing] + events[missing + 1 :])
                w.require_failed()

    def test_abort_frozen_payload_and_mailbox_identity_cannot_change(self):
        prefix = failed_events()[:6]
        for change in ({"sequence": 12}, {"boot": 1}, {"crc": 1}, {"image": 0}, {"dest": 1}):
            w = consume_failure(started(failed_case=True), prefix)
            with self.subTest(change=change), self.assertRaises(Violation):
                w.transfer(transfer(0, 2, **change))
        for change in ({"mb_epoch": 12}, {"boot": 1}, {"mb_crc": 20}, {"mb_dest": 3}):
            w = consume_failure(started(failed_case=True), prefix)
            w.transfer(transfer(0, 2))
            with self.subTest(change=change), self.assertRaises(Violation):
                w.abort(committed(0, **change))

    def test_abort_acks_need_consumption_and_matching_clock_crossing(self):
        w = consume_failure(started(failed_case=True), failed_events()[:7])
        with self.assertRaises(Violation):
            w.ack(ack(0, 2))
        w.abort(committed(0))
        with self.assertRaises(Violation):
            w.ack(ack(0, 2), apb=True)
        w.ack(ack(0, 2))
        for change in ({"accepted": 0}, {"sequence": 12}, {"boot": 1}, {"phase": 1}):
            with self.subTest(change=change), self.assertRaises(Violation):
                w.ack(ack(0, 2) | change, apb=True)
        w.ack(ack(0, 2), apb=True)
        with self.assertRaises(Violation):
            w.ack(ack(0, 2), apb=True)
        with self.assertRaises(Violation):
            w.abort(committed(0))

    def test_late_second_negative_ack_can_follow_first_abort_offer(self):
        w = consume_failure(started(failed_case=True), failed_events()[:3])
        w.transfer(transfer(0, 2))
        consume_failure(w, failed_events()[3:6])
        w.abort(committed(0))
        w.ack(ack(0, 2))
        w.ack(ack(0, 2), apb=True)
        consume_failure(w, failed_events()[10:])
        w.require_failed()

    def test_recover_requires_idle_invalid_and_complete_both_channel_abort(self):
        recover = apb(address=OFFSETS["CONTROL"], data=8, valid=0, sequence=11)
        with self.assertRaises(Violation):
            started(failed_case=True).observe_apb(recover, True)
        for change, settled in (({"busy": 1}, True), ({"valid": 1}, True), ({"error": 1}, True), ({}, False)):
            w = failed_publication()
            with self.subTest(change=change, settled=settled), self.assertRaises(Violation):
                w.observe_apb(recover | change, settled)
        w = failed_publication()
        w.observe_apb(recover, True)
        self.assertTrue(w.recover_requested)
        with self.assertRaises(Violation):
            w.observe_apb(recover, True)

    def test_failed_publication_cannot_be_reused_or_revalidated(self):
        for e in (apb(), apb(psel=0, valid=1), apb(psel=0, valid=0, epoch=11)):
            with self.subTest(e=e), self.assertRaises(Violation):
                failed_publication().observe_apb(e, True)

    def run_fixture(self):
        p, q = profile_binding(), target_profile()
        physical = [PhysicalStream(copy.deepcopy(p)), PhysicalStream(copy.deepcopy(p))]
        for owner in physical:
            owner.sample(dict(tick=0, trusted=0, cs=0, ca=0, wck=0, write=0, read=0, **{"return": 0}))
        owner = ProfilePublication([p, p], physical, boot=0, epoch=5)
        owner.prepare(sequence=11, boot=0, target_fsp=1, targets=[q, q], source_settled=True)
        run = FspRun.__new__(FspRun)
        run.active, run.baseline, run.selection, run.failure_seen = True, False, 2, True
        run.failure_pending = None
        run.source = SimpleNamespace(settled=lambda: True)
        run.publication = started(failed_case=True)
        run.owner, run.physical = owner, physical
        run.fsp_edges = [dict(prepared=1, fault=1, ready=0), dict(prepared=1, fault=1, ready=0)]
        run.last = [dict(config_valid=1), dict(config_valid=1)]
        run.committed, run.origins = [0, 0], [0, 0]
        return run

    def test_dispatch_keeps_physical_memory_mr_and_maintenance_until_observed_reset(self):
        run = self.run_fixture()
        owner, physical = run.owner, run.physical
        physical[0].phy.dram.memory[0, 2, 3, 4] = 0xAD  # Seeded model input, not hardware retention evidence.
        physical[0].phy.dram.maintenance.raa[0][2] = 7
        physical[0].phy.dram.maintenance.extra[0] = True
        physical[0].phy.dram.mr[0][16] = 5
        physical[0].requests.append({"pending": "retained synthetic obligation"})
        before = copy.deepcopy([o.__dict__ for o in physical])
        identities = [
            (o, o.phy, o.phy.dram, o.phy.dram.memory, o.phy.dram.mr, o.phy.dram.maintenance) for o in physical
        ]
        for kind, e in failed_events():
            if kind == "timeout":
                run.publication.timed_out(e)
            elif kind == "abort":
                run.publication.abort(e)
                run.fsp_edges[e["channel"]]["prepared"] = 0
                run.last[e["channel"]]["config_valid"] = 0
            else:
                run.event(e | {"kind": "fsp_" + kind})
        self.assertIs(run.owner, owner)
        self.assertIs(run.physical, physical)
        for c, o in enumerate(physical):
            self.assertTrue(
                all(
                    a is b
                    for a, b in zip(
                        identities[c],
                        (o, o.phy, o.phy.dram, o.phy.dram.memory, o.phy.dram.mr, o.phy.dram.maintenance),
                        strict=True,
                    )
                )
            )
        # Compare the actual seeded state and pending obligation without relying
        # on model object equality or allowing ABORT to replace the owners.
        self.assertEqual(physical[0].phy.dram.memory, before[0]["phy"].dram.memory)
        self.assertEqual(physical[0].phy.dram.mr, before[0]["phy"].dram.mr)
        self.assertEqual(physical[0].phy.dram.maintenance.raa, before[0]["phy"].dram.maintenance.raa)
        self.assertEqual(physical[0].phy.dram.maintenance.extra, before[0]["phy"].dram.maintenance.extra)
        self.assertEqual(physical[0].requests, before[0]["requests"])
        self.assertIsNotNone(owner.transaction)
        self.assertFalse(run.recovery_allowed())
        with self.assertRaises(Violation):
            run.record_recovery({})
        run.event(apb(address=OFFSETS["CONTROL"], data=8, valid=0, sequence=11) | {"kind": "fsp_apb"})
        self.assertTrue(run.recovery_allowed())
        run.record_recovery({})  # Run invokes this only on RecoveryConsumption's witnessed transition.
        self.assertIsNone(run.owner)
        self.assertIs(run.physical, physical)  # Physical cancellation remains owned by Run.sample.

    def test_dispatch_abort_ack_requires_actual_config_invalidation(self):
        for prepared, valid in ((1, 0), (0, 1)):
            run = self.run_fixture()
            consume_failure(run.publication, failed_events()[:8])
            run.fsp_edges[0]["prepared"] = prepared
            run.last[0]["config_valid"] = valid
            with self.subTest(prepared=prepared, valid=valid), self.assertRaises(Violation):
                run.event(ack(0, 2) | {"kind": "fsp_ack"})

    def timeout_fixture(self, *, displacement=0, returned=False, wrong_target=False, missing_program=False):
        run = self.run_fixture()
        p, q = profile_binding(), target_profile()
        program, at = {}, 4
        for index, (address, value) in enumerate(expected_program(p, q, 0, 1)):
            first, second = commands.mode_write(address, value)
            program[at], program[at + 1] = first, second
            at += 202 if index == 18 else 14
        if missing_program:
            program.pop(4)
            program.pop(5)
        release = max(program) + 84
        fault = release + p["management_ck"]["tinit_complete"] + 1 + displacement
        edges = [
            dict(
                channel=0,
                tick=t,
                start=int(t < release),
                complete=int(t == 0 or returned and t > release),
                ratio=1,
                frequency=0,
                fsp=2 if wrong_target else 1,
                clock_done=int(t >= release - 1),
                wck_disabled=1,
                prepared=1,
                prepared_epoch=11,
                boot=0,
                ready=0,
                fault=int(t == fault),
                mb_prepare=0,
                mb_commit=0,
                mb_abort=0,
            )
            for t in range(fault + 1)
        ]
        run.controls, run.origins, run.faults = [edges[:-1], []], [0, 0], set()
        run.pin_samples = {(0, t): dict(tick=t, cs=int(t in program), ca=program.get(t, 0)) for t in range(fault + 1)}
        return run, edges[-1]

    def test_timeout_dispatch_binds_first_overdue_edge_target_and_full_program(self):
        run, edge = self.timeout_fixture()
        run.edge(edge)
        self.assertEqual(run.faults, {0})
        self.assertEqual(run.publication.active["faults"], {0})
        for change in (
            {"displacement": -1},
            {"displacement": 1},
            {"returned": True},
            {"wrong_target": True},
            {"missing_program": True},
        ):
            run, edge = self.timeout_fixture(**change)
            with self.subTest(change=change), self.assertRaises(Violation):
                run.edge(edge)
            self.assertEqual(run.publication.active["faults"], set())
        for change in ({"complete": 1}, {"start": 1}, {"clock_done": 0}, {"wck_disabled": 0}, {"fsp": 2}):
            run, edge = self.timeout_fixture()
            with self.subTest(change=change), self.assertRaises(Violation):
                run.edge(edge | change)
            self.assertEqual(run.publication.active["faults"], set())

    def test_case2_fresh_reads_use_recovered_marker(self):
        before = [
            "fsp_begin",
            "fsp_initial_preserved",
            "fsp_dirty",
            "fsp_dirty_rejected",
            "fsp_quiesced",
            "fsp_publish_offer",
            "fsp_failed",
            "fsp_recover_requested",
            "fsp_stale_confirmation_rejected",
            "fsp_recovery_baseline",
            "fsp_retrained",
        ]
        run = self.run_fixture()
        run.markers = list(before)
        run.source.host = SimpleNamespace(
            transactions={
                i: SimpleNamespace(done=True, write=False, address=SimpleNamespace(id=i)) for i in (190, 191, 192)
            }
        )
        run.marker(dict(name="fsp_recovered"))
        self.assertEqual(run.markers[-1], "fsp_recovered")
        run.markers = list(before)
        with self.assertRaises(Violation):
            run.marker(dict(name="fsp_preserved"))


class DeclinedMarkerPhaseTest(unittest.TestCase):
    """Synthetic unanimous decline; neither HDL nor saved streams execute."""

    def marker_fixture(self, channels=(0, 1)):
        p, q = profile_binding(), target_profile()
        physical, controls, samples = [], [], {}
        for c in (0, 1):
            owner, edges = PhysicalStream(copy.deepcopy(p)), []
            for tick in range(6):
                pin = dict(tick=tick, trusted=1, cs=0, ca=0, wck=0, write=0, read=0, **{"return": 0})
                owner.sample(pin)
                samples[c, tick] = pin
                edges.append(
                    dict(
                        tick=tick,
                        start=int(tick < 4),
                        complete=1,
                        frequency=0,
                        ratio=1,
                        fsp=1,
                        clock_done=0,
                        wck_disabled=1,
                        declined=int(tick == 4),
                    )
                )
            physical.append(owner)
            controls.append(edges)
        owner = ProfilePublication([p, p], physical, boot=0, epoch=5)
        owner.prepare(sequence=11, boot=0, target_fsp=1, targets=[q, q], source_settled=True)
        run = FspRun.__new__(FspRun)
        run.active, run.baseline, run.selection = True, False, 0
        run.failure_pending, run.decline_pending, run.decline_ack_ps = None, None, {}
        run.owner, run.physical, run.publication = owner, physical, started()
        run.source = SimpleNamespace(settled=lambda: True)
        run.controls, run.pin_samples, run.origins = controls, samples, [0, 0]
        run.last = [
            dict(config=5, boot=0, config_valid=1, trusted=1, prepared_valid=0, fault=0, host_fault=0) for _ in (0, 1)
        ]
        run.fsp_edges = [dict(epoch=5, boot=0, prepared=0, fault=0, mb_commit=0, mb_abort=0) for _ in (0, 1)]
        run.markers = [
            "fsp_begin",
            "fsp_initial_preserved",
            "fsp_dirty",
            "fsp_dirty_rejected",
            "fsp_quiesced",
            "fsp_publish_offer",
        ]
        for c in (0, 1):
            # Exercise real sampled DFI/pin/physical return and CK ACK gates.
            run.event(ack(c, 3, kind="fsp_ack", ps=13000))
        # No selected APB transfer: the old error payload is irrelevant.
        run.event(apb(kind="fsp_apb", ps=14000, psel=0, error=1, busy=1, valid=0, sequence=11))
        for c in channels:
            run.event(ack(c, 3, kind="fsp_apb_ack", ps=14000))
        return run, dict(name="fsp_declined", ps=14751, boot=0)

    def final_apb(self, **changes):
        # CONTROL setup is allowed; it is not a completed new publication.
        return (
            apb(
                kind="fsp_apb",
                ps=14000 + PCLK_PS,
                psel=1,
                penable=0,
                pready=0,
                address=OFFSETS["CONTROL"],
                data=1,
                sequence=11,
            )
            | changes
        )

    def test_early_marker_preserves_both_owners_until_real_next_pclk(self):
        run, marker = self.marker_fixture()
        owner, physical, transaction = run.owner, tuple(run.physical), run.owner.transaction
        run.marker(marker)
        self.assertIsNotNone(run.publication.active)
        self.assertIs(run.owner.transaction, transaction)
        self.assertEqual(run.publication.history, [])
        self.assertEqual(run.decline_pending, dict(ps=21000, sequence=11, boot=0, epoch=5))
        with self.assertRaisesRegex(Violation, "current common publication"):
            owner.service(host_epoch=5, channel_epochs=[5, 5])
        with patch.object(Run, "event"):
            run.event(dict(kind="host_settlement", ps=15000, fatal=0))
        run.event(self.final_apb())
        self.assertIsNone(run.decline_pending)
        self.assertIsNone(run.publication.active)
        self.assertIsNone(owner.transaction)
        self.assertIs(run.owner, owner)
        self.assertTrue(all(a is b for a, b in zip(physical, run.physical, strict=True)))
        self.assertEqual(run.publication.epoch, 5)
        self.assertEqual([t["outcome"] for t in run.publication.history], ["declined"])
        owner.service(host_epoch=5, channel_epochs=[5, 5])

    def test_already_finalized_decline_marker_stays_valid(self):
        run, marker = self.marker_fixture()
        run.event(self.final_apb())
        run.marker(marker | {"ps": 21001})
        self.assertIsNone(run.decline_pending)

    def test_partial_mixed_or_missing_acks_cannot_open_pending_interval(self):
        for channels in ((), (0,), (1,)):
            run, marker = self.marker_fixture(channels)
            with self.subTest(channels=channels), self.assertRaises(Violation):
                run.marker(marker)
            self.assertIsNone(run.decline_pending)
        for clock in ("ck_acks", "apb_acks"):
            for value in (None, 1):
                run, marker = self.marker_fixture()
                if value is None:
                    run.publication.active[clock].pop((1, 3))
                else:
                    run.publication.active[clock][1, 3] = value
                with self.subTest(clock=clock, value=value), self.assertRaises(Violation):
                    run.marker(marker)

    def test_stale_duplicate_or_invalid_apb_ack_is_rejected_before_tracking(self):
        for changes in ({"sequence": 12}, {"boot": 1}, {"accepted": 1}, {"phase": 0}):
            run, _ = self.marker_fixture((0,))
            with self.subTest(changes=changes), self.assertRaises(Violation):
                run.event(ack(1, 3, kind="fsp_apb_ack", ps=14000) | changes)
            self.assertEqual(run.decline_ack_ps, {0: 14000})
        run, _ = self.marker_fixture()
        with self.assertRaisesRegex(Violation, "duplicate FSP ACK"):
            run.event(ack(1, 3, kind="fsp_apb_ack", ps=14000))

    def test_marker_requires_current_final_ack_edge_and_one_pclk_window(self):
        mutations = [
            ("marker", {"ps": 13999}),
            ("marker", {"ps": 21000}),
            ("marker", {"boot": 1}),
            ("ack", {1: 7000}),
        ]
        mutations += [
            ("apb", {k: v})
            for k, v in (
                ("sequence", 12),
                ("boot", 1),
                ("epoch", 6),
                ("valid", 1),
                ("busy", 0),
                ("settled", 0),
                ("enabled", 1),
            )
        ]
        for kind, changes in mutations:
            run, marker = self.marker_fixture()
            if kind == "marker":
                marker.update(changes)
            elif kind == "ack":
                run.decline_ack_ps.update(changes)
            else:
                run.publication.apb.update(changes)
                if "enabled" in changes:
                    run.publication.apb["quiesced"] = 0
            with self.subTest(kind=kind, changes=changes), self.assertRaises(Violation):
                run.marker(marker)
            self.assertIsNone(run.decline_pending)

    def test_marker_requires_real_retained_channels_and_physical_decline(self):
        for changes in ({"sequence": 12}, {"ready": {0: "declined"}}, {"committed": {0}}):
            run, marker = self.marker_fixture()
            run.owner.transaction.update(changes)
            with self.subTest(physical=changes), self.assertRaises(Violation):
                run.marker(marker)
        for collection, changes in (
            ("last", {"config": 6}),
            ("last", {"boot": 1}),
            ("last", {"prepared_valid": 1}),
            ("last", {"config_valid": 0}),
            ("last", {"trusted": 0}),
            ("last", {"fault": 1}),
            ("last", {"host_fault": 1}),
            ("fsp_edges", {"prepared": 1}),
            ("fsp_edges", {"fault": 1}),
            ("fsp_edges", {"mb_commit": 1}),
            ("fsp_edges", {"mb_abort": 1}),
        ):
            run, marker = self.marker_fixture()
            getattr(run, collection)[1].update(changes)
            with self.subTest(collection=collection, changes=changes), self.assertRaises(Violation):
                run.marker(marker)
        run, marker = self.marker_fixture()
        run.source.settled = lambda: False
        with self.assertRaises(Violation):
            run.marker(marker)

    def test_actual_final_apb_requires_exact_next_pclk_and_retained_valid_idle(self):
        for changes in (
            {"ps": 20999},
            {"ps": 21001},
            {"busy": 1},
            {"valid": 0},
            {"epoch": 11},
            {"epoch": 6},
            {"sequence": 12},
            {"boot": 1},
            {"settled": 0},
            {"enabled": 1, "quiesced": 0},
        ):
            run, marker = self.marker_fixture()
            run.marker(marker)
            with self.subTest(changes=changes), self.assertRaises(Violation):
                run.event(self.final_apb(**changes))
            self.assertIsNotNone(run.decline_pending)
            self.assertIsNotNone(run.publication.active)
            self.assertIsNotNone(run.owner.transaction)

    def test_final_witness_cannot_hide_new_fault_or_prepared_epoch_change(self):
        for collection, changes in (
            ("last", {"fault": 1}),
            ("last", {"host_fault": 1}),
            ("last", {"config": 6}),
            ("last", {"prepared_valid": 1}),
            ("fsp_edges", {"fault": 1}),
        ):
            run, marker = self.marker_fixture()
            run.marker(marker)
            getattr(run, collection)[0].update(changes)
            with self.subTest(collection=collection, changes=changes), self.assertRaises(Violation):
                run.event(self.final_apb())
        run, marker = self.marker_fixture()
        run.marker(marker)
        run.source.settled = lambda: False
        with self.assertRaises(Violation):
            run.event(self.final_apb())

    def test_missing_final_pclk_source_acceptance_markers_and_finish_rejected(self):
        for kind in ("aw", "ar"):
            run, marker = self.marker_fixture()
            run.marker(marker)
            with self.subTest(kind=kind), self.assertRaisesRegex(Violation, "source service before final"):
                run.event(dict(kind=kind, ps=15000))
        for name in ("fsp_declined", "fsp_published", "fsp_preserved", "fsp_end"):
            run, marker = self.marker_fixture()
            run.marker(marker)
            with self.subTest(name=name), self.assertRaisesRegex(Violation, "pending final decline PCLK"):
                run.marker(marker | {"name": name})
        run, marker = self.marker_fixture()
        run.marker(marker)
        with self.assertRaisesRegex(Violation, "missing next-PCLK"):
            run.event(dict(kind="fsp_binding", ps=21001))
        with self.assertRaisesRegex(Violation, "unfinished next-PCLK"):
            run.finish()

    def test_published_marker_still_requires_actual_host_installation(self):
        run, _ = self.marker_fixture()
        run.selection = 1
        run.publication.active = None
        with self.assertRaisesRegex(Violation, "final publication/host installation"):
            run.marker(dict(name="fsp_published", ps=14751, boot=0))


class PublishedMarkerPhaseTest(unittest.TestCase):
    """Synthetic config-consuming edge and following installed-state witness."""

    def fixture(self):
        p = profile_binding()
        physical = [PhysicalStream(copy.deepcopy(p)), PhysicalStream(copy.deepcopy(p))]
        owner = ProfilePublication([p, p], physical, boot=0, epoch=5)
        owner.transaction = dict(sequence=11, boot=0, committed={0, 1}, targets=[p, p], target_fsp=1)
        w = prepared()
        for c in (0, 1):
            w.transfer(transfer(c, 1))
            w.commit(committed(c))
            w.ack(ack(c, 1))
            w.ack(ack(c, 1), apb=True)
        w.observe_apb(apb(psel=0, valid=1, epoch=11), True)
        run = FspRun.__new__(FspRun)
        run.p, run.owner, run.publication = p, owner, w
        run.active, run.baseline, run.selection, run.failure_seen = True, False, 1, False
        run.failure_pending = run.decline_pending = run.published_pending = None
        run.source = SimpleNamespace(settled=lambda: True)
        run.markers = [
            "fsp_begin",
            "fsp_initial_preserved",
            "fsp_dirty",
            "fsp_dirty_rejected",
            "fsp_quiesced",
            "fsp_publish_offer",
        ]
        run.host_edge = dict(
            kind="host_edge",
            ps=100625,
            tick=10,
            reset_n=1,
            config_valid=1,
            config_epoch=5,
            config_boot=0,
            cfg_valid=1,
            cfg_ready=1,
            cfg_epoch=11,
            cfg_boot=0,
            settled=1,
            fatal=0,
            fault=0,
            enable=0,
        )
        run.last = [
            dict(
                channel=c,
                tick=11,
                ps=101250,
                boot=0,
                config=11,
                config_valid=1,
                settled=1,
                enabled=0,
                host_fault=0,
                prepared_valid=0,
            )
            for c in (0, 1)
        ]
        marker = dict(name="fsp_published", ps=101251, boot=0)
        installed = run.host_edge | dict(ps=101875, tick=11, config_epoch=11, cfg_valid=0)
        return run, marker, installed

    def test_marker_keeps_actual_owner_live_until_next_host_edge(self):
        run, marker, installed = self.fixture()
        owner, transaction, physical = run.owner, run.owner.transaction, tuple(run.owner.physical)
        run.marker(marker)
        self.assertIs(owner.transaction, transaction)
        with self.assertRaisesRegex(Violation, "current common publication"):
            owner.service(host_epoch=11, channel_epochs=[11, 11])
        with patch.object(Run, "event"):
            run.event(dict(kind="host_settlement", ps=101875, fatal=0))
            run.event(installed)
        self.assertIs(run.owner, owner)
        self.assertTrue(all(a is b for a, b in zip(owner.physical, physical, strict=True)))
        self.assertIsNone(run.published_pending)
        owner.service(host_epoch=11, channel_epochs=[11, 11])

    def test_already_observed_installation_needs_no_pending_edge(self):
        run, marker, installed = self.fixture()
        with patch.object(Run, "event"):
            run.event(installed)
        run.marker(marker | dict(ps=102501))
        self.assertIsNone(run.published_pending)

    def test_missing_or_unaccepted_config_handshake_rejects(self):
        for changes in (
            dict(cfg_valid=0),
            dict(cfg_ready=0),
            dict(cfg_epoch=10),
            dict(cfg_boot=1),
            dict(reset_n=0),
            dict(fatal=1),
            dict(fault=1),
            dict(enable=1),
            dict(settled=0),
            dict(config_epoch=11),
        ):
            run, marker, _ = self.fixture()
            run.host_edge.update(changes)
            with self.subTest(changes=changes), self.assertRaises(Violation):
                run.marker(marker)

    def test_partial_final_publication_or_commit_rejects(self):
        for change in ("active", "unpublished", "partial_commit", "wrong_boot"):
            run, marker, _ = self.fixture()
            if change == "active":
                run.publication.active = {}
            elif change == "unpublished":
                run.publication.history[-1]["outcome"] = "declined"
            elif change == "partial_commit":
                run.owner.transaction["committed"] = {0}
            else:
                marker["boot"] = 1
            with self.subTest(change=change), self.assertRaises(Violation):
                run.marker(marker)

    def test_unpaired_channel_epoch_phase_or_open_fence_rejects(self):
        for changes in (
            dict(channel=0),
            dict(tick=12),
            dict(ps=101249),
            dict(boot=1),
            dict(config=10),
            dict(config_valid=0),
            dict(settled=0),
            dict(enabled=1),
            dict(host_fault=1),
            dict(prepared_valid=1),
        ):
            run, marker, _ = self.fixture()
            run.last[1].update(changes)
            with self.subTest(changes=changes), self.assertRaises(Violation):
                run.marker(marker)
        for ps in (101249, 101875):
            run, marker, _ = self.fixture()
            with self.subTest(ps=ps), self.assertRaises(Violation):
                run.marker(marker | dict(ps=ps))

    def test_missing_early_late_faulted_or_stale_next_edge_rejects(self):
        for changes in (
            dict(ps=101874),
            dict(ps=101876),
            dict(tick=12),
            dict(config_valid=0),
            dict(config_epoch=5),
            dict(config_boot=1),
            dict(reset_n=0),
            dict(settled=0),
            dict(fatal=1),
            dict(fault=1),
            dict(enable=1),
            dict(cfg_valid=1),
        ):
            run, marker, installed = self.fixture()
            run.marker(marker)
            with self.subTest(changes=changes), patch.object(Run, "event"), self.assertRaises(Violation):
                run.event(installed | changes)

    def test_admission_markers_and_finish_cannot_hide_pending_installation(self):
        for kind in ("aw", "ar"):
            run, marker, _ = self.fixture()
            run.marker(marker)
            with self.subTest(kind=kind), self.assertRaisesRegex(Violation, "source service before final"):
                run.event(dict(kind=kind, ps=101800))
        for name in ("fsp_publish_offer", "fsp_preserved", "fsp_end"):
            run, marker, _ = self.fixture()
            run.marker(marker)
            with self.subTest(name=name), self.assertRaisesRegex(Violation, "pending host installation"):
                run.marker(marker | dict(name=name))
        run, marker, _ = self.fixture()
        run.marker(marker)
        with self.assertRaisesRegex(Violation, "missing next-edge"):
            run.event(dict(kind="fsp_binding", ps=101876))
        with self.assertRaisesRegex(Violation, "unfinished next-edge"):
            run.finish()


class FailedMarkerPhaseTest(unittest.TestCase):
    """Synthetic phase boundary; saved producer streams are not replayed."""

    def marker_fixture(self):
        run = FspRun.__new__(FspRun)
        run.active, run.baseline, run.selection, run.failure_seen = True, False, 2, False
        run.failure_pending = None
        run.p = profile_binding()
        run.faults = {0, 1}
        run.markers = [
            "fsp_begin",
            "fsp_initial_preserved",
            "fsp_dirty",
            "fsp_dirty_rejected",
            "fsp_quiesced",
            "fsp_publish_offer",
        ]
        run.last = [dict(ps=36138750, tick=28910, channel=c, host_fault=1, enabled=0) for c in range(2)]
        run.publication = SimpleNamespace(failure=object(), recover_requested=True)
        run.source = SimpleNamespace(host=SimpleNamespace(fail=lambda: None))
        return run, dict(name="fsp_failed", ps=36138751)

    def test_sampled_fence_marker_precedes_consuming_host_fatal_witness(self):
        run, marker = self.marker_fixture()
        run.marker(marker)
        self.assertEqual(run.markers[-1], "fsp_failed")
        self.assertFalse(run.failure_seen)
        self.assertEqual(run.failure_pending, (28910, 36139375))
        self.assertFalse(run.recovery_allowed())
        calls = []
        run.source.host.fail = lambda: calls.append("sampled host fatal")
        with patch.object(Run, "event"):
            run.event(dict(kind="host_settlement", ps=36139375, tick=28910, fatal=1))
            run.event(dict(kind="host_settlement", ps=36140625, tick=28911, fatal=1))
        self.assertTrue(run.failure_seen)
        self.assertIsNone(run.failure_pending)
        self.assertTrue(run.recovery_allowed())
        self.assertEqual(calls, ["sampled host fatal"])

    def test_marker_requires_both_sampled_engine_timeouts(self):
        for faults in (set(), {0}, {1}):
            run, marker = self.marker_fixture()
            run.failure_seen, run.faults = True, faults
            with self.subTest(faults=faults), self.assertRaises(Violation):
                run.marker(marker)

    def test_consumed_fatal_cannot_replace_both_sampled_closed_wrapper_fences(self):
        for channel in (0, 1):
            for changes in (
                {"host_fault": 0},
                {"enabled": 1},
                {"ps": 36138752},
                {"channel": 1 - channel},
                {"tick": 28909},
                {"ps": 36137500},
            ):
                run, marker = self.marker_fixture()
                run.failure_seen = True
                run.last[channel].update(changes)
                with self.subTest(channel=channel, changes=changes), self.assertRaises(Violation):
                    run.marker(marker)
            run, marker = self.marker_fixture()
            run.failure_seen, run.last[channel] = True, None
            with self.subTest(channel=channel, missing=True), self.assertRaises(Violation):
                run.marker(marker)

    def test_missing_or_wrong_receiving_fatal_is_rejected(self):
        for changes in ({"fatal": 0}, {"tick": 28911}, {"ps": 36139374}):
            run, marker = self.marker_fixture()
            run.marker(marker)
            edge = dict(kind="host_settlement", ps=36139375, tick=28910, fatal=1) | changes
            with (
                self.subTest(changes=changes),
                patch.object(Run, "event"),
                self.assertRaisesRegex(Violation, "not consumed at next qualified host edge"),
            ):
                run.event(edge)
            self.assertFalse(run.failure_seen)
            self.assertFalse(run.recovery_allowed())

    def test_late_fatal_cannot_discharge_next_edge_obligation(self):
        run, marker = self.marker_fixture()
        run.marker(marker)
        with patch.object(Run, "event"), self.assertRaisesRegex(Violation, "missing next-edge FSP host fatal"):
            run.event(dict(kind="host_settlement", ps=36140625, tick=28911, fatal=1))
        self.assertFalse(run.failure_seen)
        self.assertFalse(run.recovery_allowed())

    def test_omitted_receiving_record_is_rejected_when_time_advances(self):
        run, marker = self.marker_fixture()
        run.marker(marker)
        with patch.object(Run, "event"), self.assertRaisesRegex(Violation, "missing next-edge FSP host fatal"):
            run.event(dict(kind="sample", ps=36140000, tick=28911, channel=0))
        self.assertFalse(run.failure_seen)

    def test_recovery_marker_cannot_substitute_for_consuming_fatal(self):
        run, marker = self.marker_fixture()
        run.marker(marker)
        with self.assertRaisesRegex(Violation, "RECOVER marker without settled ABORT"):
            run.marker(dict(name="fsp_recover_requested"))

    def test_finish_requires_consumed_fatal_and_no_pending_receiving_witness(self):
        for consumed_fatal in (False, True):
            run, marker = self.marker_fixture()
            run.marker(marker)
            run.failure_seen = consumed_fatal
            self.assertFalse(run.recovery_allowed())
            run.finished, run.baseline, run.recovery_channels = True, True, {0, 1}
            run.markers += ["fsp_retrained", "fsp_end"]
            run.source.host.finish = lambda: None
            run.addresses = SimpleNamespace(held={}, offers={})
            run.publication = SimpleNamespace(history=[dict(outcome="failed")], require_failed=lambda: None)
            with (
                self.subTest(consumed_fatal=consumed_fatal),
                self.assertRaisesRegex(Violation, "incomplete FSP fatal recovery"),
            ):
                run.finish()


class ProfileAndSchemaTest(unittest.TestCase):
    def test_profile_pack_binds_every_declared_capability_and_timing(self):
        p, q = profile_binding(), target_profile()
        self.assertEqual((p["ratio"], q["ratio"], p["ck_ps"], q["ck_ps"]), (4, 2, 1250, 1250))
        self.assertNotEqual(profile_bits(p), profile_bits(q))
        for key in ("ratio", "ranks", "rl_ck", "wl_ck"):
            changed = copy.deepcopy(p)
            changed[key] ^= 1
            self.assertNotEqual(profile_bits(p), profile_bits(changed))
        changed = copy.deepcopy(p)
        changed["mr"]["16"] ^= 16
        self.assertNotEqual(profile_bits(p), profile_bits(changed))
        changed = copy.deepcopy(p)
        changed["management_ck"]["tinit_complete"] += 1
        self.assertNotEqual(profile_bits(p), profile_bits(changed))

    def test_crc_uses_full_packed_word_order(self):
        raw = 0x9988776655443322
        self.assertEqual(image_crc(raw), zlib.crc32(raw.to_bytes(4 * len(REGISTERS), "little")))
        self.assertNotEqual(image_crc(raw), image_crc(raw ^ (1 << (32 * (len(REGISTERS) - 1)))))
        self.assertEqual(target_slot(2 << (32 * INDEX["DVFS"])), 2)

    def records(self):
        records = []
        for t in range(3):
            for c in (0, 1):
                records.append(dict(kind="management_edge", channel=c, tick=t, ps=625 + 1250 * t, boot=0, config=5))
                e = dict.fromkeys(FSP_FIELDS, 0) | dict(kind="fsp_edge", channel=c, tick=t, ps=625 + 1250 * t, epoch=5)
                records.append(e)
        records.extend(dict(kind="fsp_apb", ps=3500 + n * 7000) for n in range(3))
        return records

    def test_full_ck_and_pclk_coverage(self):
        self.assertEqual(
            qualify_fsp(
                self.records(),
                dict(phase="posedge_before_NBA", apb_phase="pclk_posedge_before_NBA", register_count=len(REGISTERS)),
            ),
            6,
        )

    def test_missing_duplicate_reordered_misphased_and_stale_edges_rejected(self):
        binding = dict(phase="posedge_before_NBA", apb_phase="pclk_posedge_before_NBA", register_count=len(REGISTERS))
        original = self.records()
        mutants = [original[:1] + original[2:], original + [original[1]], [original[3], original[1]] + original[4:]]
        for key, value in (("ps", 626), ("epoch", 6), ("boot", 1)):
            m = copy.deepcopy(original)
            m[1][key] = value
            mutants.append(m)
        m = copy.deepcopy(original)
        del m[1]["clock_done"]
        mutants.append(m)
        m = copy.deepcopy(original)
        m[-1]["ps"] += 1
        mutants.append(m)
        for i, records in enumerate(mutants):
            with self.subTest(i=i), self.assertRaises(Violation):
                qualify_fsp(records, binding)

    def test_default_run_profile_hook_stays_strict(self):
        run = Run.__new__(Run)
        run.profile_image = None
        run.validate_profile(dict(profile_image="0x1"))
        with self.assertRaises(Violation):
            run.validate_profile(dict(profile_image="0x2"))
        run.case = None
        self.assertFalse(run.recovery_allowed())
        with self.assertRaises(Violation):
            run.record_recovery({})

    def test_fsp_memory_image_can_change_only_after_observed_commit(self):
        run = FspRun.__new__(FspRun)
        run.images = [profile_bits(profile_binding()), profile_bits(target_profile()), profile_bits(profile_binding())]
        run.committed = [0, 0]
        run.validate_profile(dict(channel=0, profile_image=run.images[0]))
        with self.assertRaises(Violation):
            run.validate_profile(dict(channel=0, profile_image=run.images[1]))
        run.committed[0] = 1
        run.validate_profile(dict(channel=0, profile_image=run.images[1]))
        with self.assertRaises(Violation):
            run.validate_profile(dict(channel=1, profile_image=run.images[1]))


if __name__ == "__main__":
    unittest.main()
