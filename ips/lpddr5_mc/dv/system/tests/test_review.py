"""Counterexamples and protocol/time/ownership boundaries from source review."""

import copy
import unittest

from contracts.dvfs import Sample, check_dvfs
from contracts.lifecycle import check_low_power
from system.control import ManagementPort, ResponseScript
from system.host import Address, HostScoreboard, Violation
from system.merge import Context, MergeMonitor
from system.ownership import Identity, LifetimeMonitor


class ManagementReview(unittest.TestCase):
    def exercise(self, kind, ack, returned, release, *, mutate=None, bound_clock=True):
        script = ResponseScript(ack, returned, initial=kind in ("fsp", "training"))
        port = ManagementPort(kind, script, completion_limit=5)
        for ck in range(45):
            values = dict(request=ck < release, response=script.value(ck))
            if kind == "fsp" and bound_clock:
                values.update(clock_change_done=ack is not None and ck >= release - 1, target=(1, 2, 1))
            if mutate:
                mutate(ck, values)
            port.sample(ck, **values)
        port.finish()
        return port

    def test_fsp_matches_accepted_oracle_boundaries_and_decline(self):
        for ack in (None, 0, 1, 3, 4, 5):
            for release in (3, 4, 6, 7):
                for returned in (None, 1, 4, 6, 7, 11, 12):
                    with self.subTest(ack=ack, release=release, returned=returned):
                        script = ResponseScript(ack, returned, initial=True)
                        trace = [
                            Sample(ck < release, script.value(ck), 1, 2, 1, ack is not None and ck >= release - 1, True)
                            for ck in range(45)
                        ]
                        expected = check_dvfs(4, 5, trace, drained=True)
                        if expected.disposition in ("acknowledged", "declined"):
                            actual = self.exercise("fsp", ack, returned, release)
                            self.assertEqual(actual.disposition, expected.disposition)
                            self.assertEqual(
                                actual.returned if actual.accepted is not None else actual.release,
                                expected.returned_edge,
                            )
                        else:
                            with self.assertRaises(Violation):
                                self.exercise("fsp", ack, returned, release)
        # The unchanged review script uses the pin-only subset of the API.
        self.assertEqual(self.exercise("fsp", 1, 11, 6, bound_clock=False).disposition, "acknowledged")
        self.assertEqual(self.exercise("fsp", None, None, 4).disposition, "declined")

    def test_fsp_clock_target_stability_and_missing_witness(self):
        changes = [
            (0, {"clock_change_done": True}),
            (5, {"clock_change_done": False}),
            (3, {"target": (2, 4, 2)}),
            (2, {"clock_change_done": None}),
            (0, {"target": (1, 3, 1)}),
        ]
        for edge, delta in changes:
            with self.subTest(edge=edge, delta=delta), self.assertRaises(Violation):
                self.exercise(
                    "fsp",
                    1,
                    11,
                    6,
                    mutate=lambda ck, v, delta=delta, edge=edge: v.update(delta) if ck == edge else None,
                )
        with self.assertRaisesRegex(Violation, "registered clock"):
            self.exercise("fsp", 1, 11, 6, mutate=lambda ck, v: v.update(clock_change_done=ck >= 6))

    def test_pm_matches_low_power_release_wakeup_and_decline(self):
        for ack in (None, 0, 1, 4, 5):
            for release in (4, 6):
                for returned in (None, 3, 4, 6, 7, 11, 12):
                    with self.subTest(ack=ack, release=release, returned=returned):
                        script = ResponseScript(ack, returned)
                        try:
                            expected = check_low_power(4, 5, script.samples(44), release)
                        except ValueError:
                            with self.assertRaises(Violation):
                                self.exercise("pm", ack, returned, release)
                        else:
                            self.assertEqual(self.exercise("pm", ack, returned, release).disposition, expected)

    def test_training_master_positive_and_fault_boundaries(self):
        for kind in ("training", "master"):
            for ack, returned, release in ((1, 3, 5), (4, 8, 9)):
                self.assertEqual(self.exercise(kind, ack, returned, release).disposition, "acknowledged")
            for ack, returned, release in ((None, None, 5), (0, 3, 5), (1, None, 5), (1, 6, 6)):
                with self.subTest(kind=kind, ack=ack, returned=returned), self.assertRaises(Violation):
                    self.exercise(kind, ack, returned, release)
            with self.assertRaisesRegex(Violation, "returned state"):
                self.exercise(
                    kind, 1, 3, 5, mutate=lambda ck, v: v.update(returned_state_known=False) if ck == 5 else None
                )
        with self.assertRaises(Violation):
            self.exercise("master", 5, 8, 9)

    def test_every_kind_holds_ownership_through_return_and_rejects_reassertion(self):
        for kind, returned, release in (
            ("update", 3, 5),
            ("training", 3, 5),
            ("pm", 6, 4),
            ("fsp", 11, 6),
            ("master", 3, 5),
        ):
            last = max(returned, release)
            for edge in (0, 2, last):
                for delta in ({"wck_disabled": False}, {"drained": False}, {"traffic": True}):
                    with self.subTest(kind=kind, edge=edge, delta=delta), self.assertRaises(Violation):
                        self.exercise(
                            kind,
                            1,
                            returned,
                            release,
                            mutate=lambda ck, v, delta=delta, edge=edge: v.update(delta) if ck == edge else None,
                        )
            for delta in ({"request": True}, {"response": kind not in ("fsp", "training")}):
                with self.subTest(kind=kind, delta=delta), self.assertRaises(Violation):
                    self.exercise(
                        kind,
                        1,
                        returned,
                        release,
                        mutate=lambda ck, v, delta=delta, last=last: v.update(delta) if ck == last + 1 else None,
                    )
            # Once the return edge has passed, normal traffic/WCK may resume.
            self.exercise(
                kind,
                1,
                returned,
                release,
                mutate=lambda ck, v, last=last: (
                    v.update(traffic=True, drained=False, wck_disabled=False) if ck > last else None
                ),
            )
        with self.assertRaisesRegex(Violation, "reassertion"):
            self.exercise("fsp", 1, 11, 6, mutate=lambda ck, v: v.update(request=True) if ck == 8 else None)
        with self.assertRaisesRegex(Violation, "reassertion"):
            self.exercise("pm", 1, 6, 4, mutate=lambda ck, v: v.update(request=True) if ck == 5 else None)

    def test_management_stale_unsolicited_samples_and_incomplete_tail(self):
        for kind in ("update", "training", "pm", "fsp", "master"):
            p = ManagementPort(kind, ResponseScript(None, None, initial=kind in ("training", "fsp")))
            with self.assertRaisesRegex(Violation, "unsolicited"):
                p.sample(0, request=False, response=not p.script.initial)
            p = ManagementPort(kind, p.script)
            p.sample(0, request=True, response=p.script.initial)
            with self.assertRaisesRegex(Violation, "unfinished"):
                p.finish()
            for ck in (0, 2):
                with self.assertRaisesRegex(Violation, "sample"):
                    p.sample(ck, request=True, response=p.script.initial)


