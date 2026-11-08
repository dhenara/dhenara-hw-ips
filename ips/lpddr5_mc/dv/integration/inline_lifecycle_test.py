"""Shared-check merging and torn-pair images must preserve distinct versions."""

import copy
import unittest

from inline_lifecycle import DATA_BYTES, check_images, location, ordered_effects
from system.ecc import InlineWitness
from system.host import Violation


class InlineImageTests(unittest.TestCase):
    def setUp(self):
        self.memory = {128 * granule + i: (i + 17 * granule) & 255 for granule in range(4) for i in range(64)}
        checks = bytearray(64)
        for granule in range(4):
            for word in range(8):
                data = bytes(self.memory[128 * granule + word * 8 + i] for i in range(8))
                checks[granule * 8 + word] = InlineWitness.check_byte(int.from_bytes(data, "little"))
        self.image = {
            "channel": 0,
            "address": 0,
            "phase": "quiesced",
            "data": hex(int.from_bytes(bytes(self.memory[i] for i in range(64)), "little")),
            "check": hex(int.from_bytes(checks, "little")),
        }

    def test_neighbor_check_byte_loss_is_rejected(self):
        check_images([self.image], self.memory, {})
        mutant = copy.deepcopy(self.image)
        mutant["check"] = hex(int(mutant["check"], 0) ^ (1 << (24 * 8)))
        with self.assertRaisesRegex(Violation, "check-line merge"):
            check_images([mutant], self.memory, {})

    def test_torn_data_requires_old_check_state(self):
        payload = bytes([211]) * 64
        image = self.image | {"phase": "torn", "address": 512, "data": hex(int.from_bytes(payload, "little"))}
        check_images([image], self.memory, {110: {"data": payload}})
        new_check = InlineWitness.check_byte(int.from_bytes(payload[:8], "little"))
        mutant = image | {"check": hex(int(image["check"], 0) | (new_check << (32 * 8)))}
        with self.assertRaisesRegex(Violation, "check-line merge"):
            check_images([mutant], self.memory, {110: {"data": payload}})

    def test_channel_stripe_does_not_alias_check_neighbors(self):
        self.assertEqual(location(512), (0, 256))
        self.assertEqual(location(576), (1, 256))
        with self.assertRaisesRegex(Violation, "channel map"):
            check_images([self.image | {"channel": 1}], self.memory, {})

    def test_response_cannot_precede_check_tail(self):
        events = [
            {"kind": "aw", "ps": 1, "id": 120, "address": 0},
            {"kind": "b", "ps": 100, "id": 120},
        ]
        accesses = [
            {"channel": 0, "write": write, "address": address, "ps": at, "tail_ps": at + 10, "return_tail_ps": at + 10}
            for write, address, at in ((False, DATA_BYTES, 10), (False, 0, 30), (True, 0, 50), (True, DATA_BYTES, 70))
        ]
        ordered_effects(events, accesses, {120: {"last_ps": 5}})
        with self.assertRaisesRegex(Violation, "before data/check tail"):
            ordered_effects([events[0], events[1] | {"ps": 75}], accesses, {120: {"last_ps": 5}})
        with self.assertRaisesRegex(Violation, "missing inline physical"):
            ordered_effects(events, accesses[:-1], {120: {"last_ps": 5}})
        with self.assertRaisesRegex(Violation, "before source validation"):
            ordered_effects(events, accesses, {120: {"last_ps": 11}})


if __name__ == "__main__":
    unittest.main()
