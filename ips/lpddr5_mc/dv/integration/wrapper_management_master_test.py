"""Synthetic source-gate and sensitivity tests; no HDL or historical replay."""

import copy
import unittest

from system.host import Violation
from wrapper_management import PhysicalStream, Run, Source, profile_binding
from wrapper_management_test import sample
from wrapper_master import PhysicalCommands, RfmTimeout, active_refresh, settled_abort


def active_fixture():
    records = []
    for tick in range(178):
        master = int(1 <= tick <= 175)
        s = dict(
            kind="sample",
            channel=0,
            tick=tick,
            master=master,
            refresh_request=int(tick == 3),
            rank_valid=int(tick != 171),
            rank_sr=0,
            admission=0,
            issue=0,
            settled=1,
            drained=1,
            phymstr_req=int(tick < 175),
            refresh_done=int(tick == 3),
        )
        records.append(s)
        records.append(s | dict(kind="management_edge"))
        for phase in ("window_sample", "window_edge"):
            records.append(
                dict(
                    kind="master_state",
                    phase=phase,
                    channel=0,
                    tick=tick,
                    maint_ck=tick,
                    external=int(tick == 4),
                    external_age=1,
                    last_refresh=3 if tick >= 5 else 0,
                    recovered=int(tick < 5 or tick >= 171),
                )
            )
    return records, [dict(channel=0, tick=3, model_ck=3, ready=171)]


def physical_fixture():
    p = profile_binding()
    records = []
    for tick in range(6):
        for c in (0, 1):
            s = dict(
                kind="sample",
                channel=c,
                tick=tick,
                ps=tick * 1250,
                master=0,
                reset_n=1,
                memory_reset=0,
                destructive=0,
                boot=0,
                cs=int(tick == 0),
                ca=0x2038 if tick == 0 else 0,
            )
            records.extend(
                (
                    s,
                    dict(
                        kind="physical_ca",
                        channel=c,
                        tick=tick,
                        ps=s["ps"],
                        phy=0,
                        cs=int(tick == 2),
                        ca=0x2038 if tick == 2 else 0,
                    ),
                )
            )
            for phase, dt in (("window_sample", 0), ("window_edge", 625)):
                records.append(dict(kind="master_state", phase=phase, channel=c, tick=tick, ps=s["ps"] + dt))
        records.append(dict(kind="host_settlement", tick=tick, ps=tick * 1250 + 625))
    return records, p


