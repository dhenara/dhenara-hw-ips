"""Thermal mutations must follow independently completed hot returns."""

import unittest

from check_observations import thermal_sensitivity
from contracts.commands import maintenance


class ThermalSensitivityTests(unittest.TestCase):
    def test_only_post_completion_refresh_on_the_observed_channel_is_removed(self):
        events = [
            {"kind": "phy", "channel": c, "ck": ck, "cs": 1, "ca": maintenance("REF", 0, True)}
            for c, ck in ((0, 5), (0, 10), (0, 11), (1, 11))
        ]
        events.append({"kind": "end", "ps": 100})
        original = [e.copy() for e in events]
        result = thermal_sensitivity(events, [[{"mr4": 13, "completed_ck": 10}], []])["missed_hot_refresh"]
        self.assertEqual(events, original)
        self.assertEqual(result[:2], events[:2])
        self.assertEqual((result[2]["cs"], result[2]["ca"]), (0, 0))
        self.assertEqual(result[3:], events[3:])
        self.assertEqual(thermal_sensitivity(events, [[{"mr4": 9, "completed_ck": 10}], []]), {})
        with self.assertRaisesRegex(RuntimeError, "actual subsequent refresh"):
            thermal_sensitivity(events, [[{"mr4": 13, "completed_ck": 100}], []])


if __name__ == "__main__":
    unittest.main()
