"""Ordered physical effects remain distinct while AXI responses are held."""

import unittest

from physical_ownership import WriteOwnership
from read_ownership import ReadOwnership
from system.host import Address, HostScoreboard, Violation


class PhysicalOwnershipTests(unittest.TestCase):
    def test_masked_fixed_write_keeps_both_mwr16_halves_and_repeated_values(self):
        host = HostScoreboard()
        tag = host.admit(Address(0, 1, 0, length=1, size=0, burst=0), True)
        host.w(0, 170, 1, False)
        host.w(0, 147, 1, True)
        writes = WriteOwnership(host)
        for beat, value in enumerate((170, 147)):
            writes.begin((beat, 0), range(32), 100 + beat * 100)
            writes.pin((beat, 0), 0, value)
            self.assertFalse(writes.ready(tag, 1000))
            # The second MWR16 is entirely masked, but must bind and drain.
            writes.begin((beat, 1), range(32, 64), 150 + beat * 100)
            self.assertEqual(writes.cursor[tag], beat + 1)
        self.assertFalse(writes.ready(tag, 249))
        writes.complete_before(tag, 250)
        host.physical_done(tag)
        host.integrity_done(tag)
        host.offer(tag, 0, 0, True)
        host.handshake(tag)
        host.finish()
        self.assertEqual(host.memory, {0: 147})
        self.assertEqual(host.transactions[tag].pin_counts, {0: 2})

    def test_duplicate_split_half_or_unmasked_padding_is_rejected(self):
        host = HostScoreboard()
        host.admit(Address(0, 1, 0, size=0), True)
        host.w(0, 170, 1, True)
        writes = WriteOwnership(host)
        writes.begin(0, range(32), 100)
        with self.assertRaisesRegex(Violation, "duplicate physical granule fragment"):
            writes.begin(1, range(32), 200)
        writes.begin(2, range(32, 64), 200)
        with self.assertRaisesRegex(Violation, "unexpected physical write byte"):
            writes.pin(2, 32, 0)

    def test_configured_g32_does_not_authorize_an_extra_masked_half(self):
        host = HostScoreboard()
        tag = host.admit(Address(0, 1, 0, size=0), True)
        host.w(0, 170, 1, True)
        writes = WriteOwnership(host, granule_bytes=32)
        writes.begin(0, range(32), 100)
        writes.pin(0, 0, 170)
        writes.complete_before(tag, 100)
        with self.assertRaisesRegex(Violation, "no admitted owner"):
            writes.begin(1, range(32, 64), 200)

    def test_split_fragments_cannot_skip_a_source_granule(self):
        host = HostScoreboard()
        host.admit(Address(0, 1, 0, length=7), True)
        for beat in range(8):
            host.w(0, 0, 65535, beat == 7)
        writes = WriteOwnership(host)
        writes.begin(0, range(32), 100)
        with self.assertRaisesRegex(Violation, "misses next source beat"):
            writes.begin(1, range(64, 96), 200)

    def test_write_read_write_snapshots_before_response_handshakes(self):
        host = HostScoreboard()
        first = host.admit(Address(0, 1, 0, length=3), True)
        middle = host.admit(Address(0, 1, 0, length=3), False)
        last = host.admit(Address(0, 1, 0, length=3), True)
        for value in (0x11, 0x22):
            for beat in range(4):
                host.w(0, int.from_bytes(bytes([value]) * 16, "little"), 65535, beat == 3)
        writes, reads = WriteOwnership(host), ReadOwnership(host)
        with self.assertRaisesRegex(Violation, "older unfinished overlap"):
            reads.begin(0, range(64))
        writes.begin(0, range(64), 200)
        for byte in range(64):
            writes.pin(0, byte, 0x11)
        self.assertFalse(writes.ready(first, 199))
        writes.complete_before(first, 200)
        host.physical_done(first)
        host.integrity_done(first)
        host.offer(first, 0, 0, True)  # Held B does not release its source credit.
        reads.begin(0, range(64))
        reads.observe(0, range(64), 300)
        reads.complete_before(middle, 300)
        with self.assertRaisesRegex(Violation, "older unfinished overlap"):
            writes.begin(1, range(64), 400)
        host.physical_done(middle)
        host.integrity_done(middle)
        host.offer(middle, int.from_bytes(bytes([0x11]) * 16, "little"), 0, False)
        writes.begin(1, range(64), 400)
        for byte in range(64):
            writes.pin(1, byte, 0x22)
        writes.complete_before(last, 400)
        host.physical_done(last)
        host.integrity_done(last)
        self.assertEqual(host.transactions[middle].read_data, [int.from_bytes(bytes([0x11]) * 16, "little")] * 4)
        self.assertEqual(set(host.serial.values()), {0x22})
        self.assertFalse(host.transactions[first].done)
        self.assertFalse(host.transactions[middle].done)

    def test_missing_effect_duplicate_command_and_unvalidated_write_fail(self):
        host = HostScoreboard()
        tag = host.admit(Address(0, 1, 0, length=3), True)
        writes = WriteOwnership(host)
        with self.assertRaisesRegex(Violation, "full validation"):
            writes.begin(0, range(64), 100)
        # A rejected oracle call is fatal to that replay; use a fresh binding.
        writes = WriteOwnership(host)
        for beat in range(4):
            host.w(0, 0, 65535, beat == 3)
        writes.begin(0, range(64), 100)
        with self.assertRaisesRegex(Violation, "duplicate physical write identity"):
            writes.begin(0, range(64), 100)
        for byte in range(63):
            writes.pin(0, byte, 0)
        with self.assertRaisesRegex(Violation, "complete physical payload"):
            writes.complete_before(tag, 100)
        with self.assertRaisesRegex(Violation, "excess physical"):
            writes.begin(1, range(64), 100)
        writes.pin(0, 63, 0)
        writes.complete_before(tag, 100)


if __name__ == "__main__":
    unittest.main()