class ExtendedMasterTests(unittest.TestCase):
    def test_external_ca_uses_existing_physical_model_and_full_recovery(self):
        records, p = physical_fixture()
        pins = PhysicalCommands(records, p)
        physical = PhysicalStream(p)
        memory = physical.phy.dram.memory
        before = list(physical.phy.dram.maintenance.raa[0])
        physical.sample(sample(cs=0, ca=0), command=(1, 0x2038))
        pin = dict(channel=0, tick=2)
        pins.applied_ref(pin, physical, before)
        self.assertIs(physical.phy.dram.memory, memory)
        self.assertEqual(pins.applied[0]["ready"], 170)
        physical.phy.dram.maintenance.last_refresh[0] += 1
        with self.assertRaisesRegex(Violation, "retention"):
            pins.applied_ref(pin, physical, before)

    def test_physical_pipeline_and_missing_tail(self):
        records, p = physical_fixture()
        pins = PhysicalCommands(records, p)
        s = next(e for e in records if e["kind"] == "sample")
        self.assertEqual(pins.command(s)[0], (1, 0x2038))
        for mutation in ("delay", "missing", "duplicate", "owner", "phase", "missing_host"):
            bad = copy.deepcopy(records)
            target = next(e for e in bad if e["kind"] == "physical_ca" and e["tick"] == 2)
            if mutation == "delay":
                target["cs"] = 0
            elif mutation == "missing":
                bad.remove(target)
            elif mutation == "duplicate":
                bad.append(target)
            elif mutation == "owner":
                target["phy"] = 1
            elif mutation == "phase":
                target["ps"] += 1
            else:
                bad = [e for e in bad if e["kind"] != "host_settlement" or e["tick"] != 1]
            with self.subTest(mutation=mutation), self.assertRaises(Violation):
                PhysicalCommands(bad, p)

    def test_first_overdue_request_age_and_containment(self):
        for mutation in (None, "early_ack", "late_fault", "early_decision", "stretched_age", "external_error"):
            checker = RfmTimeout(3)

            def replay(checker=checker, mutation=mutation):
                for tick in range(5):
                    e = dict(
                        tick=tick,
                        master=int(mutation == "early_ack" and tick == 1),
                        phymstr_req=1,
                        error=int(mutation == "external_error" and tick == 1),
                    )
                    w = dict(
                        master_pending=int(tick > 0), master_age=max(0, tick - 1), life_failure=12 if tick == 4 else 0
                    )
                    if mutation == "stretched_age" and tick == 2:
                        w["master_age"] += 1
                    if mutation == "early_decision" and tick == 3:
                        w["life_failure"] = 12
                    checker.edge(e, w)
                checker.failed(
                    dict(tick=6 if mutation == "late_fault" else 5, master=0, trusted=0), dict(life_fault_code=12)
                )
                return checker.finish()

            if mutation is None:
                self.assertEqual(replay(), dict(request=0, decision=4, fault=5))
            else:
                with self.subTest(mutation=mutation), self.assertRaises(Violation):
                    replay()

    def test_actual_source_and_bridge_abort_both_required(self):
        s = dict(wrapper=1, core=1, bridge=1, abort=1, admission_busy=0, fatal=1)
        settled_abort(s, True)
        for field in s:
            with self.subTest(field=field), self.assertRaises(Violation):
                settled_abort(s | {field: 1 - s[field]}, True)
        with self.assertRaises(Violation):
            settled_abort(s, False)

    def test_response_failure_begins_at_observed_host_fatal(self):
        run = Run(profile_binding())
        run.case = dict(id=4, failed=True)
        run.timeout.fatal = 7
        run.marker(dict(name="error", ps=100))
        self.assertFalse(run.source.host.faulted)
        source = run.source
        source.event(dict(kind="ar", port=0, id=42, address=0, length=1, size=4, burst=1, lock=0))
        tag = source.tags[0, 42, False]
        source.host.physical_done(tag)
        source.host.integrity_done(tag)
        response = dict(kind="r", port=0, id=42, data=0, response=0, last=0, ready=0)
        source.event(response)
        run.event(dict(kind="host_settlement", tick=8, fatal=1))
        self.assertTrue(source.host.faulted)
        source.event(response | dict(ready=1))
        with self.assertRaisesRegex(Violation, "response code"):
            source.event(response | dict(last=1, ready=1))
        source.event(response | dict(response=2, last=1, ready=1))
        self.assertTrue(source.settled())

    def test_active_physical_execution_done_application_recovery_return(self):
        records, applied = active_fixture()
        p = profile_binding()
        self.assertEqual(active_refresh(records, p, applied)["returned"], 175)
        for mutation in (
            "missing_execution",
            "late_execution",
            "duplicate_execution",
            "missing_done",
            "wrong_rank",
            "early_recovery",
            "missing_application",
            "late_origin",
            "stale_report",
        ):
            bad, executions = copy.deepcopy(records), copy.deepcopy(applied)
            if mutation == "missing_execution":
                executions.clear()
            elif mutation == "duplicate_execution":
                executions.append(executions[0])
            elif mutation == "late_execution":
                executions[0]["tick"] = 5
            else:
                for e in bad:
                    if mutation == "missing_done" and e["kind"] == "management_edge":
                        e["refresh_done"] = 0
                    if mutation == "wrong_rank" and e["kind"] == "management_edge" and e["refresh_done"]:
                        e["refresh_done"] = 2
                    if e["kind"] == "master_state":
                        if mutation == "early_recovery" and e["tick"] == 170:
                            e["recovered"] = 1
                        if mutation == "missing_application" and e["phase"] == "window_sample" and e["tick"] == 4:
                            e["external"] = 0
                        if mutation == "late_origin" and e["tick"] >= 5:
                            e["last_refresh"] += 1
                    if mutation == "stale_report" and e["kind"] == "management_edge":
                        e["rank_valid"] = 1
            with self.subTest(mutation=mutation), self.assertRaises(Violation):
                active_refresh(bad, p, executions)

    def test_reuse_axi_id_only_after_actual_final_handshake(self):
        source = Source()
        address = dict(kind="ar", port=0, id=42, address=0, length=0, size=4, burst=1, lock=0)
        source.event(address)
        with self.assertRaises(Violation):
            source.event(address)
        tag = source.tags[0, 42, False]
        source.host.physical_done(tag)
        source.host.integrity_done(tag)
        source.event(dict(kind="r", port=0, id=42, data=0, response=0, last=1, ready=1))
        source.event(address)
        self.assertEqual(source.counts["ar"], 2)


if __name__ == "__main__":
    unittest.main()
