"""Sampled DFI 5.0 4.11 frequency-change handshake oracle.

This checks the low-then-high acknowledgement sequence and stable settings.
It does not prove JEDEC FSP programming, refresh retention, clock switching or
PHY re-training. Edges are DFI samples, not wall-clock timeout measurements.
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    start: bool
    complete: bool
    frequency: int
    ratio: int
    fsp: int
    clock_change_done: bool
    wck_disabled: bool


@dataclass(frozen=True)
class Result:
    disposition: str
    returned_edge: int | None
    activate_target: bool


def check_dvfs(minimum: int, completion_limit: int, trace: Sequence[Sample], *, drained: bool) -> Result:
    if minimum < 2 or completion_limit < 1 or len(trace) < minimum + 2:
        raise ValueError("incomplete or unrepresentable handshake trace")
    if not drained:
        return Result("admission_denied", None, False)
    if not trace[0].start or not trace[0].complete:
        return Result("invalid_initial_state", None, False)
    target = (trace[0].frequency, trace[0].ratio, trace[0].fsp)
    if target[1] not in (2, 4) or not 0 <= target[2] <= 2 or not 0 <= target[0] <= 31:
        raise ValueError("unsupported target encoding")
    release = next((i for i, item in enumerate(trace) if not item.start), None)
    if release is None or release < minimum:
        return Result("missing_or_early_release", None, False)
    if any((item.frequency, item.ratio, item.fsp) != target for item in trace[:release]):
        return Result("settings_changed_while_requested", None, False)
    acknowledged = next((i for i, item in enumerate(trace) if not item.complete), None)
    if acknowledged is None:
        if release != minimum or any(item.clock_change_done for item in trace):
            return Result("invalid_decline", None, False)
        if any(not item.wck_disabled for item in trace[: release + 1]):
            return Result("wck_enabled_at_transfer", None, False)
        return Result("declined", release, False)
    if not 1 <= acknowledged < minimum:
        return Result("late_acknowledgement", None, False)
    if any(item.complete for item in trace[acknowledged : release + 1]):
        return Result("completion_before_release", None, False)
    if any(item.clock_change_done for item in trace[:acknowledged]):
        return Result("clock_change_before_acknowledgement", None, False)
    if not trace[release - 1].clock_change_done:
        return Result("released_before_clock_change", None, False)
    returned = next((i for i in range(release + 1, len(trace)) if trace[i].complete), None)
    if returned is None or returned - release > completion_limit:
        return Result("completion_timeout", None, False)
    if any(not item.wck_disabled for item in trace[: returned + 1]):
        return Result("wck_enabled_at_transfer", None, False)
    if any(item.start for item in trace[release : returned + 1]):
        return Result("request_reasserted", None, False)
    return Result("acknowledged", returned, True)
