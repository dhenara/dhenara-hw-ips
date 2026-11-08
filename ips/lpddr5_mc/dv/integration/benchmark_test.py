"""Bind benchmark warm-up claims to independent host handshakes."""

import unittest

from benchmark import first_write_observation, write_prefill
from system.host import Violation


class WritePrefillTests(unittest.TestCase):
    markers = "BENCHMARK_WRITE_PREFILL port=0 admitted=64 ps=2000\nBENCHMARK_WRITE_PREFILL port=1 admitted=64 ps=2100\n"

    def test_observed_addresses_support_claim(self):
        claim = write_prefill(self.markers, "write", 1000, False)
        observed = first_write_observation(claim, 0, 2500, 64, 64)
        self.assertEqual(observed["outstanding_addresses"], 64)

    def test_marker_cannot_substitute_for_handshakes(self):
        claim = write_prefill(self.markers, "write", 1000, False)
        for accepted, outstanding in ((63, 63), (64, 63), (65, 64)):
            with self.subTest(accepted=accepted, outstanding=outstanding):
                with self.assertRaisesRegex(Violation, "before observed address prefill"):
                    first_write_observation(claim, 0, 2500, accepted, outstanding)
        with self.assertRaises(Violation):
            first_write_observation(claim, 0, 2000, 64, 64)

    def test_incomplete_duplicate_or_wrong_direction_claim_fails(self):
        for text, direction in (
            (self.markers.splitlines()[0], "write"),
            (self.markers.replace("port=1", "port=0"), "write"),
            (self.markers.replace("admitted=64", "admitted=63"), "write"),
            (self.markers, "read"),
        ):
            with self.subTest(text=text, direction=direction):
                with self.assertRaises(Violation):
                    write_prefill(text, direction, 1000, False)

    def test_prefill_must_complete_during_original_warmup(self):
        for ps in (999, 37_501_000):
            with self.subTest(ps=ps):
                with self.assertRaisesRegex(Violation, "outside warm-up"):
                    write_prefill(self.markers.replace("ps=2000", f"ps={ps}"), "write", 1000, False)

    def test_legacy_startup_is_reported_without_a_claim(self):
        self.assertIsNone(write_prefill("", "write", 1000, False))
        observed = first_write_observation(None, 0, 2500, 1, 1)
        self.assertEqual(observed["accepted_addresses"], 1)


if __name__ == "__main__":
    unittest.main()
