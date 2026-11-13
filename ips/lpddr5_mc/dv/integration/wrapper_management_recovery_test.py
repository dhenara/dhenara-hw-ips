"""Recovery phase sensitivity shared with the actual coupled-RTL seam run."""

import copy
import unittest

from system.host import Violation, require
from wrapper_recovery import RecoveryConsumption


def replay(records):
    checker = RecoveryConsumption()
    for e in records:
        if e["kind"] == "begin":
            checker.begin()
        elif e["kind"] == "baseline":
            checker.require_complete()
        elif e["kind"] == "sample":
            checker.sample(e, bool(e["source_settled"]), True)
        elif e["kind"] == "management_edge":
            checker.consume(e, bool(e["source_settled"]))
    return checker.finish()


def sensitivity(records):
    """Mutate copies of actual records; retain originals and each verdict."""
    history = replay(records)
    require(len(history) == 4, "missing both-channel consecutive reset coverage")
    checks = []
    for channel in range(2):
        h = next(h for h in history if h["channel"] == channel)
        a, w, tail = h["assertion"], h["consumption"], h["invalidation"]

        def changed(target, fields, channel=channel):
            return [
                e | fields
                if e["kind"] == target["kind"] and e.get("channel") == channel and e["ps"] == target["ps"]
                else e
                for e in copy.deepcopy(records)
            ]

        variants = {
            "stuck_config": changed(tail, dict(config_valid=1)),
            "stuck_prepared": changed(tail, dict(prepared_valid=1)),
            "stretched_reset": changed(tail, dict(destructive=1)),
            "skipped_boot": changed(a, dict(boot=a["boot"] + 1)),
            "boot_wrap": changed(a, dict(boot=0x100000000)),
            "unconfirmed_pulse": changed(a, dict(confirmed=0)),
            "reused_boot": changed(a, dict(boot=a["boot"] - 1)),
            "early_trust": changed(a, dict(trusted=1)),
            "early_training": changed(a, dict(trained=1)),
            "early_admission": changed(a, dict(admission=1)),
            "early_enable": changed(a, dict(enabled=1)),
            "early_issue": changed(w, dict(issue=1)),
            "early_publication": changed(a, dict(config=a["config"] + 1)),
            "unsettled_reset": changed(a, dict(source_settled=0)),
            "unsettled_consumption": changed(w, dict(source_settled=0)),
            "stale_witness": changed(w, dict(tick=w["tick"] - 1)),
            "wrong_channel": changed(w, dict(channel=1 - channel)),
            "late_witness": changed(w, dict(ps=w["ps"] + 1)),
            "missing_witness": [e for e in records if e != w],
            "duplicate_witness": records[: records.index(w) + 1] + [w] + records[records.index(w) + 1 :],
            "missing_tail": [e for e in records if e != tail],
            "unfinished_tail": [e for e in records if e["ps"] <= w["ps"]],
            "stale_confirmation": [e | dict(confirmed=1) if e.get("channel") == channel else e for e in records],
            "unrequested_confirmation": [
                e | dict(reset_request=0) if e.get("channel") == channel else e for e in records
            ],
        }
        trigger = next(
            e
            for e in records
            if e["kind"] == "management_edge" and e.get("channel") == channel and e["ps"] == a["ps"] - 625
        )
        variants["unfinished_command_tail"] = [e for e in records if e["ps"] <= trigger["ps"]]
        variants["missing_command"] = changed(trigger, dict(recover_command=0))
        variants["unauthorized_reset"] = changed(trigger, dict(reset_authorized=0))
        # A one-cycle-late clear cannot pass by eventually becoming invalid.
        variants["delayed_invalidation"] = changed(tail, dict(config_valid=1))
        for name, mutant in variants.items():
            try:
                replay(mutant)
            except Violation as error:
                checks.append(dict(channel=channel, mutation=name, verdict="rejected", error=str(error)))
            else:
                raise AssertionError(f"Insensitive recovery checker: {channel}/{name}")
        # Earlier invalidity is legal if the actual consumed pulse and clear
        # tail still exist. Do not force visibility to persist until consumption.
        earlier = changed(a, dict(config_valid=0, prepared_valid=0))
        earlier = [e | dict(config_valid=0, prepared_valid=0) if e == w else e for e in earlier]
        require(len(replay(earlier)) == 4, "earlier valid invalidation rejected")
        checks.append(dict(channel=channel, mutation="earlier_invalidation", verdict="accepted"))
    return checks


