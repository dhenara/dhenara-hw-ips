"""Contract qualification with explicit independent fault sensitivity."""

import json
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from contracts.configuration import RegisterBank, validate_configuration
from contracts.host import Address, HostOracle, beat_bytes
from contracts.integrity import (
    InlineStore,
    WriteEpoch,
    check_transport,
    dbi_encode,
    inline_decode,
    inline_encode,
    inline_location,
    receive_byte,
    transport,
    write_byte,
)
from contracts.lifecycle import Lifecycle, Maintenance, arbitrate_update, check_low_power
from contracts.performance import check, witness
from contracts.profiles import admit, waveform
from contracts.scheduling import Cap, Queue, check_fairness
from contracts.waveform_oracle import SyncState
from contracts.waveform_oracle import check as check_waveform

IP = Path(__file__).resolve().parents[2]
PROFILES = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"]
MATRIX = json.loads((IP / "config/reference/link_ecc_matrix.json").read_text())
SCHEMA = json.loads((IP / "config/contracts/registers.json").read_text())


class ProfileTests(unittest.TestCase):
    def test_all_numeric_modes_data_paths_ranks_bursts_sync_forms(self):
        for p in PROFILES:
            for direction in ("read", "write"):
                for beats in (16, 32):
                    for rank in (0, 1):
                        for fast in (False, True):
                            with self.subTest(
                                profile=p["name"], direction=direction, beats=beats, rank=rank, fast=fast
                            ):
                                admit(p)
                                e = waveform(p, direction, beats, rank, fast)
                                check_waveform(p, e, fast)

    def test_profile_rejects_missing_overflow_capability_mr_units(self):
        for mutate in [
            lambda p: p["wck"].pop("twck_en_fs"),
            lambda p: p["wck"].update(twck_toggle=-1),
            lambda p: p["wck"].update(twck_toggle=65536),
            lambda p: p.update(ratio=2),
            lambda p: p["mr"].update({"2": 0}),
            lambda p: p["management_ck"].update(tctrlupd_min=1),
            lambda p: p["dram_ck"].update(trfc_ab=167),
            lambda p: p["device"].update(mr27=1),
        ]:
            p = deepcopy(PROFILES[0])
            mutate(p)
            with self.assertRaises(ValueError):
                admit(p)
        p = deepcopy(next(x for x in PROFILES if x["read_link"]))
        p["device"]["raw_parity"] = False
        with self.assertRaises(ValueError):
            admit(p)

    def test_independent_early_late_path_and_pin_faults(self):
        p = PROFILES[0]
        e = waveform(p, "read", 32)
        for field in ("enable", "slow", "fast", "first", "command", "stop", "disabled"):
            for delta in (-8, 8):
                # Later postamble/disable is legal; force these below minimum.
                if field in ("stop", "disabled") and delta > 0:
                    continue
                bad = replace(e, **{field: getattr(e, field) + delta})
                with self.subTest(field=field, delta=delta), self.assertRaises(ValueError):
                    check_waveform(p, bad)
        for mutate in [
            lambda x: replace(x, edges=x.edges[:8] + x.edges[9:]),
            lambda x: replace(x, data=x.data[:16] + tuple(t - 16 for t in x.data[16:])),
            lambda x: replace(x, returned=tuple(t - 16 for t in x.returned)),
            lambda x: replace(x, cs=((x.cas, 0), (x.disabled, 1))),
        ]:
            with self.assertRaises(ValueError):
                check_waveform(p, mutate(e))
        for direction in ("read", "write"):
            for delay in ("wck", "write" if direction == "write" else "read"):
                p = deepcopy(PROFILES[0])
                p["phy_half_wck"][delay] += 64
                with self.assertRaises(ValueError):
                    check_waveform(p, waveform(p, direction, 32))
        p = deepcopy(PROFILES[0])
        p["phy_half_wck"]["dq_output"] = 0
        with self.assertRaises(ValueError):
            check_waveform(p, waveform(p, "read", 32))

    def test_reuse_expiry_restart_rank_and_ownership(self):
        s = SyncState(4)
        s.access(0, 0, "read", 32, 17, True)
        s.access(8, 0, "read", 32, 17, False)
        with self.assertRaises(ValueError):
            s.access(16, 0, "read", 32, 17, True)
        with self.assertRaises(ValueError):
            s.access(40, 0, "read", 32, 17, False)
        with self.assertRaises(ValueError):
            s.access(16, 1, "read", 32, 17, False)
        with self.assertRaises(ValueError):
            s.static_restart()
        with self.assertRaises(ValueError):
            s.ownership(20, True)
        s.stop(40, 45)
        s.fresh_after_stop(45)
        s.ownership(45, True)
        s.access(46, 1, "read", 32, 17, True)
        a = SyncState(4, True)
        a.access(0, 0, "write", 32, 9, True)
        a.access(100, 0, "write", 32, 9, False)
        with self.assertRaises(ValueError):
            a.stop(101, 106)
        a.stop(120, 125)


