"""Focused behavioral tests of reference contracts, including sensitivity cases."""

import copy
import itertools
import json
import random
import unittest
from dataclasses import replace
from pathlib import Path

from contracts.link_ecc import decode, encode, read_parity, syndrome, write_parity
from contracts.pin_oracle import check_edges, check_paired
from contracts.read_completion import CompletionOracle, ExpectedWord, ReadBusOracle
from contracts.schedule import Access, check_bg32, interleaved_stream, reference_stream
from contracts.update import check_update
from contracts.wck import derive_events, edge_train, require_integration_qualification, validate_profile

IP = Path(__file__).resolve().parents[2]
PROFILES = json.loads((IP / "config/reference/wck_profiles.json").read_text())["profiles"]
MATRIX = json.loads((IP / "config/reference/link_ecc_matrix.json").read_text())


class WckTests(unittest.TestCase):
    def test_numeric_rows_and_split_completion(self) -> None:
        for profile, direction, beats in itertools.product(PROFILES, ("read", "write"), (16, 32)):
            with self.subTest(profile=profile["name"], direction=direction, beats=beats):
                validate_profile(profile)
                check_paired(profile["name"], direction, beats, derive_events(profile, direction, beats))

    def test_pin_checker_detects_early_and_late_events(self) -> None:
        for profile, direction in itertools.product(PROFILES, ("read", "write")):
            event = derive_events(profile, direction, 32)
            for field in ("command", "enable", "slow_toggle", "fast_toggle", "first_data", "data_end"):
                for offset in (-1, 1):
                    with self.subTest(profile=profile["name"], field=field, offset=offset):
                        with self.assertRaises(ValueError):
                            check_paired(
                                profile["name"],
                                direction,
                                32,
                                replace(event, **{field: getattr(event, field) + offset}),
                            )
            with self.assertRaises(ValueError):
                check_paired(profile["name"], direction, 32, replace(event, stop=event.data_end + 4))
            with self.assertRaises(ValueError):
                check_paired(profile["name"], direction, 32, replace(event, disable=event.stop - 1))

    def test_model_path_delay_is_not_silently_ignored(self) -> None:
        profile = copy.deepcopy(PROFILES[0])
        profile["phy_wck_delay_half_wck"] += 1
        with self.assertRaises(ValueError):
            check_paired(profile["name"], "read", 32, derive_events(profile, "read", 32))

    def test_unequal_whole_wck_path_compensation(self) -> None:
        for original in PROFILES:
            profile = copy.deepcopy(original)
            # The WCK path is one WCK shorter; a one-WCK larger enable
            # offset exactly compensates at the DRAM boundary.
            profile["phy_wck_delay_half_wck"] -= 2
            for key in ("twck_en_rd", "twck_en_wr", "twck_en_fs", "twck_dis"):
                profile["offsets_wck"][key] += 1
            validate_profile(profile)
            for direction in ("read", "write"):
                event = derive_events(profile, direction, 32)
                check_paired(profile["name"], direction, 32, event)
                self.assertEqual(event, derive_events(original, direction, 32))

    def test_ideal_phase_edge_pattern_and_sensitivity(self) -> None:
        for profile in PROFILES:
            for direction in ("read", "write"):
                event = derive_events(profile, direction, 32)
                edges = edge_train(event)
                check_edges(profile["name"], event, edges)
                for mutation in (edges[1:], edges[:-1], (edges[0] - 1,) + edges):
                    with self.assertRaises(ValueError):
                        check_edges(profile["name"], event, mutation)

    def test_invalid_profiles_and_admission(self) -> None:
        for field, value in (("wck_to_ck", 3), ("bank_mode", "8B"), ("ranks", 2), ("ck_mhz", 400)):
            profile = copy.deepcopy(PROFILES[0])
            profile[field] = value
            with self.assertRaises(ValueError):
                validate_profile(profile)
        for key in PROFILES[0]["offsets_wck"]:
            profile = copy.deepcopy(PROFILES[0])
            del profile["offsets_wck"][key]
            with self.assertRaises(ValueError):
                validate_profile(profile)
        for key in PROFILES[0]:
            profile = copy.deepcopy(PROFILES[0])
            del profile[key]
            with self.assertRaises(ValueError):
                validate_profile(profile)
        for key, value in (
            ("read_latency_ck", -1),
            ("mr2_rl_code", 2),
            ("mr3_bank_org", 2),
            ("mr18_ckr", 1),
            ("mr22_recc", 1),
            ("static_preamble_ck", 3),
            ("read_enable_ck", 8),
            ("read_toggle_ck", 8),
            ("postamble_half_wck", 7),
            ("ck_mhz", True),
            ("integration_qualified", True),
        ):
            profile = copy.deepcopy(PROFILES[0])
            profile[key] = value
            with self.assertRaises(ValueError):
                validate_profile(profile)
        for value in (-1, 65536, True, 1.5):
            profile = copy.deepcopy(PROFILES[0])
            profile["offsets_wck"]["twck_en_rd"] = value
            with self.assertRaises(ValueError):
                validate_profile(profile)
        for profile in PROFILES:
            with self.assertRaisesRegex(ValueError, "qualification remain pending"):
                require_integration_qualification(profile)