class MergeReview(unittest.TestCase):
    def group(self, h, m, address, count=2):
        tags = []
        for n in range(count):
            tag = h.admit(Address(0, h.next_tag, address), True)
            h.w(0, n + 1, 65535, True)
            m.context(tag, Context(0, 0, 0, 0, 1, h.epoch, 1))
            tags.append(tag)
        return tags

    def complete(self, h, m, physical, tags, address):
        m.pin(physical, address, len(tags).to_bytes(16, "little"), 65535, h.epoch)
        m.physical(physical)
        for t in tags:
            h.integrity_done(t)
            h.offer(t, 0, 0, True)
            h.handshake(t)

    def test_duplicate_overlapping_originals_and_physical_identity(self):
        h = HostScoreboard()
        m = MergeMonitor(h)
        tags = self.group(h, m, 0, 3)
        m.begin(100, tags)
        for physical, originals in ((101, tags), (102, tags[1:]), (100, tags)):
            with self.assertRaises(Violation):
                m.begin(physical, originals)
        self.complete(h, m, 100, tags, 0)
        for action in (lambda: m.physical(100), lambda: m.begin(101, tags), lambda: m.pin(100, 0, bytes(16), 65535, 0)):
            with self.assertRaises(Violation):
                action()
        h.finish()
        m.finish()

    def test_disjoint_pending_groups_and_retired_new_originals(self):
        h = HostScoreboard()
        m = MergeMonitor(h)
        a, b = self.group(h, m, 0), self.group(h, m, 64)
        m.begin(100, a)
        m.begin(101, b)
        self.complete(h, m, 101, b, 64)
        self.complete(h, m, 100, a, 0)
        c = self.group(h, m, 0)
        with self.assertRaisesRegex(Violation, "identity"):
            m.begin(100, c)
        m.begin(102, c)
        self.complete(h, m, 102, c, 0)
        h.finish()
        m.finish()

    def test_fault_reset_stale_completion_and_new_epoch_id_reuse(self):
        h = HostScoreboard()
        m = MergeMonitor(h)
        tags = self.group(h, m, 0)
        m.begin(100, tags)
        h.fail()
        for action in (
            lambda: m.physical(100),
            lambda: m.pin(100, 0, bytes(16), 65535, 0),
            lambda: m.begin(101, tags),
            m.reset,
        ):
            with self.assertRaises(Violation):
                action()
        for tag in tags:
            h.offer(tag, 0, 2, True)
            h.handshake(tag)
        h.reset(destructive=True)
        with self.assertRaisesRegex(Violation, "epoch"):
            m.physical(100)
        m.reset()
        with self.assertRaises(Violation):
            m.physical(100)
        tags = self.group(h, m, 0)
        m.begin(100, tags)
        with self.assertRaisesRegex(Violation, "epoch"):
            m.pin(100, 0, bytes(16), 65535, 0)
        self.complete(h, m, 100, tags, 0)
        h.finish()
        m.finish()

    def test_original_physical_effect_cannot_be_claimed_twice(self):
        h = HostScoreboard()
        m = MergeMonitor(h)
        tags = self.group(h, m, 0)
        m.begin(100, tags)
        h.pin_write(tags[0], 0, (1).to_bytes(16, "little"), 65535, 0)
        for action in (lambda: m.pin(100, 0, (2).to_bytes(16, "little"), 65535, 0), lambda: m.physical(100)):
            with self.assertRaisesRegex(Violation, "ownership"):
                action()