class HostTests(unittest.TestCase):
    def test_burst_address_rules(self):
        self.assertEqual(beat_bytes(Address(0, 65535, 3, 3, 4), 16), [(3,), (4, 5, 6, 7), (8, 9, 10, 11)])
        self.assertEqual([b[0] for b in beat_bytes(Address(0, 1, 12, 4, 4, "WRAP"), 16)], [12, 0, 4, 8])
        self.assertEqual(beat_bytes(Address(0, 0, 7, 2, 4, "FIXED"), 16), [(7,), (7,)])
        for a in [Address(0, 0, 4092, 2, 4), Address(0, 0, 2, 4, 4, "WRAP"), Address(0, 0, 0, 17, 4, "FIXED")]:
            with self.assertRaises(ValueError):
                beat_bytes(a, 16)

    def test_aw_hazard_before_w_and_exclusive_side_effect(self):
        h = HostOracle()
        w = h.admit(Address(0, 9, 0, 1, 4, lock=True), True)
        r = h.admit(Address(1, 9, 0, 1, 4), False)
        with self.assertRaises(ValueError):
            h.execute(r)
        h.w(w, 0x12345678, 15, True)
        h.execute(w)
        h.execute(r)
        self.assertEqual(h.response(r), (9, 0x12345678, "OKAY", True))
        self.assertEqual(h.response(w), (9, 0, "OKAY", True))

    def test_same_edge_ties_same_id_and_w_order(self):
        h = HostOracle()
        tags = h.edge(
            [(Address(1, 1, 0, 1, 4), False), (Address(0, 1, 0, 1, 4), False), (Address(0, 1, 16, 1, 4), True)]
        )
        self.assertEqual([h.transactions[t].a.port for t in tags], [0, 0, 1])
        r = h.admit(Address(0, 1, 32, 1, 4), False)
        h.execute(r)
        with self.assertRaises(ValueError):
            h.response(r)
        h.execute(tags[1])
        h.response(tags[1])
        self.assertEqual(h.response(r)[2], "OKAY")
        w = h.admit(Address(0, 2, 64, 1, 4), True)
        with self.assertRaises(ValueError):
            h.w(w, 0, 15, True)

    def test_decode_errors_have_no_partial_side_effect(self):
        h = HostOracle(regions=((0, 4096),))
        w = h.admit(Address(0, 7, 4092, 2, 4), True)
        h.w(w, 123 << 96, 15 << 12, False)
        h.w(w, 99, 15, True)
        h.execute(w)
        self.assertEqual(h.response(w)[2], "DECERR")
        self.assertEqual(h.memory, {})
        w = h.admit(Address(0, 7, 0, 2, 4), True)
        h.w(w, 123, 15, True)
        h.w(w, 99 << 32, 15 << 4, True)
        h.execute(w)
        self.assertEqual(h.response(w)[2], "SLVERR")
        self.assertEqual(h.memory, {})

    def test_fatal_settles_original_counts_and_waits_for_w(self):
        h = HostOracle()
        r = h.admit(Address(0, 65535, 0, 4, 4), False)
        w = h.admit(Address(1, 65534, 64, 2, 4), True)
        h.execute(r)
        self.assertFalse(h.response(r)[3])
        h.fail()
        with self.assertRaises(ValueError):
            h.recover()
        with self.assertRaises(ValueError):
            h.admit(Address(0, 0, 0, 1, 4), False)
        with self.assertRaises(ValueError):
            h.execute(w)
        for i in range(3):
            self.assertEqual(h.response(r), (65535, 0, "SLVERR", i == 2))
        h.w(w, 0, 15, False)
        h.w(w, 0, 15 << 4, True)
        h.execute(w)
        self.assertEqual(h.response(w)[2], "SLVERR")
        h.recover()