class UpdateTests(unittest.TestCase):
    @staticmethod
    def trace(start: int | None, end: int | None, count: int = 18) -> list[bool]:
        return [start is not None and a >= start and (end is None or a < end) for a in range(count)]

    def test_ignored_and_registered_release(self) -> None:
        result = check_update(4, 12, self.trace(None, None))
        self.assertEqual((result.release, result.fault), (4, None))
        for start, end in ((1, 2), (3, 4), (3, 10)):
            result = check_update(4, 12, self.trace(start, end))
            self.assertEqual(result.release, max(4, end + 1))
            self.assertTrue(result.traffic_allowed)

    def test_boundaries_and_stuck_ack_hold_traffic(self) -> None:
        for start, end in ((0, 1), (4, 5), (5, 6), (3, 11), (3, None)):
            result = check_update(4, 12, self.trace(start, end))
            self.assertIsNone(result.release)
            self.assertIsNotNone(result.fault)
            self.assertFalse(result.traffic_allowed)
        ack = self.trace(1, 2)
        ack[8] = True
        self.assertFalse(check_update(4, 12, ack).traffic_allowed)

    def test_drain_and_refresh_admission(self) -> None:
        for kwargs in ({"drained": False}, {"wck_disabled": False}, {"refresh_budget": 13}):
            self.assertEqual(check_update(4, 12, self.trace(None, None), **kwargs).fault, "admission_denied")
        self.assertTrue(check_update(4, 12, self.trace(None, None), refresh_budget=14).traffic_allowed)
        for minimum, maximum in ((1, 12), (4, 5)):
            with self.assertRaises(ValueError):
                check_update(minimum, maximum, self.trace(None, None))


