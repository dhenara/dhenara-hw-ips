"""Positive and faulty traces for the selected sampled DFI DVFS handshake."""

import unittest
from dataclasses import replace

from contracts.dvfs import Sample, check_dvfs


def trace(ack: int | None, release: int, returned: int | None) -> list[Sample]:
    return [
        Sample(
            start=i < release,
            complete=ack is None or i < ack or (returned is not None and i >= returned),
            frequency=1,
            ratio=2,
            fsp=1,
            clock_change_done=ack is not None and i >= release - 1,
            wck_disabled=True,
        )
        for i in range(20)
    ]


class DvfsTests(unittest.TestCase):
    def test_acknowledged_and_declined(self) -> None:
        for ack in (1, 3):
            result = check_dvfs(4, 5, trace(ack, 6, 11), drained=True)
            self.assertEqual(
                (result.disposition, result.returned_edge, result.activate_target), ("acknowledged", 11, True)
            )
        result = check_dvfs(4, 5, trace(None, 4, None), drained=True)
        self.assertEqual((result.disposition, result.activate_target), ("declined", False))

    def test_stale_high_never_activates_target(self) -> None:
        result = check_dvfs(4, 5, trace(None, 4, None), drained=True)
        self.assertFalse(result.activate_target)
        self.assertFalse(check_dvfs(4, 5, trace(1, 6, 8), drained=False).activate_target)

    def test_ack_boundary_early_high_timeout_and_release(self) -> None:
        for ack, release, returned, expected in (
            (4, 6, 8, "late_acknowledgement"),
            (5, 6, 8, "late_acknowledgement"),
            (1, 3, 5, "missing_or_early_release"),
            (1, 6, 6, "completion_before_release"),
            (1, 6, 12, "completion_timeout"),
            (1, 6, None, "completion_timeout"),
        ):
            with self.subTest(expected=expected):
                result = check_dvfs(4, 5, trace(ack, release, returned), drained=True)
                self.assertEqual(result.disposition, expected)
                self.assertFalse(result.activate_target)

    def test_settings_clock_and_ownership_faults(self) -> None:
        original = trace(1, 6, 8)
        for edge, mutation, expected in (
            (3, {"ratio": 4}, "settings_changed_while_requested"),
            (0, {"clock_change_done": True}, "clock_change_before_acknowledgement"),
            (5, {"clock_change_done": False}, "released_before_clock_change"),
            (8, {"wck_disabled": False}, "wck_enabled_at_transfer"),
            (7, {"start": True}, "request_reasserted"),
        ):
            changed = list(original)
            changed[edge] = replace(changed[edge], **mutation)
            result = check_dvfs(4, 5, changed, drained=True)
            self.assertEqual(result.disposition, expected)
            self.assertFalse(result.activate_target)


if __name__ == "__main__":
    unittest.main()