class IntegrityTests(unittest.TestCase):
    def test_dbi_mask_transform_all_bytes(self):
        for value in range(256):
            dq, dmi = dbi_encode(value)
            self.assertLessEqual(dq.bit_count(), 4)
            self.assertEqual(receive_byte(dq, dmi, True, False), value)
            for dbi in (False, True):
                self.assertIsNone(receive_byte(*write_byte(value, False, dbi, True), dbi, True))
        with self.assertRaises(ValueError):
            write_byte(1, False, False, False)

    def test_full_x16_split_link_transport_faults(self):
        for size in (32, 64):
            data = bytes((i * 97 + 11) & 255 for i in range(size))
            for write in (False, True):
                for dbi in (False, True) if write else (False,):
                    enabled = [i % 7 != 0 for i in range(size)] if write else [True] * size
                    wire = transport(MATRIX, 17, data, enabled, write=write, dbi=dbi)
                    output, status = check_transport(MATRIX, wire, write=write, dbi=dbi, masked=not all(enabled))
                    self.assertEqual(output, bytes(x if enabled[i] else 0 for i, x in enumerate(data)))
                    self.assertEqual(set(status), {"clean"})
                    broken = list(wire)
                    broken[9] = replace(broken[9], dq=broken[9].dq ^ 1)
                    output, status = check_transport(MATRIX, broken, write=write, dbi=dbi, masked=not all(enabled))
                    self.assertIn("corrected", status)
                    broken[9] = replace(broken[9], dq=broken[9].dq ^ 2)
                    with self.assertRaises(ValueError):
                        check_transport(MATRIX, broken, write=write, dbi=dbi)
                    broken = list(wire)
                    broken[0] = replace(broken[0], tag=18)
                    with self.assertRaises(ValueError):
                        check_transport(MATRIX, broken, write=write, dbi=dbi)

    def test_inline_secded_all_single_double_faults(self):
        data = 0xF0E1D2C3B4A59687
        code = inline_encode(data)
        self.assertEqual(inline_decode(code), (data, "clean"))
        self.assertEqual(inline_encode(0), 0)
        for i in range(72):
            self.assertEqual(inline_decode(code ^ (1 << i)), (data, "corrected"))
            for j in range(i):
                self.assertEqual(inline_decode(code ^ (1 << i) ^ (1 << j))[1], "uncorrectable")
        self.assertEqual(inline_location(63, 576), (504, 575))
        with self.assertRaises(ValueError):
            inline_location(64, 576)

    def test_partial_rmw_scrub_version_cache_and_physical_failure(self):
        s = InlineStore(cache_lines=1)
        s.begin(1, 0, 0x11223344, 255)
        self.assertFalse(s.drain())
        self.assertEqual(s.commit(1), "OKAY")
        s.words[0] ^= 1 << 2
        self.assertEqual(s.read(0), 0x11223344)
        s.begin(2, 0, 0x55, 1)
        s.commit(2)
        self.assertFalse(s.scrub_one(0))
        self.assertEqual(s.read(0), 0x11223355)
        s.begin(3, 0, 0, 255)
        with self.assertRaises(ValueError):
            s.read(0)
        with self.assertRaises(ValueError):
            s.begin(4, 64, 0, 255)
        with self.assertRaises(ValueError):
            s.reset()
        self.assertEqual(s.commit(3, check_ok=False), "SLVERR")
        with self.assertRaises(ValueError):
            s.read(0)
        with self.assertRaises(ValueError):
            s.begin(4, 0, 0, 1)
        s.begin(4, 0, 99, 255)
        s.commit(4)
        self.assertEqual(s.read(0), 99)
        self.assertTrue(s.drain())

    def test_strict_posted_epochs_poll_clear_and_late_error(self):
        for posted in (False, True):
            e = WriteEpoch(posted)
            e.add(99, 0, 30)
            e.add(100, 1, 31)
            self.assertEqual(e.responses, {})
            with self.assertRaises(ValueError):
                e.poll(30)
            if posted:
                self.assertEqual(e.acknowledge(99, 30), "OKAY")
                self.assertEqual(e.acknowledge(100, 31), "OKAY")
            e.poll(32)
            with self.assertRaises(ValueError):
                e.settle(51, 128, 8, 12)
            self.assertEqual(set(e.settle(52, 128, 8, 12).values()), {"OKAY" if posted else "SLVERR"})
            self.assertEqual(e.late_error, posted)