class LinkEccTests(unittest.TestCase):
    def test_normative_column_anchors(self) -> None:
        # Independently read from Table 331: beat 0/DQ0, beat 1/DQ0,
        # beat 15/DQ7 and C8. DMI anchors are M0, M15 and C5.
        data = MATRIX["data_rows"]
        self.assertEqual([syndrome(data, 1 << i) for i in (0, 8, 127, 136)], [0x118, 0x1E0, 0x19F, 0x100])
        dmi = MATRIX["dmi_rows"]
        self.assertEqual([syndrome(dmi, 1 << i) for i in (0, 15, 21)], [0x2C, 0x3F, 0x20])

    def test_all_single_and_double_error_locations(self) -> None:
        for name in ("data_rows", "dmi_rows"):
            rows = MATRIX[name]
            width = len(rows[0]) - len(rows)
            payload = int.from_bytes(bytes(range(width // 8)), "little")
            word = payload | (encode(rows, payload) << width)
            self.assertEqual(syndrome(rows, word), 0)
            columns = [syndrome(rows, 1 << bit) for bit in range(len(rows[0]))]
            self.assertEqual(len(set(columns)), len(columns))
            for bit in range(len(rows[0])):
                result = decode(rows, word ^ (1 << bit))
                self.assertEqual((result.payload, result.status, result.corrected_column), (payload, "corrected", bit))
            for a, b in itertools.combinations(range(len(rows[0])), 2):
                result = decode(rows, word ^ (1 << a) ^ (1 << b))
                self.assertEqual(result.status, "uncorrectable")

    def test_seeded_payloads_and_pin_positions(self) -> None:
        rng = random.Random(2095)
        for name in ("data_rows", "dmi_rows"):
            rows = MATRIX[name]
            width = len(rows[0]) - len(rows)
            for _ in range(128):
                payload = rng.getrandbits(width)
                checks = encode(rows, payload)
                self.assertEqual(decode(rows, payload | (checks << width)).payload, payload)
                self.assertEqual(syndrome(rows, payload | (checks << width)), 0)
        for data_check, dmi_check in ((0x155, 0x2A), (0x0AA, 0x15)):
            write = write_parity(data_check, dmi_check)
            read = read_parity(data_check)
            self.assertEqual(write[0], 0)
            self.assertEqual(sum(write[b] << (b - 1) for b in range(1, 7)), dmi_check)
            self.assertEqual(sum(write[b] << (b - 7) for b in range(7, 16)), data_check)
            self.assertEqual(read[:7], (0,) * 7)
            self.assertEqual(read[7:], write[7:])


class ScheduleTests(unittest.TestCase):
    def test_two_port_capacity_in_a_finite_legal_row_hit_window(self) -> None:
        for latency in (17, 19):
            accesses = reference_stream(64)
            payload = check_bg32(accesses, latency)
            self.assertEqual(payload, 2 * 64 * 64)
            # CK=800MHz; each channel issues one 64B command every 8 CK.
            measured_bytes_per_ck = payload / (64 * 8)
            self.assertEqual(measured_bytes_per_ck, 16)
            self.assertEqual(measured_bytes_per_ck * 800_000_000, 2 * 16 * 400_000_000)

    def test_interleaved_reference_has_capacity_for_later_overhead_analysis(self) -> None:
        accesses = interleaved_stream(64)
        payload = check_bg32(accesses)
        self.assertEqual(payload / (64 * 8), 32)
        # The integration still needs actual refresh, row and CDC schedules.

    def test_checker_detects_illegal_spacing_duplicate_and_collision(self) -> None:
        for accesses in (
            [Access(0, 1, 0, 0), Access(3, 2, 0, 0)],
            [Access(0, 1, 0, 0), Access(8, 1, 0, 0)],
            [Access(0, 1, 0, 0), Access(3, 2, 0, 1)],
        ):
            with self.assertRaises(ValueError):
                check_bg32(accesses)


class ReadCompletionTests(unittest.TestCase):
    @staticmethod
    def expected() -> list[ExpectedWord]:
        return [
            ExpectedWord(half * 16 + tag * 8 + word, tag, half * 8 + word, slice_index)
            for half in range(2)
            for tag in range(2)
            for word in range(8)
            for slice_index in range(2)
        ]

    def test_split_interleave_skew_and_gaps(self) -> None:
        expected = self.expected()
        oracle = CompletionOracle(expected, 16, 64)
        completed = []
        # Different per-slice skew, plus a hole between each data cycle.
        for item in sorted(expected, key=lambda e: (2 * e.enable + e.slice_index, e.slice_index)):
            at = 2 * item.enable + item.slice_index
            tag, done = oracle.valid(at, item.slice_index, (item.tag << 12) | item.word)
            if done:
                completed.append(tag)
                self.assertEqual(item.word, 15)
        self.assertEqual(completed, [0, 1])
        self.assertEqual(oracle.received[1][(15, 1)], 0x100F)
        self.assertFalse(any(oracle.pending.values()))

    def test_missing_extra_early_late_and_duplicate_enable(self) -> None:
        expected = self.expected()
        oracle = CompletionOracle(expected, 16, 64)
        with self.assertRaises(ValueError):
            oracle.valid(-1, 0, 0)
        with self.assertRaises(ValueError):
            oracle.valid(65, 0, 0)
        with self.assertRaises(ValueError):
            oracle.check_deadlines(65)
        with self.assertRaises(ValueError):
            CompletionOracle(expected + [expected[0]], 16, 64)
        for item in expected:
            oracle.valid(item.enable, item.slice_index, 0)
        with self.assertRaises(ValueError):
            oracle.valid(64, 1, 0)

    def test_actual_word_lanes_wrap_gaps_skew_and_dnv(self) -> None:
        for ratio in (2, 4):
            expected = [ExpectedWord(w, 1, w, s) for w in range(8) for s in range(2)]
            oracle = CompletionOracle(expected, 8, 100)
            bus = ReadBusOracle(ratio, oracle)
            next_word = [0, 0]
            completions = []
            # Return different contiguous prefixes per slice, including gaps
            # and wrapping across lane 0 within a snapshot.
            for ck, sizes in enumerate(((1, 0), (0, 1), (ratio, 1), (1, ratio), (ratio, ratio), (ratio, ratio))):
                lanes = [[None, None] for _ in range(ratio)]
                dnv = [[0, 0] for _ in range(ratio)]
                for s, wanted in enumerate(sizes):
                    count = min(wanted, 8 - next_word[s])
                    for i in range(count):
                        lane = (bus.pointer[s] + i) % ratio
                        word = next_word[s] + i
                        lanes[lane][s] = 0x1000 + 2 * word + s
                        if word == 3 and s == 1:
                            dnv[lane][s] = 1
                    next_word[s] += count
                completions.extend(bus.snapshot(ck + 10, lanes, dnv))
            self.assertEqual(next_word, [8, 8])
            self.assertEqual(completions, [1])
            self.assertEqual(bus.unavailable, {(1, 3, 1)})
            self.assertEqual(oracle.received[1][(7, 1)], 0x100F)

    def test_rotation_fault_deadline_and_resynchronization(self) -> None:
        for ratio in (2, 4):
            expected = [ExpectedWord(tag, tag, 0, s) for tag in range(2) for s in range(2)]
            bus = ReadBusOracle(ratio, CompletionOracle(expected, 1, 20))
            empty_dnv = [[0, 0] for _ in range(ratio)]
            bad = [[None, None] for _ in range(ratio)]
            bad[1] = [0, 0]
            with self.assertRaisesRegex(ValueError, "rotation"):
                bus.snapshot(1, bad, empty_dnv)
            self.assertEqual(sum(map(len, bus.completion.pending.values())), 4)
            first = [[None, None] for _ in range(ratio)]
            first[0] = [0xA, 0xB]
            self.assertEqual(bus.snapshot(1, first, empty_dnv), [0])
            with self.assertRaisesRegex(ValueError, "drain"):
                bus.resynchronize("dfi_ctrlupd_req")
            second = [[None, None] for _ in range(ratio)]
            second[1] = [0xC, 0xD]
            self.assertEqual(bus.snapshot(2, second, empty_dnv), [1])
            for event in ("dfi_init_start", "dfi_ctrlupd_req", "dfi_phyupd_ack", "initialization"):
                bus.resynchronize(event)
                self.assertEqual(bus.pointer, [0, 0])
            with self.assertRaises(ValueError):
                bus.resynchronize("timer_expired")
            with self.assertRaises(ValueError):
                bus.snapshot(2, first, empty_dnv)
            early = ReadBusOracle(ratio, CompletionOracle([ExpectedWord(20, 0, 0, s) for s in range(2)], 1, 4))
            with self.assertRaisesRegex(ValueError, "deadline"):
                early.snapshot(1, first, empty_dnv)
            with self.assertRaisesRegex(ValueError, "deadline"):
                early.snapshot(30, first, empty_dnv)
            with self.assertRaisesRegex(ValueError, "missing read valid"):
                early.snapshot(30, [[None, None] for _ in range(ratio)], empty_dnv)


if __name__ == "__main__":
    unittest.main()
