"""Synthetic admission/sensitivity checks. No historical replay or RTL run."""

import copy
import unittest

import wrapper_management_test as fixtures
from maintenance_window import WINDOW_FIELDS, MaintenanceWindow, window_budget
from physical_stream import PhysicalStream
from system.host import Violation
from wrapper_management import profile_binding, qualify_phases
from wrapper_management_test import sample


def diagnostic(s, **changes):
    return (
        dict(
            kind="window_edge",
            ps=s["ps"] + 625,
            tick=s["tick"],
            channel=s["channel"],
            **(dict.fromkeys(WINDOW_FIELDS, 0) | dict(running=1, thermal=9, refresh_budget=3000)),
        )
        | changes
    )


def raw(c, tick, **changes):
    return sample(channel=c, tick=tick, ps=(tick + 1) * 1250, phyupd_req=0, enabled=1, **changes)


def renewed():
    """Build decoded-command origins and real independent complete MR4 data.

    This is an admission-model fixture, not a complete legal bus simulation.
    Other owning tests exercise the physical replay that supplies these facts.
    """
    w = MaintenanceWindow(profile_binding())
    physical = [PhysicalStream(profile_binding()) for _ in range(2)]
    for c, p in enumerate(physical):
        p.sample(raw(c, 0) | dict(time_ps=1250))
        w.observe(raw(c, 1, cs=1, ca=0x2038), p)
        p.ck = 21
        w.observe(raw(c, 20, cs=1, ca=0x218), p)
        p.thermal.begin(1, 20, 1, 4)
        for word in range(8):
            for lane in range(2):
                p.thermal.receive(1, word, lane, 0x909 if word < 4 and lane == 0 else 0xDADA, 50, 63750)
        w.observe(raw(c, 50), p)
        w.observe(raw(c, 80, cs=1, ca=0x370), p)
        w.observe(raw(c, 99), p)
        w.diagnostic(diagnostic(raw(c, 99)))
        w.observe(raw(c, 100), p)
    return w, physical, raw(0, 100)["ps"] + 1


