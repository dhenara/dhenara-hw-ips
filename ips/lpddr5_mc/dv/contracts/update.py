"""Executable sampled-edge contract for the selected conservative update policy.

This is an oracle for future controller tests, not controller RTL. Edge zero
asserts req after the edge. Inputs in sample[a] are observations at edge a.
The returned release edge is the registered output change after that edge.
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class UpdateResult:
    release: int | None
    fault: str | None
    traffic_allowed: bool


def check_update(
    minimum: int,
    maximum: int,
    ack: Sequence[bool],
    *,
    drained: bool = True,
    wck_disabled: bool = True,
    refresh_budget: int = 100,
    recovery_bound: int = 2,
) -> UpdateResult:
    if minimum < 2 or maximum < minimum + 2:
        raise ValueError("profile cannot support registered release margin")
    if not drained or not wck_disabled or refresh_budget < maximum + recovery_bound:
        return UpdateResult(None, "admission_denied", False)
    if len(ack) <= maximum:
        raise ValueError("need a complete trace")
    if ack[0]:
        return UpdateResult(None, "unsolicited_ack", False)
    assertion = next((i for i, value in enumerate(ack) if value), None)
    if assertion is None:
        return UpdateResult(minimum, None, True)
    if not 1 <= assertion < minimum:
        return UpdateResult(None, "late_or_unsolicited_ack", False)
    completion = next((i for i in range(assertion + 1, len(ack)) if not ack[i]), None)
    if completion is None or completion > maximum - 2:
        return UpdateResult(None, "stuck_or_late_completion", False)
    release = max(minimum, completion + 1)
    if any(ack[completion:]):
        return UpdateResult(None, "ack_reassertion", False)
    return UpdateResult(release, None, True)
