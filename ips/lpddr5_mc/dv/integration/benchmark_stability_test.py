"""Changed-owner adverse checks for the production write stability method."""

import copy
import json
import unittest
from pathlib import Path

from benchmark import require_full_performance
from benchmark_stability import WriteStability, _channel_stability
from system.host import Violation

START, END, LIMIT = 30000, 300000, 3124
REFS = list(range(1600, END + 10000, 2950))


def periodic():
    tx = [{"port": 0, "id": i, "aw": at, "wlast": at + 200} for i, at in enumerate(range(0, END + 2000, 300))]
    next_b = 0
    for t in tx:
        at = max(t["wlast"] + 40, next_b)
        phase = (at - REFS[0]) % 2950
        if phase < 700:
            at += 700 - phase
        t["b"], next_b = at, at + 2
    return tx


def assess(tx, refs=REFS):
    return _channel_stability(tx, refs, start=0, measure_start=START, measure_end=END, ref_limit=LIMIT)


def profile():
    path = Path(__file__).resolve().parents[2] / "config/contracts/profiles.json"
    return json.loads(path.read_text())["profiles"][4] | {"ranks": 1}


HEADER = {"kind": "header", "version": 1, "ports": 2, "channels": 2, "bus_bytes": 16}


def aw(at, port=0):
    return {
        "kind": "aw",
        "ps": at,
        "port": port,
        "id": 0,
        "address": port * 64,
        "length": 3,
        "size": 4,
        "burst": 1,
        "lock": 0,
    }


def axi(at, flags=517, port=0):
    return {"kind": "axi", "ps": at, "port": port, "flags": flags}


def phy(ck, channel=0, *, ref=False):
    return {
        "kind": "phy",
        "ps": ck * 1250,
        "ck": ck,
        "channel": channel,
        "cs": int(ref),
        "ca": 56 | (64 << 7) if ref else 0,
    }


