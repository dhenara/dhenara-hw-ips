"""Source-only automatic-power witness and persistent-profile sensitivity."""

import contextlib
import copy
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contracts import commands
from system.host import Violation
from wrapper_management import PhysicalStream, digest, profile_binding
from wrapper_power import DeclineWitness, PowerRun, PowerWitness, qualify_power_binding, qualify_power_edges


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
    def test_residence_requires_the_full_selected_ck_interval(self):
        for policy, cycles in ((1, 40), (2, 3500)):
            for delta in (-1, 0, 1):
                with self.subTest(policy=policy, delta_ps=delta):
                    run = PowerRun.__new__(PowerRun)
                    run.policy = policy
                    run.p = {"ck_ps": 1250}
                    run.markers = dict.fromkeys(
                        ("power_begin", "power_dirty", "power_deferred", "power_source_settled", "power_sleeping"),
                        1001,
                    )
                    marker = {"name": "power_wake_offer", "ps": 1001 + cycles * 1250 + delta}
                    if delta < 0:
                        with self.assertRaisesRegex(Violation, "residence shorter"):
                            run.marker(marker)
                    else:
                        run.marker(marker)

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


class DeclineTests(unittest.TestCase):
    @staticmethod
    def status(**changes):
        return (
            dict(
                reset_n=1,
                trusted=1,
                config_valid=1,
                trained=1,
                fault=0,
                host_fault=0,
                destructive=0,
                memory_reset=0,
                boot=0,
                config=1,
                training_epoch=0,
            )
            | changes
        )

    def witness(self):
        w = DeclineWitness(4)
        w.status(self.status())
        for tick in range(128):
            consume(w, edge(tick), source=False)
        return w

    def test_complete_no_ack_opportunity_is_a_normal_decline(self):
        w = self.witness()
        for tick in range(128, 132):
            consume(w, edge(tick, ctrl_req=1, data_req=1))
        consume(w, edge(132))
        result = w.finish()
        self.assertEqual((result["declines"], result["entries"], result["exits"]), (1, 0, 0))

    def test_missing_truncated_short_and_long_opportunities_fail(self):
        with self.assertRaisesRegex(Violation, "complete power decline"):
            self.witness().finish()
        w = self.witness()
        consume(w, edge(128, ctrl_req=1, data_req=1))
        with self.assertRaisesRegex(Violation, "complete power decline"):
            w.finish()
        with self.assertRaisesRegex(Violation, "withdrew before"):
            consume(w, edge(129))
        w = self.witness()
        for tick in range(128, 132):
            consume(w, edge(tick, ctrl_req=1, data_req=1))
        with self.assertRaisesRegex(Violation, "exceeded response deadline"):
            consume(w, edge(132, ctrl_req=1, data_req=1))

    def test_partial_or_complete_ack_is_not_a_decline(self):
        for ctrl, data in ((1, 0), (0, 1), (1, 1)):
            with self.subTest(ctrl=ctrl, data=data):
                with self.assertRaisesRegex(Violation, "decline received ACK"):
                    consume(self.witness(), edge(128, ctrl_req=1, data_req=1, ctrl_ack=ctrl, data_ack=data))

    def test_decline_cannot_enter_physical_sleep(self):
        with self.assertRaisesRegex(Violation, "entered physical sleep"):
            self.witness().physical(sleeping=True)

    def test_decline_retains_trust_and_exact_epochs(self):
        for field in (
            "trusted",
            "config_valid",
            "trained",
            "reset_n",
            "fault",
            "host_fault",
            "destructive",
            "memory_reset",
            "boot",
            "config",
            "training_epoch",
        ):
            w = self.witness()
            altered = self.status()
            altered[field] ^= 1
            with self.subTest(field=field), self.assertRaises(Violation):
                w.status(altered)

    def test_source_acceptance_requires_both_completed_declines(self):
        run = PowerRun.__new__(PowerRun)
        run.power_case, run.active = "decline", True
        run.markers = {"power_decline_offer": 1}
        run.power = [self.witness(), self.witness()]
        for tick in range(128, 132):
            consume(run.power[0], edge(tick, ctrl_req=1, data_req=1))
        consume(run.power[0], edge(132))
        with self.assertRaisesRegex(Violation, "before complete normal decline"):
            run.event({"kind": "ar"})

    def test_request_count_uses_consumed_edges_and_not_inclusive_time_endpoints(self):
        for response_ck in (1, 2, 4, 8):
            with self.subTest(response_ck=response_ck):
                w = DeclineWitness(response_ck)
                w.status(self.status())
                for tick in range(128):
                    consume(w, edge(tick), source=False)
                # Launch was low before NBA; the first high consuming edge
                # starts at age zero. The low observation has age=response_ck.
                for age in range(response_ck):
                    consume(w, edge(128 + age, ctrl_req=1, data_req=1))
                consume(w, edge(128 + response_ck))
                self.assertEqual(w.finish()["declines"], 1)

    def test_power_selection_keeps_unsolicited_management_case_state_separate(self):
        run = PowerRun(profile_binding(), 1, [], case="decline")
        # An ordinary host settlement is valid outside an unsolicited case.
        # Reusing Run.case for a selector would misinterpret this event.
        run.event({"kind": "host_settlement", "tick": 0, "ps": 1250, "fatal": 0})

    def test_missing_terminal_marker_and_stalled_address_tail_reject(self):
        run = PowerRun(profile_binding(), 1, [], case="decline")
        with self.assertRaisesRegex(Violation, "missing power terminal marker"):
            run.finish()
        run.finished = True
        run.addresses.held[0, "ar"] = (0, 204, 0)
        with self.assertRaisesRegex(Violation, "unconsumed source address tail"):
            run.finish()

    def test_missing_last_consumed_power_or_management_edge_rejects(self):
        records = []
        for tick in range(2):
            for channel in range(2):
                records.extend(
                    (
                        edge(tick, channel=channel, kind="management_edge"),
                        edge(tick, channel=channel, kind="power_edge"),
                    )
                )
        self.assertEqual(qualify_power_edges(records), 4)
        for missing_kind in ("management_edge", "power_edge"):
            altered = [e for e in records if not (e["channel"] == 1 and e["tick"] == 1 and e["kind"] == missing_kind)]
            with self.subTest(missing_kind=missing_kind), self.assertRaisesRegex(Violation, "power witness"):
                qualify_power_edges(altered)


