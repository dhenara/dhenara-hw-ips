"""Regression of the three predecessor review failures and adjacent boundaries."""

import json
import unittest
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path

from contracts.configuration import RegisterBank, example_configuration, register_image, validate_register_image
from contracts.integrity import WriteEpoch
from contracts.lifecycle import Lifecycle, Maintenance
from contracts.profiles import admit, waveform
from contracts.waveform_oracle import check, check_data_cs_sequence

IP = Path(__file__).resolve().parents[2]
PROFILES = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"]
SCHEMA = json.loads((IP / "config/contracts/registers.json").read_text())


class WriteCompletionTests(unittest.TestCase):
    def test_pin_completion_edges_and_independent_finish_order(self):
        epoch = WriteEpoch(posted=True)
        epoch.add(99, at=0, pin_end=30)
        epoch.add(100, at=1, pin_end=20)
        self.assertEqual(epoch.responses, {})
        for at in (1, 19):
            with self.assertRaisesRegex(ValueError, "not eligible"):
                epoch.acknowledge(100, at)
        self.assertEqual(epoch.offer(100, 20), "OKAY")
        self.assertEqual(epoch.acknowledged, {})
        self.assertEqual(epoch.acknowledge(100, 21), "OKAY")
        with self.assertRaises(ValueError):
            epoch.offer(99, 29)
        self.assertEqual(epoch.offer(99, 30), "OKAY")
        self.assertEqual(epoch.acknowledge(99, 31), "OKAY")
        with self.assertRaises(ValueError):
            epoch.acknowledge(99, 32)
        self.assertEqual(epoch.acknowledged, {100: "OKAY", 99: "OKAY"})

    def test_loss_before_at_after_completion_without_exposure(self):
        for at in (29, 30, 31):
            with self.subTest(loss_at=at):
                epoch = WriteEpoch(posted=True)
                epoch.add(1, 0, 30)
                self.assertEqual(epoch.fail(at), {1: "SLVERR"})
                self.assertFalse(epoch.late_error)
                epoch.advance(100)
                self.assertEqual(epoch.acknowledge(1, 100), "SLVERR")
                with self.assertRaises(ValueError):
                    epoch.add(2, 101, 120)
                with self.assertRaises(ValueError):
                    epoch.poll(101)

    def test_loss_distinguishes_admitted_eligible_offered_acknowledged(self):
        epoch = WriteEpoch(posted=True)
        for tag, end in [(1, 40), (2, 20), (3, 20), (4, 20)]:
            epoch.add(tag, 0, end)
        epoch.offer(3, 20)
        epoch.acknowledge(4, 20)
        self.assertEqual(epoch.fail(30), {1: "SLVERR", 2: "SLVERR", 3: "OKAY", 4: "OKAY"})
        self.assertEqual(epoch.acknowledged, {4: "OKAY"})
        self.assertEqual(epoch.offered, {3: "OKAY"})
        self.assertTrue(epoch.late_error)
        self.assertEqual(epoch.acknowledge(3, 31), "OKAY")
        self.assertEqual(epoch.acknowledge(2, 31), "SLVERR")
        self.assertEqual(epoch.acknowledge(1, 31), "SLVERR")

    def test_mr43_failure_and_clear_preserve_response_backpressure(self):
        for posted in (False, True):
            with self.subTest(posted=posted):
                epoch = WriteEpoch(posted=posted)
                for tag in (1, 2, 3):
                    epoch.add(tag, 0, 30)
                if posted:
                    epoch.offer(2, 30)
                    epoch.acknowledge(3, 30)
                else:
                    with self.assertRaises(ValueError):
                        epoch.offer(2, 30)
                epoch.poll(30)
                with self.assertRaises(ValueError):
                    epoch.poll(31)
                with self.assertRaises(ValueError):
                    epoch.add(4, 31, 32)
                with self.assertRaises(ValueError):
                    epoch.settle(49, 128, 8, 12)
                self.assertEqual(
                    epoch.settle(50, 128, 8, 12),
                    {1: "SLVERR", 2: "OKAY" if posted else "SLVERR", 3: "OKAY" if posted else "SLVERR"},
                )
                self.assertEqual(epoch.late_error, posted)
                self.assertEqual(epoch.acknowledge(1, 50), "SLVERR")
                self.assertEqual(epoch.acknowledge(2, 50), "OKAY" if posted else "SLVERR")
                if not posted:
                    self.assertEqual(epoch.acknowledge(3, 50), "SLVERR")
                epoch.add(4, 50, 60)

    def test_strict_success_waits_for_clean_mr43_and_actual_handshake(self):
        epoch = WriteEpoch()
        epoch.add(1, 0, 30)
        epoch.advance(30)
        self.assertEqual(epoch.completed, {1})
        self.assertEqual(epoch.responses, {})
        epoch.poll(256)
        epoch.settle(276, 0, 8, 12)
        self.assertEqual(epoch.responses, {1: "OKAY"})
        self.assertEqual(epoch.acknowledged, {})
        self.assertEqual(epoch.acknowledge(1, 277), "OKAY")

    def test_deadlines_time_reversal_limits_and_duplicate_admission(self):
        epoch = WriteEpoch(posted=True, limit=1)
        epoch.add(1, 10, 30)
        for args in [(1, 10, 30), (2, 9, 30), (2, 10, 30)]:
            with self.assertRaises(ValueError):
                epoch.add(*args)
        with self.assertRaises(ValueError):
            epoch.poll(267)
        epoch.advance(30)
        with self.assertRaises(ValueError):
            epoch.advance(29)
        self.assertEqual(epoch.responses, {1: "OKAY"})
        self.assertEqual(epoch.acknowledged, {})


