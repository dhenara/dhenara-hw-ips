"""Valid and deliberately faulty host seam accounting; no hardware/replay."""

import copy
import unittest

from host_completions import MAX_COUNTER, HostCompletions
from system.host import Violation


def request(channel=0, tag=13, **changes):
    return (
        dict(
            tag=tag,
            generation=3,
            boot=0,
            config=1,
            write=1,
            channel=channel,
            port=0,
            axi_id=43,
            address=0,
            bytes=64,
            mask=(1 << 64) - 1,
            data=17,
        )
        | changes
    )


def row(q, slot=0, **changes):
    return (
        {k: q[k] for k in ("tag", "generation", "boot", "config", "write", "channel")}
        | dict(
            slot=slot,
            valid=1,
            pending=1,
            physical=0,
            integrity=0,
            port=0,
            source_slot=0,
            source_generation=3,
            axi_id=43,
            merged=0,
            follower=0,
            first=0,
            beats=4,
            length=3,
        )
        | changes
    )


def completion(q, **changes):
    return (
        {k: q[k] for k in ("tag", "generation", "boot", "config", "write", "data")}
        | dict(
            physical=1,
            integrity=1,
            failed=0,
            response=0,
            match_slot=0,
        )
        | changes
    )


def channel(c, q=None, result=None, **changes):
    return (
        dict(
            channel=c,
            request_valid=int(q is not None),
            request_ready=1,
            completion_valid=int(result is not None),
            completion_ready=1,
            inline=1,
            posted=0,
            write_link=0,
            request=q,
            completion=result,
        )
        | changes
    )


def edge(tick, rows=(), requests=(), results=(), count=0, **changes):
    qs, vs = dict(requests), dict(results)
    return (
        dict(
            kind="host_edge",
            tick=tick,
            ps=625 + tick * 1250,
            reset_n=1,
            fatal=0,
            fault=0,
            recover=0,
            enable=1,
            settled=0,
            config_valid=1,
            cfg_valid=0,
            cfg_ready=0,
            config_boot=0,
            config_epoch=1,
            cfg_boot=0,
            cfg_epoch=1,
            count=count,
            external_abort=1,
            posted_early=0,
            ledger=list(rows),
            channels=[channel(c, qs.get(c), vs.get(c)) for c in range(2)],
        )
        | changes
    )


class Scenario:
    def __init__(self, failed=True, q=None, participants=None, split=False):
        self.model = HostCompletions()
        self.model.begin(1 if failed else 0, failed)
        self.q = request() if q is None else q
        self.rows = [row(self.q)] if participants is None else participants
        self.tick = 0
        self.feed(reset_n=0, enable=0, settled=1, config_valid=0)
        e = edge(self.tick, self.rows, [(self.q["channel"], self.q)])
        if split:
            e["channels"][self.q["channel"]].update(inline=0, write_link=1, posted=1)
        self.model.edge(e)
        self.tick += 1

    def feed(self, rows=(), requests=(), results=(), **changes):
        e = edge(self.tick, rows, requests, results, **changes)
        self.model.edge(e)
        self.tick += 1
        return e

    def abort(self):
        self.feed(self.rows, fatal=1, enable=0, config_valid=0)

    def drop(self, **changes):
        self.feed(results=[(self.q["channel"], completion(self.q, match_slot=-1, **changes))], enable=0, config_valid=0)

    def recovered(self, count=1):
        self.feed(count=count, recover=1, enable=0, settled=1, config_valid=0)
        self.feed(count=count, enable=0, settled=1, config_valid=0)

    def baseline(self, count=1, **changes):
        e = dict(ps=self.model.previous["ps"] + 625, count=count, boot=1)
        samples = [
            dict(
                boot=1,
                confirmed=1,
                reset_request=0,
                reset_n=1,
                settled=1,
                destructive=0,
                memory_reset=0,
                enabled=0,
                host_fault=0,
                fault=0,
                config_valid=0,
                trained=0,
            )
            for _ in range(2)
        ]
        self.model.baseline(
            e, changes.get("samples", samples), changes.get("settled", True), changes.get("channels", {0, 1})
        )
        return e