class RecurrenceTests(unittest.TestCase):
    def reject(self, tx, reason):
        result = assess(tx)
        self.assertFalse(result["passed"])
        self.assertIn(reason, result["failures"])
        with self.assertRaisesRegex(Violation, "write stability"):
            require_full_performance(
                {
                    "direction": "write",
                    "smoke_only": False,
                    "threshold_passed": True,
                    "steady_backlog_endpoint_passed": True,
                    "write_stability": result,
                }
            )
        return result

    def test_bounded_periodic_unequal_endpoint_phases(self):
        result = assess(periodic())
        self.assertTrue(result["passed"])
        window = result["window_conservation"]
        self.assertEqual([window["before"]["aw_no_b"], window["after"]["aw_no_b"]], [0, 2])
        self.assertTrue(all(not row["unretired_previous_aw"] for row in result["intervals"]))

    def test_sustained_service_deficit(self):
        tx = periodic()
        for t in tx:
            if t["wlast"] >= START:
                t["b"] = max(t["b"], t["wlast"] + 40 + (1 + (t["wlast"] - START) // 3000) * 100)
        self.reject(tx, "service_ready_floor_did_not_recur")

    def test_finite_credits_with_increasing_delay(self):
        tx, next_service = [], 0
        for i in range(1400):
            at = max(i * 220, tx[i - 64]["b"] if i >= 64 else 0)
            b = max(next_service, at + 32)
            next_service = b + 300
            tx.append({"port": 0, "id": i, "aw": at, "wlast": at + 2, "b": b})
        self.reject(tx, "aw_to_b_age_limit")

    def test_w_throttling_cannot_hide_accepted_aw(self):
        tx = periodic()
        next_w, release = 0, START + 24000
        for t in tx:
            if START <= t["aw"] < release:
                t["wlast"] = max(release, next_w)
                next_w = t["wlast"] + 2
                t["b"] = t["wlast"] + 40
        result = self.reject(tx, "old_aw_survived_recurrence")
        self.assertIn("aw_to_b_age_limit", result["failures"])

    def test_stranded_warmup_write_despite_recurrent_low_water(self):
        tx = [
            {"port": 0, "id": i, "aw": at, "wlast": at + 1, "b": at + 101}
            for i, at in enumerate(range(0, END + 2000, 100))
        ]
        for t in tx:
            if t["aw"] == START - 100:
                t["b"] = END + 100000
            elif t["aw"] >= START:
                t["b"] = t["wlast"] + 1
        result = self.reject(tx, "old_aw_survived_recurrence")
        self.assertEqual(result["warmup_calibration"]["floor"], 1)
        self.assertTrue(all(row["cut_ps"] is not None for row in result["intervals"]))

    def test_tail_only_catchup_cannot_repair_recurrence(self):
        tx = periodic()
        for t in tx:
            if START <= t["aw"] < END:
                t["b"] = max(END + 1000 + t["id"] * 2, t["wlast"] + 1)
        self.reject(tx, "service_ready_floor_did_not_recur")

    def test_missing_b_duplicate_and_incomplete_w_fail_admission(self):
        for key in ("b", "wlast"):
            tx = periodic()
            tx[150][key] = None
            with self.assertRaisesRegex(Violation, "unsettled"):
                assess(tx)
        tx = periodic()
        tx.append(copy.deepcopy(tx[150]))
        with self.assertRaisesRegex(Violation, "duplicate identity"):
            assess(tx)

    def test_bounded_w_delay(self):
        tx = periodic()
        for t in tx:
            t["wlast"] += 10
            t["b"] = max(t["b"], t["wlast"] + 40)
        self.assertTrue(assess(tx)["passed"])

    def test_6246_ck_bounded_delay(self):
        refs = list(range(0, END + 4 * LIMIT, LIMIT))
        tx = [
            {"port": 0, "id": i, "aw": at + 1, "wlast": at + 2 * LIMIT - 2, "b": at + 2 * LIMIT - 1}
            for i, at in enumerate(refs)
        ]
        result = assess(tx, refs)
        self.assertTrue(result["passed"])
        self.assertEqual(result["maximum_live_aw_to_b_ps"], 6246)

    def test_carry_in_at_and_over_age_boundary_before_first_recurrence(self):
        for extra in (0, 1):
            tx = periodic()
            tx.append(
                {
                    "port": 1,
                    "id": 60000,
                    "aw": START + 2 - 3 * LIMIT - extra,
                    "wlast": START + 3 - 3 * LIMIT - extra,
                    "b": START + 2,
                }
            )
            result = assess(tx)
            self.assertEqual(result["failures"], [] if extra == 0 else ["aw_to_b_age_limit"])

    def test_young_tail_does_not_earn_in_window_completion(self):
        tx = periodic()
        original = assess(tx)["window_conservation"]["events"]["b"]
        tx.append({"port": 1, "id": 60000, "aw": END - 10, "wlast": END - 8, "b": END + 50})
        result = assess(tx)
        self.assertTrue(result["passed"])
        self.assertEqual(result["window_conservation"]["events"]["b"], original)

    def test_ref_coverage_truncation_gaps_and_calibration_adjacency(self):
        for refs in (
            [],
            [r for r in REFS if r < 40000],
            REFS[12:],
            [r for r in REFS if r != REFS[40]],
            REFS[:-1] + [REFS[-2]],
        ):
            with self.subTest(refs=refs[:3]):
                with self.assertRaises(Violation):
                    assess(periodic(), refs)

    def test_partial_edges_need_no_future_ref_until_due(self):
        refs = [r for r in REFS if r <= END]
        result = assess(periodic(), refs)
        self.assertTrue(result["passed"])
        self.assertEqual(result["warmup_calibration"]["end_ps"], max(r for r in refs if r <= START))
        self.assertLessEqual(result["coverage"]["trailing_age_ps"], LIMIT)
        refs.pop()
        with self.assertRaisesRegex(Violation, "trailing REF"):
            assess(periodic(), refs)


class SourceAdmissionTests(unittest.TestCase):
    def collector(self, **kwargs):
        result = WriteStability(profile(), 0, **kwargs)
        result.sample(HEADER)
        return result

    def test_absent_evidence_is_not_assumed_true(self):
        with self.assertRaisesRegex(Violation, "truncated"):
            self.collector().finish()
        with self.assertRaises(TypeError):
            WriteStability(profile(), 0, source_offer_valid=True)

    def test_handshake_requires_actual_sampled_flags(self):
        for events in ([aw(0)], [axi(0), aw(0)], [axi(0, flags=773)], [axi(0), axi(0)]):
            c = self.collector()
            for e in events:
                c.sample(e)
            with self.assertRaises(Violation):
                c.sample({"kind": "end", "ps": 1250})

    def test_under_offer_and_legitimate_wready_backpressure(self):
        at = START * 1250
        for flags, rejected in ((5, True), (517, True), (549, False)):
            c = self.collector()
            c.sample(aw(at - 2500))
            c.sample(axi(at - 2500, 773))
            c.sample(axi(at, flags))
            if rejected:
                with self.assertRaisesRegex(Violation, "under-offer"):
                    c.sample({"kind": "end", "ps": at + 1250})
            else:
                c.sample({"kind": "end", "ps": at + 1250})
                self.assertEqual(c.offers[0]["w_stalls"], 1)

    def test_missing_axi_and_phy_samples_and_unsupported_scope(self):
        c = self.collector()
        c.sample(axi(0))
        c.sample(axi(5000))
        with self.assertRaisesRegex(Violation, "AXI clock"):
            c.sample({"kind": "end", "ps": 6250})
        c = self.collector()
        c.sample(phy(0))
        with self.assertRaisesRegex(Violation, "PHY clock"):
            c.sample(phy(2))
        for changed in ({"ranks": 2}, {"ck_ps": 2500}, {"read_link": True}):
            with self.assertRaisesRegex(Violation, "unsupported"):
                WriteStability(profile() | changed, 0)

    def test_raw_missing_b_bad_wlast_and_duplicate_b(self):
        for bad in ("missing_b", "wlast", "duplicate_b"):
            c = self.collector()
            c.sample(aw(0))
            c.sample(axi(0, 773))
            try:
                for beat in range(4):
                    at = (beat + 1) * 2500
                    c.sample(axi(at, 565))
                    c.sample({"kind": "w", "ps": at, "port": 0, "last": int(beat == (2 if bad == "wlast" else 3))})
                if bad != "missing_b":
                    c.sample(axi(12500, 525))
                    c.sample({"kind": "b", "ps": 12500, "port": 0, "id": 0, "ready": 1, "response": 0})
                    c.sample(axi(15000, 525))
                    c.sample({"kind": "b", "ps": 15000, "port": 0, "id": 0, "ready": 1, "response": 0})
                c.sample({"kind": "end", "ps": 16000})
                c.finish()
            except Violation as error:
                expected = {"missing_b": "unsettled W/B", "wlast": "WLAST", "duplicate_b": "B identity"}
                self.assertIn(expected[bad], str(error))
                continue
            self.fail(f"Accepted {bad}")

    def test_invalid_rank_refpb_and_truncated_clock_suffix(self):
        for event, reason in ((phy(0) | {"cs": 2}, "rank scope"), (phy(0) | {"cs": 1, "ca": 56}, "REFpb")):
            with self.assertRaisesRegex(Violation, reason):
                self.collector().sample(event)
        c = self.collector()
        c.sample(axi(0))
        c.sample(phy(0))
        c.sample(phy(0, 1))
        c.sample({"kind": "end", "ps": 300000 * 1250})
        with self.assertRaisesRegex(Violation, "clock/window coverage"):
            c.finish()

    def test_complete_sampled_window_and_smoke_none(self):
        # This synthetic source has no measured admissions. Stability alone
        # can pass, but the independent 99% payload gate must still reject it.
        for smoke in (True, False):
            c = self.collector(smoke=smoke)
            count = 6000 if smoke else 300000
            for ck in range(count + 1):
                at = ck * 1250
                for p in (0, 1):
                    if ck % 2 == 0:
                        flags = (
                            517 | (256 if ck == 0 else 0) | (48 if ck in (2, 4, 6, 8) else 0) | (8 if ck == 10 else 0)
                        )
                        c.sample(axi(at, flags, p))
                        if ck == 0:
                            c.sample(aw(at, p))
                        if ck in (2, 4, 6, 8):
                            c.sample({"kind": "w", "ps": at, "port": p, "last": int(ck == 8)})
                        if ck == 10:
                            c.sample({"kind": "b", "ps": at, "port": p, "id": 0, "ready": 1, "response": 0})
                    c.sample(phy(ck, p, ref=ck >= 1600 and (ck - 1600) % 2950 == 0))
            c.sample({"kind": "end", "ps": count * 1250})
            result = c.finish()
            if smoke:
                self.assertIsNone(result)
            else:
                self.assertTrue(result["passed"])
                self.assertEqual(result["clock_samples"]["axi"], [150000, 150000])
                with self.assertRaisesRegex(Violation, "99%"):
                    require_full_performance(
                        {
                            "smoke_only": False,
                            "direction": "write",
                            "threshold_passed": False,
                            "write_stability": result,
                        }
                    )


class VerdictTests(unittest.TestCase):
    write = {
        "direction": "write",
        "smoke_only": False,
        "threshold_passed": True,
        "steady_backlog_endpoint_passed": False,
        "write_stability": {"passed": True},
    }

    def test_write_method_supersedes_only_endpoint_proxy(self):
        require_full_performance(self.write)
        for value in (None, {}, {"passed": False}):
            with self.assertRaisesRegex(Violation, "write stability"):
                require_full_performance(self.write | {"write_stability": value})

    def test_read_endpoint_remains_required_and_smoke_never_promotes(self):
        for observed in (
            self.write | {"direction": "read"},
            self.write | {"smoke_only": True},
            self.write | {"threshold_passed": False},
            self.write | {"direction": None},
        ):
            with self.assertRaises(Violation):
                require_full_performance(observed)
        require_full_performance(self.write | {"direction": "read", "steady_backlog_endpoint_passed": True})


if __name__ == "__main__":
    unittest.main()