class DestructiveRecoveryTests(unittest.TestCase):
    def booted(self):
        lifecycle = Lifecycle()
        lifecycle.boot(0, complete=True, boot_state="IDLE", training_epoch=0, profile_epoch=1)
        lifecycle.enable()
        return lifecycle

    def test_refresh_fault_reset_fresh_boot_enable_and_new_refresh(self):
        lifecycle = self.booted()
        with self.assertRaisesRegex(ValueError, "missed periodic refresh"):
            lifecycle.maintenance.advance(3125)
        lifecycle.fail()
        lifecycle.recover(host_settled=True, reset_confirmed=True)
        with self.assertRaises(ValueError):
            lifecycle.enable()
        for training, profile in [(0, 2), (1, 1), (1, 0)]:
            with self.assertRaises(ValueError):
                lifecycle.boot(4000, complete=True, boot_state="IDLE", training_epoch=training, profile_epoch=profile)
        lifecycle.boot(4000, complete=True, boot_state="IDLE", training_epoch=1, profile_epoch=2)
        lifecycle.enable()
        self.assertEqual((lifecycle.state, lifecycle.owner, lifecycle.trusted), ("RUN", "MC", True))
        self.assertFalse(lifecycle.maintenance.fault)
        lifecycle.maintenance.advance(7000)
        for rank in range(2):
            lifecycle.maintenance.refresh(rank)
        lifecycle.request("CTRL_UPDATE", 7000, 32)
        lifecycle.finish(7010)
        lifecycle.enable()
        self.assertEqual(lifecycle.state, "RUN")

    def test_reset_all_volatile_state_keep_independent_diagnostics(self):
        lifecycle = self.booted()
        m = lifecycle.maintenance
        m.hold_sr(0)
        m.activate(1, 3)
        m.refresh(1, pair=0)
        m.exit_sr(1)
        m.credit, m.debt, m.thermal, m.interval = 3, 1, 15, 390
        m.fault = True
        lifecycle.pending.add(42)
        lifecycle.write_end = 9000
        lifecycle.ecc_dirty = True
        lifecycle.transfer_at = 3
        lifecycle.kind = "DVFS"
        lifecycle.target_fsp = 2
        lifecycle.maximum = 32
        lifecycle.active_fsp = 1
        lifecycle.retention_deadline_ps = 12345
        lifecycle.fail()
        retained = deepcopy(lifecycle.fault_diagnostics)
        for host, reset in [(False, False), (True, False), (False, True)]:
            with self.assertRaises(ValueError):
                lifecycle.recover(host, reset)
        self.assertIs(lifecycle.maintenance, m)
        lifecycle.fail()
        self.assertEqual(lifecycle.fault_diagnostics, retained)
        lifecycle.recover(True, True)
        self.assertEqual(asdict(lifecycle.maintenance), asdict(Maintenance()))
        self.assertIsNot(lifecycle.maintenance, m)
        self.assertEqual(lifecycle.fault_diagnostics, retained)
        self.assertEqual(lifecycle.pending, set())
        self.assertEqual(lifecycle.write_end, 0)
        self.assertFalse(lifecycle.ecc_dirty)
        self.assertTrue(lifecycle.wck_disabled)
        self.assertIsNone(lifecycle.training_epoch)
        self.assertEqual(lifecycle.profile_epoch, 0)
        self.assertIsNone(lifecycle.transfer_at)
        self.assertIsNone(lifecycle.kind)
        self.assertIsNone(lifecycle.target_fsp)
        self.assertIsNone(lifecycle.retention_deadline_ps)
        self.assertEqual((lifecycle.maximum, lifecycle.active_fsp), (0, 0))
        lifecycle.boot(4000, complete=True, boot_state="IDLE", training_epoch=1, profile_epoch=2)
        lifecycle.enable()
        self.assertTrue(lifecycle.drain(4000))
        lifecycle.maintenance.activate(1, 3)
        self.assertEqual(lifecycle.fault_diagnostics, retained)

    def test_fresh_self_refresh_boot_has_only_new_extra_obligations(self):
        lifecycle = self.booted()
        lifecycle.maintenance.hold_sr(0)
        lifecycle.fail()
        lifecycle.recover(True, True)
        lifecycle.boot(4000, complete=True, boot_state="SELF_REFRESH", training_epoch=1, profile_epoch=2)
        self.assertEqual(lifecycle.maintenance.self_refresh, set())
        self.assertEqual(lifecycle.maintenance.extra, [True, True])
        self.assertEqual(lifecycle.maintenance.last_refresh, [4000, 4000])
        lifecycle.enable()
        with self.assertRaises(ValueError):
            lifecycle.request("SELF_REFRESH", 4001, 100)
        for rank in range(2):
            lifecycle.maintenance.refresh(rank, extra=True)
        lifecycle.request("SELF_REFRESH", 4001, 100)
        lifecycle.enter_self_refresh(4010)
        lifecycle.exit_self_refresh(100000, 174)
        self.assertTrue(lifecycle.trusted)

    def test_repeated_recovery_fences_epochs_and_preserves_capabilities(self):
        lifecycle = Lifecycle(maintenance=Maintenance(mr27=0x47, mr57=1, ranks=1))
        for epoch in range(3):
            lifecycle.boot(
                epoch * 4000, complete=True, boot_state="IDLE", training_epoch=epoch, profile_epoch=epoch + 1
            )
            lifecycle.enable()
            lifecycle.fail()
            lifecycle.recover(True, True)
            self.assertEqual(lifecycle.maintenance.ranks, 1)
            self.assertEqual((lifecycle.maintenance.mr27, lifecycle.maintenance.mr57), (0x47, 1))
            self.assertEqual(lifecycle.boot_epoch, epoch + 1)
        self.assertEqual(len(lifecycle.fault_diagnostics), 3)
        lifecycle.boot_epoch = (1 << 32) - 1
        lifecycle.fail()
        with self.assertRaisesRegex(ValueError, "epoch exhausted"):
            lifecycle.recover(True, True)
        self.assertEqual(lifecycle.state, "FAULT")