class LifecycleTests(unittest.TestCase):
    def test_boot_training_reset_and_outstanding_recovery(self):
        lifecycle = Lifecycle()
        with self.assertRaises(ValueError):
            lifecycle.enable()
        with self.assertRaises(ValueError):
            lifecycle.boot(0, complete=True, boot_state="ACTIVE", training_epoch=0, profile_epoch=1)
        lifecycle.boot(0, complete=True, boot_state="IDLE", training_epoch=0, profile_epoch=1)
        lifecycle.enable()
        lifecycle.pending.add(1)
        with self.assertRaises(ValueError):
            lifecycle.request("CTRL_UPDATE", 10, 32)
        lifecycle.pending.clear()
        lifecycle.write_end = 20
        with self.assertRaises(ValueError):
            lifecycle.request("CTRL_UPDATE", 19, 32)
        lifecycle.request("CTRL_UPDATE", 20, 32)
        with self.assertRaises(ValueError):
            lifecycle.finish(25, ownership_returned=False)
        self.assertFalse(lifecycle.trusted)
        self.assertEqual(lifecycle.owner, "UNKNOWN")
        with self.assertRaises(ValueError):
            lifecycle.recover(False, True)
        lifecycle.recover(True, True)
        with self.assertRaises(ValueError):
            lifecycle.boot(0, complete=True, boot_state="IDLE", training_epoch=0, profile_epoch=1)

    def test_thermal_refresh_pairs_rfm_and_sr_extra(self):
        m = Maintenance()
        m.advance(1000)
        m.temperature(13)
        self.assertFalse(m.service_allowed())
        self.assertEqual(m.credit, 0)
        self.assertEqual(m.timing_derating()["trc"], 3)
        for rank in range(2):
            m.refresh(rank)
        m.debt = 0
        self.assertTrue(m.service_allowed())
        for _ in range(m.maximum):
            m.activate(0, 0)
        with self.assertRaises(ValueError):
            m.activate(0, 8)
        before = m.last_refresh[0]
        m.rfm(0, 0)
        self.assertEqual(m.last_refresh[0], before)
        self.assertEqual(m.raa[0][0], m.maximum - m.imt)
        m.exit_sr(0)
        with self.assertRaises(ValueError):
            m.enter_sr(0)
        m.refresh(0)
        self.assertTrue(m.extra[0])
        m.refresh(0, extra=True)
        m.enter_sr(0)
        with self.assertRaises(ValueError):
            m.refresh(0, pair=1)
        for pair in range(8):
            m.refresh(0, pair=pair)
        with self.assertRaises(ValueError):
            m.temperature(31)

    def test_rfm_decline_and_required_discovery(self):
        for mr in (1,):
            with self.assertRaises(ValueError):
                Maintenance(mr27=mr)
        for code in (0, 16, 31):
            with self.assertRaises(ValueError):
                Maintenance().temperature(code)
        for factor in range(4):
            m = Maintenance(mr57=factor)
            for _ in range(32):
                m.activate(0, 0)
            m.rfm(0, 0)
            self.assertEqual(m.raa[0][0], max(0, 32 - 16 * (1, 1.5, 2, 4)[factor]))

    def test_pm_dvfs_phy_master_and_retention(self):
        for kind in ("POWER_DOWN", "SELF_REFRESH", "DVFS", "PHY_MASTER_IDLE", "PHY_MASTER_SR"):
            lifecycle = Lifecycle()
            lifecycle.boot(0, complete=True, boot_state="IDLE", training_epoch=0, profile_epoch=1)
            lifecycle.enable()
            lifecycle.request(kind, 10, 100, target_fsp=1 if kind == "DVFS" else None)
            if kind == "PHY_MASTER_IDLE":
                lifecycle.phy_refresh(20, 0, 2)
            lifecycle.finish(100, profile_epoch=2)
            self.assertTrue(lifecycle.trusted)
            self.assertEqual(lifecycle.state, "QUIESCED")
            if kind == "DVFS":
                self.assertEqual(lifecycle.active_fsp, 1)
        lifecycle = Lifecycle()
        lifecycle.boot(0, complete=True, boot_state="IDLE", training_epoch=0, profile_epoch=1)
        lifecycle.request("DVFS", 10, 100, target_fsp=1)
        with self.assertRaises(ValueError):
            lifecycle.finish(20, elapsed_ps=4000 * 1250, profile_epoch=2)
        self.assertFalse(lifecycle.trusted)
        lifecycle = Lifecycle()
        lifecycle.boot(0, complete=True, boot_state="IDLE", training_epoch=0, profile_epoch=1)
        with self.assertRaises(ValueError):
            lifecycle.request("CTRL_UPDATE", 3100, 32)

    def test_low_power_decline_wakeup_and_competing_update(self):
        self.assertEqual(check_low_power(4, 4, [False] * 13, 4), "declined")
        self.assertEqual(check_low_power(4, 4, [False, False] + [True] * 5 + [False] * 6, 7), "acknowledged")
        with self.assertRaises(ValueError):
            check_low_power(4, 4, [False] * 5 + [True] * 12, 7)
        self.assertEqual(arbitrate_update(True, False, True, 0, 96), "wait_controller_release")
        self.assertEqual(arbitrate_update(False, True, True, 35, 96), "ack_phy")
        with self.assertRaises(ValueError):
            arbitrate_update(False, True, True, 97, 96)


class SchedulerTests(unittest.TestCase):
    def test_age_replay_generation_empty_escape_and_pair_slots(self):
        for div in (1, 2, 4):
            q = Queue(service_div=div)
            q.admit(0, 1, True, 0)
            for ck in range(1, 17):
                q.tick(ck)
            self.assertEqual(q.slots[0].age, 2)
            pick = q.pick(16)
            self.assertTrue(q.batch_write)
            self.assertFalse(q.issue(pick, legal=False, owned=True, credits=True))
            pick = q.pick(20)
            self.assertTrue(q.issue(pick, legal=True, owned=True, credits=True))
            with self.assertRaises(ValueError):
                q.issue(pick, legal=True, owned=True, credits=True)
            q.admit(0, 2, False, 1)
            with self.assertRaises(ValueError):
                q.issue(pick, legal=True, owned=True, credits=True)
            q.slots[0].age = 65535
            q.tick(24)
            self.assertEqual(q.slots[0].age, 65535)

    def test_caps_and_fairness_sensitivity(self):
        for mode in ("strict", "work_conserving"):
            c = Cap(16, (1, 1), mode)
            self.assertTrue(c.admit(0, 0, True))
            self.assertFalse(c.admit(1, 0, True))
            self.assertEqual(c.admit(2, 0, False), mode == "work_conserving")
            self.assertTrue(c.admit(16, 0, True))
        check_fairness([(4, 0), (8, 1), (12, 0), (16, 1)], {0: 0, 1: 0}, 8, observed_until=16)
        with self.assertRaises(ValueError):
            check_fairness([(4, 0), (20, 1)], {0: 0, 1: 0}, 8, observed_until=16)


