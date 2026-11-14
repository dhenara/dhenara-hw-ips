"""Bounded synthetic contract tests; no HDL or saved hardware stream replay."""

import copy
import unittest
import zlib

from system.host import Violation
from wrapper_fsp import (
    FSP_FIELDS,
    INDEX,
    OFFSETS,
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
from wrapper_management import Run


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
        e | dict(prepare=int(phase == 0), commit=int(phase == 1), abort=0, dest=3, crc=image_crc(e["image"])) | changes
    )


def ack(c, phase=0, **changes):
    return dict(channel=c, sequence=11, boot=0, phase=phase, accepted=int(phase != 3)) | changes


def started():
    w = PublicationWitness(5)
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