class DataChipSelectTests(unittest.TestCase):
    def test_nonzero_gaps_rejected_at_all_profile_and_programming_boundaries(self):
        config = example_configuration()
        image = register_image(SCHEMA, PROFILES[0], config)
        for name in ("tphy_rdcsgap", "tphy_wrcsgap"):
            for value in (1, 64, 65535):
                p = deepcopy(PROFILES[0])
                p["dfi_wck"][name] = value
                with self.subTest(name=name, value=value):
                    for invoke in (
                        lambda profile: admit(profile),
                        lambda profile: waveform(profile, "read", 32),
                        lambda profile: waveform(profile, "write", 32),
                        lambda profile: check(profile, waveform(PROFILES[0], "read", 32)),
                        lambda profile: register_image(SCHEMA, profile, config),
                        lambda profile: validate_register_image(image, profile, config, SCHEMA),
                    ):
                        with self.assertRaisesRegex(ValueError, "gap unsupported"):
                            invoke(p)
        for fsp in range(3):
            for name in ("TPHY_RDCSGAP", "TPHY_WRCSGAP"):
                b = RegisterBank(SCHEMA, 2)
                b.staging = [dict(image), dict(image)]
                b.staging[1][f"FSP{fsp}_{name}"] = 1
                with self.assertRaises(ValueError):
                    b.prepare(lambda state: validate_register_image(state, PROFILES[0], config, SCHEMA))
                self.assertFalse(b.valid)
                self.assertIsNone(b.pending)

    def test_cs_continuous_through_split_and_explicit_deassertion(self):
        for p in PROFILES:
            for direction in ("read", "write"):
                for beats in (16, 32):
                    e = waveform(p, direction, beats)
                    check(p, e)
                    span = beats + (16 if p["ratio"] == 4 and beats == 32 else 0)
                    self.assertEqual(e.data_cs_end - e.data_cs[0][0], span)
                    self.assertEqual(len(e.data_cs), span // 2)
                    # Early/late deassertion, a pulse in the split gap, and a
                    # missing final word slot must each fail independently.
                    for bad in (
                        replace(e, data_cs_end=e.data_cs_end - 2),
                        replace(e, data_cs_end=e.data_cs_end + 2),
                        replace(e, data_cs=e.data_cs[:7] + e.data_cs[8:]),
                        replace(e, data_cs=e.data_cs[:-1]),
                        replace(e, data_cs=tuple((at, 1) for at, _ in e.data_cs)),
                    ):
                        with self.assertRaisesRegex(ValueError, "chip-select"):
                            check(p, bad)
                    if p["ratio"] == 4 and beats == 32:
                        # Former sparse word-only representation left all
                        # eight split-gap cycles unspecified.
                        sparse = e.data_cs[:8] + e.data_cs[16:]
                        with self.assertRaisesRegex(ValueError, "chip-select"):
                            check(p, replace(e, data_cs=sparse))

    def test_rank_changes_wait_for_both_cs_deassertion_and_full_wck_disable(self):
        for p in PROFILES:
            for direction in ("read", "write"):
                first = waveform(p, direction, 32, rank=0)
                second = waveform(p, direction, 32, rank=1)
                # A fresh rank has its own CAS after all old-rank holds and
                # full disable, regardless of normal/always-on WCK policy.
                boundary = max(first.disabled - second.cas, first.data_cs_end - second.data_cs[0][0])
                check_data_cs_sequence(p, [(0, first), (boundary, second)])
                with self.assertRaisesRegex(ValueError, "rank change"):
                    check_data_cs_sequence(p, [(0, first), (boundary - 2 * p["ratio"], second)])
                with self.assertRaisesRegex(ValueError, "CK phase"):
                    check_data_cs_sequence(p, [(0, first), (boundary + 1, second)])
                with self.assertRaisesRegex(ValueError, "rank change"):
                    check_data_cs_sequence(p, [(0, first), (0, second)])
                check_data_cs_sequence(p, [(0, first), (boundary, waveform(p, direction, 32, rank=0))])

    def test_long_cs_path_cannot_switch_rank_while_cs_is_held(self):
        p = deepcopy(PROFILES[0])
        p["dfi_wck"]["tphy_rdcslat"] = 1000
        first = waveform(p, "read", 32, rank=0)
        second = waveform(p, "write", 32, rank=1)
        # WCK is disabled, yet the independently delayed read CS still owns
        # rank 0. A cross-direction transition must wait for that hold too.
        early = first.disabled - second.cas
        with self.assertRaisesRegex(ValueError, "CS deassertion"):
            check_data_cs_sequence(p, [(0, first), (early, second)])
        ready = first.data_cs_end - second.data_cs[0][0]
        check_data_cs_sequence(p, [(0, first), (ready, second)])