class PerformanceTests(unittest.TestCase):
    def test_report_rejects_changed_pin_offsets(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("lp5_qualify", IP / "tools/qualify.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for field, value in (("dq_input", 3), ("dq_output", 7)):
            profiles = deepcopy(PROFILES)
            benchmark = next(
                p
                for p in profiles
                if p["ratio"] == 4
                and p["always_on"]
                and not any(p[k] for k in ("read_link", "write_link", "read_dbi", "write_dbi"))
            )
            benchmark["phy_half_wck"][field] = value
            # These remain legal fixture offsets; they cannot be silently used
            # with the schedule checker's independently transcribed constants.
            admit(benchmark)
            with self.assertRaisesRegex(ValueError, "periodic witness PHY timing changed"):
                module.check_benchmark_profile(benchmark)

    def test_periodic_read_write_full_and_reduced(self):
        for direction in ("read", "write"):
            for div in (1, 2, 4):
                report = check(witness(4, direction, div), 4, direction, div)
                self.assertEqual(report["periodic_backlog"], [20] * 4)
                self.assertLessEqual(report["outstanding_max"], 64)
                self.assertAlmostEqual(report["combined_gb_s"], 12.8 if div != 4 else 11.5712)

    def test_early_precharge_missing_refresh_split_collision_and_row_wrap(self):
        commands = witness(3)
        for predicate, change in [
            (lambda c: c.op == "PRE", lambda c: replace(c, ck=c.ck - 100)),
            (lambda c: c.op == "read", lambda c: replace(c, column=2048)),
            (lambda c: c.op == "ACT2", lambda c: replace(c, ck=c.ck + 10)),
        ]:
            bad = list(commands)
            index = next(i for i, c in enumerate(bad) if predicate(c))
            bad[index] = change(bad[index])
            with self.assertRaises(ValueError):
                check(sorted(bad, key=lambda c: c.ck), 3, "read")
        with self.assertRaises(ValueError):
            check([c for c in commands if c.op != "REF"], 3, "read")


class ConfigurationTests(unittest.TestCase):
    def test_matrix_mapping_and_invalid_values(self):
        from contracts.configuration import example_configuration, map_address

        for ports in (1, 2):
            for channels in (1, 2):
                for ranks in (1, 2):
                    for width in (64, 128, 256):
                        for div in (1, 2, 4):
                            c = example_configuration()
                            c.update(ports=ports, channels=channels, ranks=ranks, axi_bits=width, service_div=div)
                            validate_configuration(c)
        c = example_configuration()
        c["regions"][0]["map_bits"][6:8] = [7, 6]
        validate_configuration(c)
        self.assertEqual(map_address(64, c["regions"][0]), 128)
        self.assertEqual(map_address(128, c["regions"][0]), 64)
        for key, value in [
            ("queue_depth", 1),
            ("outstanding", 65),
            ("id_bits", 17),
            ("address_bits", 41),
            ("service_div", 3),
            ("read_link", True),
        ]:
            c = example_configuration()
            c[key] = value
            with self.assertRaises(ValueError):
                validate_configuration(c)
        c = example_configuration()
        c["regions"].append(deepcopy(c["regions"][0]))
        with self.assertRaises(ValueError):
            validate_configuration(c)
        c = example_configuration()
        c["regions"][0]["map_bits"][6] = 0
        with self.assertRaises(ValueError):
            validate_configuration(c)

    def test_apb_strobes_w1s_locks_broadcast_and_no_side_effect_error(self):
        b = RegisterBank(SCHEMA, 2)
        b.access(0, "SCHED_RD_AGE", True, 0x1234, 1, broadcast=True)
        self.assertEqual(b.access(1, "SCHED_RD_AGE", strobes=0), 0x434)
        self.assertEqual(b.access(0, "SCHED_RD_AGE", strobes=0), 0x434)
        b.access(0, "CONTROL", True, 2)
        self.assertTrue(b.mailbox)
        self.assertEqual(b.access(0, "CONTROL", strobes=0), 0)
        b.access(0, "MODE", True, 0x101)
        self.assertEqual(b.staging[0]["MODE"], b.staging[1]["MODE"])
        b.enabled = True
        b.quiesced = True
        before = deepcopy(b.staging)
        for args in [
            (0, "MODE", True, 0, 15, False, True),
            (0, "ID", True, 0, 15, False, True),
            (0, "UNMAPPED", True, 0, 15, False, True),
            (0, "MODE", False, 0, 15, False, True),
            (0, "SCHED_RD_AGE", True, 0, 15, False, False),
            (1, "SCHED_RD_AGE", True, 0, 15, True, True),
        ]:
            with self.assertRaises(ValueError):
                b.access(*args)
            self.assertEqual(b.staging, before)
        b.quiesced = False
        with self.assertRaises(ValueError):
            b.access(0, "FSP0_RL", True, 17)
        b.access(0, "FSP1_RL", True, 17)

    def test_atomic_snapshot_stale_partial_timeout_reset(self):
        b = RegisterBank(SCHEMA, 2)
        b.access(0, "SCHED_RD_AGE", True, 55, broadcast=True)
        with self.assertRaises(ValueError):
            b.prepare(lambda s: (_ for _ in ()).throw(ValueError("invalid profile")))
        self.assertIsNone(b.pending)
        self.assertFalse(b.valid)
        seq = b.prepare(lambda s: None)
        self.assertNotEqual(b.active[0]["SCHED_RD_AGE"], 55)
        with self.assertRaises(ValueError):
            b.acknowledge(seq, {0})
        with self.assertRaises(ValueError):
            b.acknowledge(seq - 1, {0, 1})
        with self.assertRaises(ValueError):
            b.access(0, "SCHED_RD_AGE", True, 99)
        b.acknowledge(seq, {0, 1})
        self.assertTrue(b.valid)
        self.assertEqual(b.active[1]["SCHED_RD_AGE"], 55)
        seq = b.prepare(lambda s: None)
        b.abort()
        self.assertTrue(b.fault)
        self.assertFalse(b.valid)
        with self.assertRaises(ValueError):
            b.acknowledge(seq, {0, 1})
        b.reset()
        self.assertFalse(b.valid)
        self.assertEqual(b.active[1]["FSP0_RL"], 0)


class CommandTests(unittest.TestCase):
    def test_ca_literal_vectors_and_operand_round_trip(self):
        from contracts.commands import access, activate, cas, maintenance, mode_read, mode_write

        self.assertEqual(access(False, 32, 0, 0), 5)
        self.assertEqual(access(True, 32, 0, 0), 4)
        self.assertEqual(cas("read"), 0x2C)
        self.assertEqual(cas("write"), 0x1C)
        self.assertEqual(cas("fast"), 0x4C)
        self.assertEqual(cas("off"), 0x7C)
        self.assertEqual(maintenance("PRE"), 0x2078)
        self.assertEqual(maintenance("REF"), 0x2038)
        self.assertEqual(maintenance("RFM"), 0x2438)
        self.assertEqual(mode_read(43), 0x1598)
        self.assertEqual(mode_write(1, 176), (0xD8, 0x1848))
        for row in (0, 1, 127, 128, 2048, 32767, 32768, 262143):
            for bank in range(16):
                first, second = activate(row, bank)
                self.assertEqual(first & 7, 7)
                self.assertEqual(second & 7, 3)
                decoded = (
                    ((first >> 3 & 15) << 14)
                    | ((first >> 11 & 7) << 11)
                    | ((second >> 3 & 15) << 7)
                    | (second >> 7 & 127)
                )
                self.assertEqual(decoded, row)
                self.assertEqual(first >> 7 & 15, bank)
        for column in range(64):
            for bank in range(16):
                ca = access(False, 32, bank, column)
                self.assertEqual(ca & 7, 5)
                self.assertEqual(ca >> 7 & 15, bank)
                self.assertEqual((ca >> 3 & 1) | ((ca >> 11 & 3) << 1) | ((ca >> 4 & 7) << 3), column)
        with self.assertRaises(ValueError):
            access(True, 32, 0, 1)

    def test_register_programming_example_validates_numeric_pin_contract(self):
        from contracts.configuration import example_configuration, register_image, validate_register_image

        c = example_configuration()
        p = PROFILES[0]
        image = register_image(SCHEMA, p, c)
        validate_register_image(image, p, c, SCHEMA)
        image["FSP0_TWCK_EN_RD"] -= 1
        with self.assertRaises(ValueError):
            validate_register_image(image, p, c, SCHEMA)


class OwnershipTests(unittest.TestCase):
    def test_fsp_ac_guards_and_atomic_publication(self):
        from contracts.lifecycle import check_fsp

        steps = [
            (0, "DRAIN", 0),
            (1, "WS_OFF", 0),
            (6, "DISABLE", 0),
            (7, "FSP_WR", 1),
            (20, "PROGRAM", 1),
            (40, "FSP_OP", 1),
            (241, "CLOCK_STABLE", 0),
            (242, "TRAINED", 1),
            (247, "PUBLISH", 1),
        ]
        check_fsp(steps, 1)
        for index, value in [(2, (5, "DISABLE", 0)), (6, (240, "CLOCK_STABLE", 0)), (8, (247, "PUBLISH", 2))]:
            bad = list(steps)
            bad[index] = value
            with self.assertRaises(ValueError):
                check_fsp(bad, 1)

    def test_phy_update_competing_response_and_write_drain(self):
        from contracts.lifecycle import check_phy_update

        options = dict(response=96, type_bound=32, write_end=30, ctrl_release=32, wck_disabled=True)
        check_phy_update(0, 33, 60, 61, **options)
        for args in [(0, 30, 60, 61), (0, 97, 100, 101), (0, 33, 66, 67), (0, 33, 60, 60)]:
            with self.assertRaises(ValueError):
                check_phy_update(*args, **options)

    def test_runtime_master_refresh_and_retention_faults(self):
        from contracts.lifecycle import check_master

        options = dict(
            response=96,
            type_bound=256,
            closed_at=30,
            write_end=20,
            wck_disabled=True,
            cs_state=(0, 0),
            syscs=(1, 1),
            refreshes=[(100, 102, 0), (103, 105, 1)],
            refresh_deadline=150,
            refresh_latency=2,
            state_sel=0,
            chosen_states=("IDLE", "IDLE"),
            returned_states=("IDLE", "IDLE"),
        )
        check_master(0, 40, 250, 251, **options)
        for name, value in [
            ("wck_disabled", False),
            ("closed_at", 41),
            ("refreshes", []),
            ("refreshes", [(100, 104, 0), (103, 105, 1)]),
            ("refresh_deadline", 100),
        ]:
            bad = dict(options)
            bad[name] = value
            with self.assertRaises(ValueError):
                check_master(0, 40, 250, 251, **bad)


class ClosureTests(unittest.TestCase):
    def test_periodic_maintenance_missing_or_late_is_detected(self):
        commands = witness(3)
        for op in ("MR4", "ZQ_LATCH", "OFF", "SYNC"):
            with self.subTest(op=op), self.assertRaises(ValueError):
                check([c for c in commands if c.op != op], 3, "read")
        with self.assertRaises(ValueError):
            check([replace(c, ck=c.ck - 20) if c.op == "OFF" else c for c in commands], 3, "read")

    def test_sync_expiry_uses_array_cycle_not_split_pin_span(self):
        for beats, maximum in ((16, 21), (32, 25)):
            state = SyncState(4)
            state.access(0, 0, "read", beats, 17, True)
            self.assertEqual(state.expires, maximum)
            state.access(maximum, 0, "read", beats, 17, False)
            with self.assertRaises(ValueError):
                state.access(maximum + maximum + 1, 0, "read", beats, 17, False)

    def test_mailbox_overflow_is_atomic_and_status_is_hardware_owned(self):
        b = RegisterBank(SCHEMA, 2)
        b.sequence = (1 << 32) - 2
        before = deepcopy(b.staging)
        with self.assertRaises(ValueError):
            b.access(0, "PERF_SNAPSHOT", True, 1, broadcast=True)
        self.assertEqual(b.staging, before)
        self.assertEqual(b.mailbox, [])
        self.assertEqual(b.sequence, (1 << 32) - 2)
        b.hardware_update(0, "STATUS", 15)
        self.assertEqual(b.access(1, "STATUS", strobes=0), 15)
        with self.assertRaises(ValueError):
            b.access(0, "STATUS", True, 0)
        b.access(0, "TRAIN_TOKEN", True, 0)
        b.access(0, "TRAIN_ACK", True, 1)
        self.assertEqual(b.mailbox[-1][2], "TRAIN_ACK")

    def test_generator_rejects_schema_defects(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("lp5_generate", IP / "tools/generate.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for mutate in (
            lambda s: s["registers"].append(deepcopy(s["registers"][0])),
            lambda s: s["registers"][0].update(offset=1),
            lambda s: s["registers"][0]["fields"][0].update(width=33),
            lambda s: s["registers"][0]["fields"][0].update(width=True),
            lambda s: s["registers"][1]["fields"][1].update(lsb=0),
            lambda s: s["registers"][0]["fields"][0].update(access="w0c"),
        ):
            schema = deepcopy(SCHEMA)
            mutate(schema)
            with self.assertRaises(ValueError):
                module.products(schema)

    def test_fairness_end_of_window_and_byte_caps(self):
        from contracts.scheduling import fairness_bound

        self.assertEqual(fairness_bound(64, 32, 8, 64), 4360)
        check_fairness([(4, 0)], {0: 0}, 8, observed_until=12)
        with self.assertRaises(ValueError):
            check_fairness([(4, 0)], {0: 0}, 8, observed_until=13)
        c = Cap(16, (64, 64))
        self.assertTrue(c.admit(0, 0, True, 32))
        self.assertFalse(c.admit(1, 0, True, 64))
        self.assertTrue(c.admit(2, 0, True, 32))

    def test_address_geometry_capacity_and_distinct_regions(self):
        from contracts.configuration import decode_address, example_configuration

        config = example_configuration()
        self.assertEqual(decode_address(config, 64)["channel"], 1)
        self.assertEqual(decode_address(config, 128)["column_32b"], 2)
        config["regions"] = [
            dict(base=0, size=4096, map_bits=list(range(12))),
            dict(base=8192, size=4096, map_bits=list(range(12))),
        ]
        a = decode_address(config, 0)
        b = decode_address(config, 8192)
        self.assertNotEqual((a["bank"], a["row"]), (b["bank"], b["row"]))
        with self.assertRaises(ValueError):
            decode_address(config, 4096)
        config["regions"] = [dict(base=0, size=1 << 32, map_bits=list(range(32)))]
        with self.assertRaises(ValueError):
            validate_configuration(config)

    def test_long_self_refresh_and_extra_before_reentry(self):
        lifecycle = Lifecycle()
        lifecycle.boot(0, complete=True, boot_state="IDLE", training_epoch=0, profile_epoch=1)
        lifecycle.request("SELF_REFRESH", 10, 100)
        lifecycle.enter_self_refresh(20)
        lifecycle.exit_self_refresh(1_000_000, 174)
        self.assertTrue(lifecycle.trusted)
        with self.assertRaises(ValueError):
            lifecycle.request("SELF_REFRESH", 1_000_175, 100)
        for rank in range(2):
            lifecycle.maintenance.refresh(rank, extra=True)
        lifecycle.request("SELF_REFRESH", 1_000_175, 100)
        with self.assertRaises(ValueError):
            lifecycle.enter_self_refresh(1_000_276)

    def test_write_epoch_ownership_loss_strict_and_posted(self):
        for posted in (False, True):
            e = WriteEpoch(posted)
            e.add(1, 0, 20)
            self.assertEqual(e.fail(), {1: "SLVERR"})
            self.assertFalse(e.late_error)
            self.assertEqual(e.tags, [])

    def test_profile_to_actual_read_word_completion(self):
        from contracts.read_completion import CompletionOracle, ExpectedWord, ReadBusOracle

        for p in PROFILES:
            for beats in (16, 32):
                e = waveform(p, "read", beats)
                expected = [ExpectedWord(en // 2, 7, i, lane) for i, en in enumerate(e.dfi_enable) for lane in range(2)]
                completion = CompletionOracle(expected, beats // 2, p["dfi_wck"]["tphy_rdlat"])
                bus = ReadBusOracle(p["ratio"], completion)
                returns = {}
                for word, at in enumerate(e.returned):
                    returns.setdefault(at // (2 * p["ratio"]), []).append(word)
                completed = []
                for ck, words in returns.items():
                    lanes = [[None, None] for _ in range(p["ratio"])]
                    for offset, word in enumerate(words):
                        for lane in range(2):
                            lanes[(bus.pointer[lane] + offset) % p["ratio"]][lane] = word * 2 + lane
                    completed += bus.snapshot(ck, lanes, [[0, 0] for _ in lanes])
                self.assertEqual(completed, [7])
                self.assertEqual(len(completion.received[7]), beats)


class MaskedCommandTests(unittest.TestCase):
    def test_masked_bl16_only_and_spacing(self):
        from contracts.commands import access, check_masked_sequence, mode_write

        self.assertEqual(access(True, 16, 0, 0, masked=True), 2)
        with self.assertRaises(ValueError):
            access(True, 32, 0, 0, masked=True)
        for ratio in (2, 4):
            check_masked_sequence([(0, 0), (4, 1), (16, 0)], ratio)
            with self.assertRaises(ValueError):
                check_masked_sequence([(0, 0), (15, 0)], ratio)
        # Independent Table 201 fixed opcode anchors, including MA6 and OP7.
        self.assertEqual(mode_write(127, 255), (0x3FD8, 0x3FC8))


class InterfaceSeamTests(unittest.TestCase):
    def test_data_cs_timing_and_rank_faults(self):
        p = PROFILES[0]
        e = waveform(p, "read", 32)
        for data_cs in (tuple((at, 1) for at, _ in e.data_cs), tuple((at - 1, r) for at, r in e.data_cs)):
            with self.assertRaises(ValueError):
                check_waveform(p, replace(e, data_cs=data_cs))

    def test_weighted_shares_empty_escape_zero_and_urgent_classes(self):
        from contracts.scheduling import WeightedArbiter

        arbiter = WeightedArbiter((2, 1))
        self.assertEqual([arbiter.choose((True, True)) for _ in range(6)], [0, 0, 1, 0, 0, 1])
        self.assertEqual(arbiter.choose((False, True)), 1)
        self.assertIsNone(WeightedArbiter((0, 0)).choose((True, True)))
        q = Queue()
        q.admit(0, 0, False, 0, 0)
        q.admit(1, 1, False, 1, 3)
        self.assertEqual(q.pick(0)[2], 1)
        q.issue((1, 1, 1), legal=False, owned=True, credits=True)
        q.slots[0].age = 32
        self.assertEqual(q.pick(2)[2], 0)

    def test_live_status_survives_configuration_publication(self):
        b = RegisterBank(SCHEMA, 2)
        seq = b.prepare(lambda state: None)
        b.hardware_update(0, "STATUS", 15)
        b.acknowledge(seq, {0, 1})
        self.assertEqual(b.active[0]["STATUS"], 15)
        self.assertEqual(b.access(1, "CONFIG_EPOCH", strobes=0), seq)


class FinalBoundaryTests(unittest.TestCase):
    def test_multiple_partial_forward_dependencies_match_serial_memory(self):
        h = HostOracle()
        first = h.admit(Address(0, 1, 0, 1, 4), True)
        second = h.admit(Address(1, 2, 0, 1, 4), True)
        read = h.admit(Address(0, 3, 0, 1, 4), False)
        with self.assertRaises(ValueError):
            h.preview_forwarding(read)
        h.w(first, 0x11223344, 15, True)
        h.w(second, 0xAABBCCDD, 10, True)
        values, deps = h.preview_forwarding(read)
        self.assertEqual((values, deps), ([0xAA22CC44], {first, second}))
        with self.assertRaises(ValueError):
            h.execute(read)
        h.execute(first)
        h.execute(second)
        h.execute(read)
        self.assertEqual(h.response(read)[1], values[0])

    def test_master_request_state_and_return_attestation(self):
        from contracts.lifecycle import check_master

        options = dict(
            response=96,
            type_bound=256,
            closed_at=10,
            write_end=10,
            wck_disabled=True,
            cs_state=(0,),
            syscs=(1,),
            refreshes=[],
            refresh_deadline=100,
            refresh_latency=2,
            state_sel=1,
            chosen_states=("SELF_REFRESH",),
            returned_states=("SELF_REFRESH",),
        )
        check_master(0, 20, 200, 201, **options)
        for key, value in (("state_sel", 0), ("returned_states", ("IDLE",)), ("refreshes", [(50, 52, 0)])):
            bad = dict(options)
            bad[key] = value
            with self.assertRaises(ValueError):
                check_master(0, 20, 200, 201, **bad)
