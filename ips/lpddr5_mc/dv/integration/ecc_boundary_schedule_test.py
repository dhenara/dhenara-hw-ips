"""Compile-free tests of the fixture's literal CA scheduling selector.

The bounded extractor reads the actual SV field comparison, then tests its
behavior with independent encoded command families. It is not an HDL parser,
simulation, physical replay or a proof of the eventual injection timing.
"""

import re
import unittest
from pathlib import Path

from contracts import commands
from system.dram import decode


class PinSelectorTests(unittest.TestCase):
    def setUp(self):
        text = Path(__file__).with_name("ecc_boundary.svh").read_text()
        selectors = re.findall(
            r"if\(eb_kind==0 && ca\[0\]\.cs==1 && ca\[0\]\.address\[(\d+):0\]==(\d+)'d(\d+)\)",
            text,
        )
        self.assertEqual(len(selectors), 1, "unsupported/missing fixture pin selector expression")
        high, width, self.value = map(int, selectors[0])
        self.assertEqual(high + 1, width)
        self.mask = (1 << width) - 1

    def selected(self, cs, ca):
        return cs == 1 and ca & self.mask == self.value

    def test_all_wr32_banks_columns_and_ap_select_only_rank_zero(self):
        for bank in range(16):
            for column in range(0, 64, 2):
                for ap in (False, True):
                    ca = commands.access(True, 32, bank, column, ap)
                    decoded = decode(ca)
                    self.assertEqual((decoded.kind, decoded.beats), ("WR", 32))
                    self.assertTrue(self.selected(1, ca))
                    for cs in (0, 2, 3):
                        self.assertFalse(self.selected(cs, ca))

    def test_cas_read_write_fast_off_and_non_wr32_commands_cannot_schedule(self):
        # The retained failure's CA=44 is CAS(read), not a WR32 command.
        self.assertEqual(decode(44).kind, "CAS")
        vectors = [commands.cas(kind) for kind in ("read", "write", "fast", "off")]
        vectors += [0, 40, 104, 64, commands.mode_read(43)]
        vectors += list(commands.mode_write(16, 0))
        for bank in range(16):
            vectors.extend(commands.activate(0x12345, bank))
            vectors.append(commands.maintenance("PRE", bank))
            if bank < 8:
                vectors += [commands.maintenance(kind, bank) for kind in ("REF", "RFM")]
            for column in range(64):
                vectors += [commands.access(write, 16, bank, column) for write in (False, True)]
                vectors.append(commands.access(False, 32, bank, column))
                vectors.append(commands.access(True, 16, bank, column, masked=True))
        for ca in vectors:
            decoded = decode(ca)
            self.assertNotEqual((decoded.kind, decoded.beats), ("WR", 32))
            self.assertFalse(self.selected(1, ca), f"incorrect scheduling on {decoded} CA={ca:#06x}")


if __name__ == "__main__":
    unittest.main()