class PowerBindingTests(unittest.TestCase):
    def test_profile_policy_phase_and_case_are_independently_bound(self):
        p = profile_binding()
        header = dict(
            kind="header",
            profile_sha256=digest(p),
            power_policy=1,
            power_phase="posedge_before_NBA",
            power_case="decline",
        )
        qualify_power_binding(header, p, 1, "decline")
        for field, altered in (
            ("profile_sha256", "wrong"),
            ("power_policy", 2),
            ("power_phase", "negedge"),
            ("power_case", "entry_wake"),
        ):
            with self.subTest(field=field), self.assertRaises(Violation):
                qualify_power_binding(header | {field: altered}, p, 1, "decline")
        missing = dict(header)
        del missing["power_case"]
        with self.assertRaisesRegex(Violation, "power case"):
            qualify_power_binding(missing, p, 1, "decline")
        # Old entry/wake traces retain their default; they cannot stand in for
        # a newly selected decline trace.
        qualify_power_binding(missing, p, 1, "entry_wake")

    def test_runner_rejects_unbound_decline_before_preparation(self):
        from importlib.util import module_from_spec, spec_from_file_location

        # Other wrapper tests also load dv/channel/check.py. Bind this runner
        # by its own path rather than whichever module last occupied "check".
        spec = spec_from_file_location("power_integration_runner", Path(__file__).with_name("check.py"))
        self.assertIsNotNone(spec)
        runner = module_from_spec(spec)
        spec.loader.exec_module(runner)

        cases = (
            (["--wrapper-power-case", "decline"], "matching wrapper power policy"),
            (["--wrapper-power-policy", "2", "--wrapper-power-case", "decline"], "matching wrapper power policy"),
            (
                ["--wrapper-power-policy", "1", "--wrapper-power-case", "decline", "--wrapper-fsp-case", "0"],
                "separate selections",
            ),
        )
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            for args, reason in cases:
                stderr = io.StringIO()
                with (
                    self.subTest(args=args),
                    patch(
                        "sys.argv",
                        ["check.py", "--family", "controller", "--report", str(Path(temporary) / "unused.json"), *args],
                    ),
                    contextlib.redirect_stderr(stderr),
                ):
                    with self.assertRaises(SystemExit) as error:
                        runner.main()
                    self.assertEqual(error.exception.code, 2)
                    self.assertIn(reason, stderr.getvalue())
            self.assertFalse((Path(temporary) / "unused.json").exists())


if __name__ == "__main__":
    unittest.main()