class HostCompletionTests(unittest.TestCase):
    def test_normal_completion_and_next_edge_counter(self):
        s = Scenario(failed=False)
        s.feed(s.rows, results=[(0, completion(s.q))])
        s.feed()
        s.model.checkpoint(dict(count=0))
        self.assertEqual(s.model.finish()["counts"]["matched"], 1)

    def test_abort_drop_recovery_preserves_counter_and_history(self):
        s = Scenario()
        s.abort()
        s.drop()
        s.recovered()
        e = s.baseline()
        s.model.checkpoint(e)
        report = s.model.finish()
        self.assertEqual(report["final_count"], 1)
        self.assertEqual([h["kind"] for h in report["history"]], ["accept", "cancel", "drop", "counter"])
        self.assertEqual(report["baselines"][0]["history_entries"], 4)

    def test_simultaneous_matched_completion_and_fatal_is_not_a_drop(self):
        s = Scenario()
        s.feed(s.rows, results=[(0, completion(s.q))], fatal=1, enable=0)
        s.feed(enable=0, config_valid=0)
        self.assertEqual(s.model.counts["matched"], 1)
        self.assertEqual(s.model.counts["unmatched"], 0)
        self.assertFalse(any(h["kind"] == "cancel" for h in s.model.history))
        with self.assertRaisesRegex(Violation, "counter update"):
            s.feed(count=1, enable=0)

    def test_unknown_wrong_identity_channel_type_and_new_epoch(self):
        for field, value in (("tag", 14), ("generation", 4), ("boot", 1), ("config", 2), ("write", 0)):
            with self.subTest(field=field):
                s = Scenario()
                s.abort()
                with self.assertRaisesRegex(Violation, "previously accepted"):
                    s.drop(**{field: value})
        s = Scenario()
        s.abort()
        with self.assertRaisesRegex(Violation, "previously accepted"):
            s.feed(results=[(1, completion(s.q, match_slot=-1))])

    def test_old_epoch_and_fault_level_do_not_authorize_unknown_drop(self):
        s = Scenario()
        s.abort()
        with self.assertRaisesRegex(Violation, "previously accepted"):
            s.feed(results=[(0, completion(request(tag=99), match_slot=-1))], fault=1)

    def test_unexplained_missing_increment_and_missing_event(self):
        s = Scenario()
        s.abort()
        s.drop()
        with self.assertRaisesRegex(Violation, "counter update"):
            s.feed(count=0)
        s = Scenario()
        s.abort()
        with self.assertRaisesRegex(Violation, "counter update"):
            s.feed(count=1)
        s = Scenario(failed=False)
        with self.assertRaisesRegex(Violation, "counter update"):
            s.feed(s.rows, count=1)

    def test_fabricated_match_or_missing_ledger_cannot_excuse_event(self):
        s = Scenario()
        with self.assertRaisesRegex(Violation, "match selection"):
            s.feed(s.rows, results=[(0, completion(s.q, match_slot=-1))])
        s = Scenario()
        with self.assertRaisesRegex(Violation, "participants changed"):
            s.feed(results=[(0, completion(s.q, match_slot=0))])

    def test_duplicate_and_already_completed_identity(self):
        for canceled in (False, True):
            s = Scenario()
            if canceled:
                s.abort()
                s.drop()
            else:
                s.feed(s.rows, results=[(0, completion(s.q))])
            with self.assertRaisesRegex(Violation, "already completed"):
                s.feed(results=[(0, completion(s.q, match_slot=-1))], count=int(canceled))

    def test_both_simultaneous_drops_are_checked_for_one_counter_advance(self):
        s = Scenario()
        q = request(channel=1, tag=14)
        r = row(q, slot=1)
        s.feed([*s.rows, r], requests=[(1, q)])
        s.feed([*s.rows, r], fatal=1, enable=0)
        s.feed(results=[(0, completion(s.q, match_slot=-1)), (1, completion(q, match_slot=-1))])
        s.feed(count=1, enable=0)
        self.assertEqual(s.model.counts["unmatched"], 2)
        self.assertEqual(s.model.counts["unmatched_edges"], 1)
        s = Scenario()
        s.abort()
        with self.assertRaisesRegex(Violation, "previously accepted"):
            s.feed(results=[(0, completion(s.q, match_slot=-1)), (1, completion(q, match_slot=-1))])

    def test_missing_channel_or_clock_edge_is_rejected(self):
        for defect in ("channel", "tick", "timestamp"):
            s = Scenario()
            e = edge(s.tick, s.rows)
            if defect == "channel":
                e["channels"].pop()
            elif defect == "tick":
                e["tick"] += 1
            else:
                e["ps"] += 1
            with self.subTest(defect=defect), self.assertRaises(Violation):
                s.model.edge(e)

    def test_split_physical_and_final_completions(self):
        s = Scenario(failed=False, split=True)
        s.feed(s.rows, results=[(0, completion(s.q, integrity=0))])
        s.feed([row(s.q, physical=1)], results=[(0, completion(s.q))])
        s.feed()
        self.assertEqual(s.model.finish()["counts"]["matched"], 2)

    def test_matched_physical_on_abort_then_canceled_final(self):
        s = Scenario(split=True)
        s.feed(s.rows, results=[(0, completion(s.q, integrity=0))], fatal=1, enable=0)
        s.drop()
        s.recovered()
        s.baseline()
        self.assertEqual(s.model.finish()["counts"]["unmatched"], 1)

    def test_split_duplicate_missing_notice_and_unsupported_phase(self):
        s = Scenario(split=True)
        with self.assertRaisesRegex(Violation, "before physical notice"):
            s.feed(s.rows, results=[(0, completion(s.q))])
        s = Scenario(split=True)
        s.feed(s.rows, results=[(0, completion(s.q, integrity=0))])
        with self.assertRaisesRegex(Violation, "duplicate completion phase"):
            s.feed([row(s.q, physical=1)], results=[(0, completion(s.q, integrity=0))])
        s = Scenario()
        with self.assertRaisesRegex(Violation, "unsupported nonterminal"):
            s.feed(s.rows, results=[(0, completion(s.q, integrity=0))])

    def test_failed_final_without_physical_and_invalid_status(self):
        s = Scenario()
        s.feed(s.rows, results=[(0, completion(s.q, physical=0, failed=1, response=2))])
        s.feed()
        s.model.finish()
        for flags in (dict(physical=0), dict(failed=1), dict(response=2), dict(response=3, failed=1)):
            s = Scenario()
            with self.subTest(flags=flags), self.assertRaises(Violation):
                s.feed(s.rows, results=[(0, completion(s.q, **flags))])

    def test_merged_participants_retire_on_the_shared_phase(self):
        q = request()
        rows = [row(q, merged=1), row(q, slot=1, merged=1, follower=1, source_slot=1)]
        s = Scenario(q=q, participants=rows, failed=False)
        s.feed(rows, results=[(0, completion(q, match_slot=1))])
        s.feed()
        self.assertEqual(s.model.finish()["history"][0]["participants"], 2)
        for fields in (dict(merged=0), dict(follower=0)):
            invalid = copy.deepcopy(rows)
            invalid[1].update(fields)
            with self.assertRaises(Violation):
                Scenario(q=q, participants=invalid)

    def test_merged_participant_cannot_disappear_or_change(self):
        q = request()
        rows = [row(q, merged=1), row(q, slot=1, merged=1, follower=1, source_slot=1)]
        s = Scenario(q=q, participants=rows)
        with self.assertRaisesRegex(Violation, "participants changed"):
            s.feed(rows[:1], results=[(0, completion(q))])

    def test_reset_clears_numeric_counter_but_recover_does_not(self):
        s = Scenario()
        s.abort()
        s.drop()
        s.recovered()
        s.model.reset(dict(ps=s.model.previous["ps"] + 100, tick=s.tick - 1, reset_n=0, count=1))
        s.feed(count=0, reset_n=0)
        self.assertEqual(s.model.expected, 0)
        self.assertTrue(any(h["kind"] == "drop" for h in s.model.history))
        s = Scenario()
        s.abort()
        s.drop()
        s.recovered()
        with self.assertRaisesRegex(Violation, "counter update"):
            s.feed(count=0, recover=1)

    def test_reset_witness_cannot_hide_an_unreconciled_increment(self):
        s = Scenario()
        s.abort()
        s.drop()
        with self.assertRaisesRegex(Violation, "unreconciled counter"):
            s.model.reset(dict(ps=s.model.previous["ps"] + 100, tick=s.tick - 1, reset_n=0, count=0))
        s = Scenario()
        with self.assertRaisesRegex(Violation, "actual core reset edge"):
            s.feed(reset_n=0)

    def test_saturation_does_not_waive_events_and_wrap_fails(self):
        # Seed only the arithmetic state to exercise a 32-bit boundary without
        # claiming a hardware history containing billions of aborts.
        s = Scenario()
        s.abort()
        s.model.expected = MAX_COUNTER - 1
        s.feed(results=[(0, completion(s.q, match_slot=-1))], count=MAX_COUNTER - 1)
        self.assertEqual(s.model.expected, MAX_COUNTER)
        with self.assertRaisesRegex(Violation, "counter update"):
            s.feed(count=0)
        s = Scenario()
        s.abort()
        s.model.expected = MAX_COUNTER
        s.feed(results=[(0, completion(s.q, match_slot=-1))], count=MAX_COUNTER)
        s.feed(count=MAX_COUNTER, recover=1, enable=0, settled=1, config_valid=0)
        s.baseline(count=MAX_COUNTER)
        with self.assertRaisesRegex(Violation, "already completed"):
            s.feed(results=[(0, completion(s.q, match_slot=-1))], count=MAX_COUNTER)

    def test_baseline_requires_recover_both_channels_source_and_new_epoch(self):
        s = Scenario()
        s.abort()
        s.drop()
        with self.assertRaisesRegex(Violation, "before observed host recovery"):
            s.baseline()
        for change in (dict(channels={0}), dict(settled=False), dict(samples=[])):
            s = Scenario()
            s.abort()
            s.drop()
            s.recovered()
            with self.subTest(change=change), self.assertRaisesRegex(Violation, "destructive/source"):
                s.baseline(**change)
        s = Scenario()
        s.abort()
        s.drop()
        s.recovered()
        with self.assertRaisesRegex(Violation, "unreconciled counter"):
            s.baseline(count=0)

    def test_early_or_late_baseline_does_not_hide_reopened_service(self):
        for field in ("enabled", "config_valid", "trained", "fault", "destructive", "reset_request", "memory_reset"):
            s = Scenario()
            s.abort()
            s.drop()
            s.recovered()
            samples = [
                dict(
                    boot=1,
                    confirmed=1,
                    reset_request=0,
                    reset_n=1,
                    settled=1,
                    destructive=0,
                    memory_reset=0,
                    enabled=0,
                    host_fault=0,
                    fault=0,
                    config_valid=0,
                    trained=0,
                )
                for _ in range(2)
            ]
            samples[1][field] = 1
            with self.subTest(field=field), self.assertRaisesRegex(Violation, "complete new-epoch"):
                s.baseline(samples=samples)
        s = Scenario()
        s.abort()
        s.drop()
        s.recovered()
        q = request(boot=1, config=6)
        with self.assertRaisesRegex(Violation, "before recovery baseline"):
            s.feed([row(q)], requests=[(0, q)], count=1, config_boot=1, config_epoch=6)

    def test_late_old_epoch_after_baseline_is_rejected_even_without_counter_change(self):
        s = Scenario()
        s.abort()
        s.recovered(count=0)
        s.baseline(count=0)
        with self.assertRaisesRegex(Violation, "outside canceled recovery"):
            s.feed(results=[(0, completion(s.q, match_slot=-1))])

    def test_actual_request_epoch_and_same_edge_completion_are_required(self):
        for q in (request(boot=1), request(config=2), request(channel=1)):
            with self.subTest(q=q):
                if q["channel"] == 1:
                    m = HostCompletions()
                    m.edge(edge(0, reset_n=0))
                    with self.assertRaisesRegex(Violation, "channel wire"):
                        m.edge(edge(1, [row(q)], [(0, q)]))
                else:
                    with self.assertRaisesRegex(Violation, "actual core configuration"):
                        Scenario(q=q)
        m = HostCompletions()
        m.edge(edge(0, reset_n=0))
        q = request()
        with self.assertRaisesRegex(Violation, "previously accepted"):
            m.edge(edge(1, [row(q)], [(0, q)], [(0, completion(q))]))

    def test_inline_seam_binds_actual_wire_epochs_flags_and_payload(self):
        s = Scenario(failed=False)
        e = s.q | dict(kind="inline_admit", tick=1, ps=1875)
        s.model.seam(e)
        with self.assertRaisesRegex(Violation, "actual epoch"):
            s.model.seam(e | dict(boot=1))
        c = completion(s.q)
        s.feed(s.rows, results=[(0, c)])
        e = c | dict(kind="inline_complete", channel=0, tick=2, ps=3125)
        s.model.seam(e)
        for field in ("physical", "integrity", "failed", "response", "data"):
            with self.subTest(field=field), self.assertRaisesRegex(Violation, "payload"):
                s.model.seam(e | {field: 1 - e[field]})

    def test_final_counter_requires_a_later_observation(self):
        s = Scenario()
        s.abort()
        s.drop()
        with self.assertRaisesRegex(Violation, "next-edge counter tail"):
            s.model.finish()

    def test_request_leader_source_identity_is_bound(self):
        q = request()
        for fields in (dict(port=1), dict(axi_id=44), dict(source_generation=4)):
            with self.subTest(fields=fields), self.assertRaisesRegex(Violation, "source leader identity"):
                Scenario(q=q, participants=[row(q, **fields)])

    def test_duplicate_reset_and_recovery_before_reset_do_not_prove_baseline(self):
        s = Scenario()
        s.abort()
        s.recovered(count=0)
        e = dict(ps=s.model.previous["ps"] + 100, tick=s.tick - 1, reset_n=0, count=0)
        s.model.reset(e)
        with self.assertRaisesRegex(Violation, "duplicate host reset"):
            s.model.reset(e)
        s.feed(reset_n=0, enable=0, config_valid=0)
        with self.assertRaisesRegex(Violation, "before observed host recovery"):
            s.baseline(count=0)

    def test_baseline_requires_next_edge_numeric_reconciliation(self):
        s = Scenario()
        s.abort()
        s.recovered(count=0)
        s.drop()
        with self.assertRaisesRegex(Violation, "unreconciled counter"):
            s.baseline(count=1)

    def test_baseline_requires_confirmed_new_epoch_and_closed_core(self):
        for change in (dict(boot=0), dict(confirmed=0)):
            s = Scenario()
            s.abort()
            s.recovered(count=0)
            samples = [
                dict(
                    boot=1,
                    confirmed=1,
                    reset_request=0,
                    reset_n=1,
                    settled=1,
                    destructive=0,
                    memory_reset=0,
                    enabled=0,
                    host_fault=0,
                    fault=0,
                    config_valid=0,
                    trained=0,
                )
                for _ in range(2)
            ]
            samples[1].update(change)
            with self.subTest(change=change), self.assertRaisesRegex(Violation, "complete new-epoch"):
                s.baseline(count=0, samples=samples)
        for field in ("enable", "config_valid", "fatal", "fault"):
            s = Scenario()
            s.abort()
            s.recovered(count=0)
            s.model.previous[field] = 1
            with self.subTest(field=field), self.assertRaisesRegex(Violation, "completed core recovery"):
                s.baseline(count=0)


if __name__ == "__main__":
    unittest.main()
