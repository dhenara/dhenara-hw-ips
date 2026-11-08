"""Narrow AXI bytes cannot hide an incomplete physical granule return."""

import unittest

from read_ownership import ReadOwnership
from system.host import Address, HostScoreboard, Violation


class ReadOwnershipTests(unittest.TestCase):
    def test_padding_is_not_a_second_source_but_must_complete(self):
        host = HostScoreboard()
        tag = host.admit(Address(0, 1, 3, size=2), False)  # One byte in its first beat.
        reads = ReadOwnership(host)
        reads.begin((0, 1), range(64))
        with self.assertRaisesRegex(Violation, "duplicate physical read range"):
            reads.begin((0, 2), range(64))
        reads.observe((0, 1), {2, 3}, 100)
        with self.assertRaisesRegex(Violation, "including padding"):
            reads.complete_before(tag, 100)
        for at in range(0, 64, 2):
            if at != 2:
                reads.observe((0, 1), {at, at + 1}, 100 + at)
        with self.assertRaisesRegex(Violation, "including padding"):
            reads.complete_before(tag, 100)
        reads.complete_before(tag, 200)
        reads.finish()

    def test_multiple_granules_keep_one_complete_source_obligation(self):
        host = HostScoreboard()
        tag = host.admit(Address(0, 1, 60, length=1, size=4), False)
        reads = ReadOwnership(host)
        reads.begin((0, 1), range(64))
        reads.begin((0, 2), range(64, 128))
        reads.observe((0, 1), range(64), 100)
        with self.assertRaises(Violation):
            reads.complete_before(tag, 100)
        reads.observe((0, 2), range(64, 128), 200)
        reads.complete_before(tag, 200)

    def test_unadmitted_error_ambiguous_and_duplicate_effects_fail(self):
        host = HostScoreboard()
        reads = ReadOwnership(host)
        with self.assertRaises(Violation):
            reads.begin((0, 1), range(64))
        host.admit(Address(0, 1, 0, size=4), False)
        reads.begin((0, 1), range(64))
        reads.observe((0, 1), {0, 1}, 100)
        with self.assertRaises(Violation):
            reads.observe((0, 1), {0, 1}, 101)
        with self.assertRaises(Violation):
            reads.observe((0, 1), {64, 65}, 101)
        with self.assertRaises(Violation):
            reads.finish()
        host.admit(Address(1, 1, 8, size=4), False)
        with self.assertRaises(Violation):
            reads.begin((0, 2), range(64))
        invalid = HostScoreboard(mapped_end=32)
        invalid.admit(Address(0, 1, 64, size=4), False)
        with self.assertRaises(Violation):
            ReadOwnership(invalid).begin((0, 1), range(64, 128))


if __name__ == "__main__":
    unittest.main()