class LifetimeReview(unittest.TestCase):
    def epoch(self, ck=100):
        m = LifetimeMonitor()
        key = Identity(1, 0, 0, 1)
        m.allocate(key, 0, 0, posted=False, link=True, ck=ck)
        m.physical(key)
        return m, key

    def settle(self, m, key):
        m.transfer(key, reserved=True)
        m.expose(key, 0, handshaken=True)

    def test_all_timestamped_apis_reject_reversal_and_invalid_time(self):
        for ck in (-1, 0, 99, True, 100.5):
            for event in ("allocate", "poll", "result", "advance", "mr43"):
                with self.subTest(ck=ck, event=event):
                    m, _ = self.epoch()
                    m.poll(0, 100)
                    m.advance(110)
                    actions = {
                        "allocate": lambda m=m, ck=ck: m.allocate(
                            Identity(2, 0, 0, 1), 1, 1, posted=True, link=True, ck=ck
                        ),
                        "poll": lambda m=m, ck=ck: m.poll(0, ck),
                        "result": lambda m=m, ck=ck: m.result(0, ck, False, read_clear_ck=130),
                        "advance": lambda m=m, ck=ck: m.advance(ck),
                        "mr43": lambda m=m, ck=ck: m.mr43(0, ck, False),
                    }
                    with self.assertRaisesRegex(Violation, "time reversal"):
                        actions[event]()

    def test_epoch_poll_deadline_and_result_clear_boundaries(self):
        m, key = self.epoch()
        with self.assertRaisesRegex(Violation, "time reversal"):
            m.poll(0, 0)
        m.advance(356)
        m.poll(0, 356)
        for ck, clear in ((363, 376), (364, 375), (376, 376)):
            with self.assertRaisesRegex(Violation, "result/read-clear"):
                m.result(0, ck, False, read_clear_ck=clear)
        m.result(0, 364, False, read_clear_ck=376)
        self.settle(m, key)
        m.finish()
        m, _ = self.epoch()
        for event in (
            lambda: m.poll(0, 357),
            lambda: m.advance(357),
            lambda: m.allocate(Identity(2, 0, 0, 1), 1, 1, posted=True, link=False, ck=357),
        ):
            with self.assertRaisesRegex(Violation, "missing MR43"):
                event()

    def test_same_edge_independent_ranks_and_read_clear_future_guard(self):
        m, a = self.epoch()
        b = Identity(2, 0, 0, 1)
        m.allocate(b, 1, 1, posted=False, link=True, ck=100)
        m.physical(b)
        m.advance(100)
        m.poll(0, 100)
        m.poll(1, 100)
        m.result(0, 108, False, read_clear_ck=120)
        m.result(1, 108, False, read_clear_ck=120)
        m.advance(108)  # Future guard does not jump observed time to 120.
        self.settle(m, a)
        self.settle(m, b)
        c = Identity(3, 1, 0, 1)
        with self.assertRaisesRegex(Violation, "read-clear"):
            m.allocate(c, 0, 0, posted=False, link=True, ck=119)
        m.allocate(c, 0, 0, posted=False, link=True, ck=120)
        m.physical(c)
        m.mr43(0, 120, False)
        self.settle(m, c)
        m.finish()

    def test_reset_preserves_simulation_time_and_rejects_stale_epoch(self):
        m, key = self.epoch()
        m.ownership_loss()
        m.transfer(key, reserved=True)
        m.expose(key, 2, handshaken=True)
        m.reset(destructive_done=True)
        with self.assertRaisesRegex(Violation, "time reversal"):
            m.advance(0)
        with self.assertRaisesRegex(Violation, "stale allocation"):
            m.allocate(key, 0, 0, posted=True, link=False, ck=100)
        new = Identity(1, 0, 1, 0)
        m.allocate(new, 0, 0, posted=False, link=False, ck=100)
        m.physical(new)
        self.settle(m, new)
        m.finish()

    def test_result_cannot_retroactively_settle_after_observed_time(self):
        m, key = self.epoch()
        m.poll(0, 100)
        m.advance(120)
        before = copy.deepcopy(m.live)
        with self.assertRaisesRegex(Violation, "time reversal"):
            m.result(0, 108, False, read_clear_ck=128)
        self.assertEqual(m.live, before)
        m.result(0, 120, False, read_clear_ck=132)
        self.settle(m, key)
        m.finish()