def fixture():
    """Explicit phase fixture for fast units; actual RTL is tested separately."""
    records = []
    for cycle in range(2):
        records.append(dict(kind="begin", ps=cycle * 10000))
        for t in range(4):
            for phase in ("sample", "management_edge"):
                for c in range(2):
                    # Each new case starts after the prior invalidation.
                    pulse, clear = t == 2, t == 3
                    e = dict(
                        kind=phase,
                        ps=cycle * 10000 + 1250 + t * 1250 + (625 if phase == "management_edge" else 0),
                        tick=cycle * 8 + t,
                        channel=c,
                        boot=cycle + int(t >= 2),
                        config=cycle + 1,
                        config_valid=int(not clear),
                        prepared_valid=0,
                        destructive=int(pulse),
                        trusted=0,
                        trained=0,
                        train_request=0,
                        training_epoch=cycle,
                        enabled=0,
                        admission=0,
                        issue=0,
                        state=0 if t >= 2 else 5,
                        owner=1 if t >= 2 else 4,
                        confirmed=int(t > 0),
                        reset_request=int(t < 2),
                        reset_authorized=int(t == 1),
                        recover_command=int(t == 1 and phase == "management_edge"),
                        source_settled=1,
                    )
                    records.append(e)
        records.append(dict(kind="baseline", ps=cycle * 10000 + 6000))
    # Gap between cycles is deliberate only between separate checker instances
    # in unit tests; the timed seam retains a fully contiguous real clock.
    return records


class RecoveryTests(unittest.TestCase):
    def test_precise_registered_consumption_and_sensitivity(self):
        # Keep all samples contiguous across both synthetic recoveries.
        rows = fixture()
        for e in rows:
            if e["ps"] >= 10000:
                e["ps"] -= 5000
                if "tick" in e:
                    e["tick"] -= 4
        self.assertEqual(len(replay(rows)), 4)
        self.assertEqual(len(sensitivity(rows)), 58)

    def test_cross_channel_fence_and_epoch_reuse(self):
        rows = fixture()
        rows = [e for e in rows if e["ps"] < 10000]
        a = next(e for e in rows if e["kind"] == "sample" and e.get("channel") == 0 and e.get("destructive"))
        for fields in (dict(train_request=1), dict(config_valid=0, config=9), dict(prepared_valid=1, trained=1)):
            mutant = [e | fields if e == a else e for e in rows]
            with self.assertRaises(Violation):
                replay(mutant)
        # Baseline cannot use assertion alone as completed invalidation.
        early = [e for e in rows if e["ps"] <= a["ps"]]
        early.append(dict(kind="baseline", ps=a["ps"] + 1))
        with self.assertRaisesRegex(Violation, "completed configuration"):
            replay(early)

    def test_all_channel_fence_after_only_one_consumption(self):
        rows = [e for e in fixture() if e["ps"] < 10000]
        target = next(e for e in rows if e["kind"] == "sample" and e.get("channel") == 0 and e.get("tick") == 3)
        prefix = rows[: rows.index(target) + 1]
        for fields in (dict(train_request=1), dict(config_valid=1), dict(prepared_valid=1), dict(admission=1)):
            checker = RecoveryConsumption()
            for e in prefix:
                if e["kind"] == "begin":
                    checker.begin()
                elif e["kind"] == "sample":
                    checker.sample(e, True, True)
                else:
                    checker.consume(e, True)
            self.assertEqual(checker.completed, {0})
            with self.assertRaisesRegex(Violation, "before both reset consumptions"):
                checker.service(target | fields)

    def test_retired_epoch_training_and_service_remain_fenced(self):
        checker = RecoveryConsumption()
        checker.retired[1] = 2
        checker.completed = {0, 1}
        e = dict(
            channel=1,
            config_valid=1,
            config=3,
            trained=1,
            trusted=1,
            training_epoch=1,
            boot=1,
            train_request=0,
            enabled=0,
            admission=0,
            issue=0,
        )
        checker.service(e)
        for fields in (
            dict(config=2),
            dict(training_epoch=0),
            dict(trusted=0, trained=0, admission=1),
            dict(trusted=0, config_valid=0, train_request=1),
        ):
            with self.assertRaises(Violation):
                checker.service(e | fields)


if __name__ == "__main__":
    unittest.main()