class WindowTests(unittest.TestCase):
    def test_full_setup_and_remaining_entry_fit_a_natural_period(self):
        w, p, ps = renewed()
        b = w.budget
        self.assertEqual((b["setup_ck"], b["request_ck"], b["total_ck"]), (1866, 349, 2215))
        self.assertLess(b["total_ck"], b["renewal_floor_ck"])
        result = w.begin(ps, p, True)
        self.assertEqual(result["origins"][0]["mr4"], 20)
        # The same complete natural history is rechecked at the dirty point.
        for c in range(2):
            w.observe(raw(c, 999), p[c])
            w.diagnostic(diagnostic(raw(c, 999)))
            w.observe(raw(c, 1000), p[c])
        w.request(raw(0, 1000)["ps"] + 1, p)
        self.assertIsNone(w.start)

    def test_refresh_headroom_does_not_override_imminent_mr4_or_zq(self):
        for key in ("mr4", "zq"):
            w, p, ps = renewed()
            w.origins[0][key] = -2300
            with self.subTest(key=key), self.assertRaisesRegex(Violation, "periodic service"):
                w.begin(ps, p, True)

    def test_equality_rejects_and_one_ck_more_accepts(self):
        for key in ("ref", "mr4", "zq"):
            w, p, ps = renewed()
            lead = w.budget["refresh_lead_ck" if key == "ref" else "poll_lead_ck"]
            period = 3124 if key == "ref" else 3000
            w.origins[0][key] = 100 + w.budget["total_ck"] - period + lead
            with self.subTest(key=key), self.assertRaisesRegex(Violation, "headroom"):
                w.begin(ps, p, True)
            w.origins[0][key] += 1
            w.begin(ps, p, True)

    def test_missing_raw_renewal_or_complete_return_cannot_be_replaced_by_done(self):
        for key in ("ref", "mr4", "zq"):
            w, p, ps = renewed()
            del w.origins[1][key]
            w.diagnostics[1]["mr4_done"] = 1
            with self.subTest(key=key), self.assertRaisesRegex(Violation, "renewal"):
                w.begin(ps, p, True)
        w, p, ps = renewed()
        del w.origins[0]["mr4"]
        # A new MRR must not inherit the completed earlier return.
        p[0].ck = 101
        w.observe(raw(0, 100, cs=1, ca=0x218), p[0])
        self.assertNotIn("mr4", w.origins[0])

    def test_pending_service_on_either_channel_rejects(self):
        for c in range(2):
            for key in (
                "urgent",
                "pending",
                "candidate",
                "mr4_pending",
                "zq_pending",
                "mr4_request",
                "zq_request",
                "thermal_debt",
                "extra_pending",
                "register_pending",
                "register_state",
                "register_owner",
                "maintenance_valid",
                "register_result_valid",
                "life_failure",
                "life_fault_code",
                "master_pending",
                "external_fault",
                "update_fault",
                "fsp_fault",
                "backend_fault",
                "inline_fault",
                "register_fault",
                "maintenance_fault",
                "register_io_state",
            ):
                w, p, ps = renewed()
                w.diagnostics[c][key] = 1
                with self.subTest(channel=c, field=key), self.assertRaisesRegex(Violation, "pending maintenance"):
                    w.begin(ps, p, True)

    def test_recovery_host_thermal_rfm_and_missing_history_reject(self):
        for mutate in (
            lambda p: setattr(p[0].phy.dram, "zq_ready", 1000),
            lambda p: p[1].phy.dram.maintenance.extra.__setitem__(0, True),
            lambda p: setattr(p[0].phy.dram.maintenance, "debt", 1),
            lambda p: setattr(p[1].phy.dram.maintenance, "interval", 390),
            lambda p: p[0].phy.dram.maintenance.raa[0].__setitem__(0, 60),
            lambda p: p.__setitem__(0, None),
        ):
            w, p, ps = renewed()
            mutate(p)
            with self.assertRaises(Violation):
                w.begin(ps, p, True)
        w, p, ps = renewed()
        with self.assertRaisesRegex(Violation, "host recovery"):
            w.begin(ps, p, False)

    def test_freshness_setup_timeout_and_incoming_maintenance_reject(self):
        w, p, ps = renewed()
        with self.assertRaisesRegex(Violation, "stale maintenance readiness"):
            w.begin(ps + 1250, p, True)
        w, p, ps = renewed()
        w.diagnostics[1]["tick"] -= 1
        with self.assertRaisesRegex(Violation, "stale maintenance diagnostic"):
            w.begin(ps, p, True)
        w, p, ps = renewed()
        with self.assertRaisesRegex(Violation, "without fresh"):
            w.request(ps, p)
        w.begin(ps, p, True)
        with self.assertRaisesRegex(Violation, "budget"):
            w.request(ps + (w.budget["setup_ck"] + 1) * 1250, p)
        with self.assertRaisesRegex(Violation, "arrived"):
            w.observe(raw(0, 101, cs=1, ca=0x2038), p[0])

    def test_workload_cannot_exceed_budget_or_preserve_readiness_through_reset(self):
        w, p, ps = renewed()
        w.begin(ps, p, True)
        for _ in range(14):
            w.physical_transfer()
        with self.assertRaisesRegex(Violation, "physical workload"):
            w.physical_transfer()
        for tick in range(101, 105):
            w.observe(raw(0, tick, cs=1, ca=7), p[0])
        with self.assertRaisesRegex(Violation, "ACT workload"):
            w.observe(raw(0, 105, cs=1, ca=7), p[0])
        w, p, ps = renewed()
        w.observe(raw(1, 100, destructive=1), None)
        with self.assertRaisesRegex(Violation, "renewal"):
            w.begin(ps, p, True)

    def test_setup_equality_and_request_time_recheck(self):
        for delta in (-1, 0, 1):
            w, p, ps = renewed()
            w.begin(ps, p, True)
            tick = 100 + w.budget["setup_ck"] + delta
            for c in range(2):
                w.observe(raw(c, tick - 1), p[c])
                w.diagnostic(diagnostic(raw(c, tick - 1)))
                w.observe(raw(c, tick), p[c])
            if delta > 0:
                with self.assertRaisesRegex(Violation, "setup exceeded"):
                    w.request(raw(0, tick)["ps"] + 1, p)
            else:
                w.request(raw(0, tick)["ps"] + 1, p)
        w, p, ps = renewed()
        w.begin(ps, p, True)
        for c in range(2):
            w.diagnostic(diagnostic(raw(c, 100), zq_request=1))
            w.observe(raw(c, 101), p[c])
        with self.assertRaisesRegex(Violation, "pending maintenance"):
            w.request(raw(0, 101)["ps"] + 1, p)

    def test_misphased_missing_diagnostic_and_infeasible_profile(self):
        w, p, ps = renewed()
        d = diagnostic(raw(0, 100))
        for change in (dict(ps=d["ps"] + 1), dict(tick=99)):
            with self.assertRaisesRegex(Violation, "misphased"):
                w.diagnostic(d | change)
        for key in WINDOW_FIELDS:
            missing = d.copy()
            del missing[key]
            with self.subTest(field=key), self.assertRaisesRegex(Violation, "incomplete"):
                w.diagnostic(missing)
        profile = profile_binding()
        profile["dfi_wck"]["twrdata_delay"] = 10000
        with self.assertRaises(Violation):
            window_budget(profile)

    def test_phase_gate_requires_every_new_field_and_both_phases(self):
        fixture = fixtures.PhaseTests()
        fixture.setUp()
        for kind in ("window_sample", "window_edge"):
            with self.assertRaisesRegex(Violation, "maintenance phase"):
                qualify_phases([e for e in fixture.records if e["kind"] != kind], fixture.header)
            for key in WINDOW_FIELDS:
                rows = copy.deepcopy(fixture.records)
                del next(e for e in rows if e["kind"] == kind)[key]
                with self.subTest(kind=kind, field=key), self.assertRaisesRegex(Violation, "maintenance phase"):
                    qualify_phases(rows, fixture.header)
        rows = copy.deepcopy(fixture.records)
        next(e for e in rows if e["kind"] == "window_edge")["master_age"] = 1
        with self.assertRaisesRegex(Violation, "registered phase binding"):
            qualify_phases(rows, fixture.header)
        with self.assertRaisesRegex(Violation, "unqualified"):
            qualify_phases(fixture.records, fixture.header | dict(version=4))
