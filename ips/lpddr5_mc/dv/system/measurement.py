"""Completed host payload accounting, with integral windows and a checked tail.

This accepts observations from a bus adapter, not reference schedule counts.
Synthetic fixtures qualify the accountant only. No report from this module
alone establishes legal protocol/data behavior or RTL throughput acceptance.
"""

from dataclasses import dataclass, field

from system.host import require


@dataclass
class Benchmark:
    direction: str
    service_div: int
    periods: int = 100
    period_ck: int = 3000
    warmup: int = 10
    measured: int = 90
    credits: int = 64
    accepted: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)
    completed: set[tuple[int, int]] = field(default_factory=set)
    counts: list[list[int]] = field(default_factory=lambda: [[0] * 100 for _ in range(2)])
    boundaries: dict[tuple[int, int], int] = field(default_factory=dict)
    outstanding: list[int] = field(default_factory=lambda: [0, 0])
    maximum: list[int] = field(default_factory=lambda: [0, 0])
    stalls: dict[str, int] = field(default_factory=dict)
    last_time: int = -1

    def __post_init__(self) -> None:
        require(self.direction in ("read", "write") and self.service_div in (1, 2, 4), "benchmark mode")
        require(
            self.periods >= 100 and self.period_ck == 3000 and self.warmup == 10 and self.measured == 90,
            "benchmark window",
        )
        self.counts = [[0] * self.periods for _ in range(2)]

    def _time(self, at: int, channel: int) -> None:
        require(type(at) is int and self.last_time <= at, "benchmark time/order")
        require(channel in (0, 1), "benchmark channel")
        self.last_time = at

    def admission(self, at: int, channel: int, tag: int, size: int = 64) -> None:
        self._time(at, channel)
        key = channel, tag
        require(size == 64 and key not in self.accepted, "benchmark identity/size")
        require(self.outstanding[channel] < self.credits, "benchmark credit overflow")
        self.accepted[key] = at, size
        self.outstanding[channel] += 1
        self.maximum[channel] = max(self.maximum[channel], self.outstanding[channel])

    def completion(self, at: int, channel: int, tag: int, *, physical_ok: bool, integrity_ok: bool) -> None:
        """Invoke on final R handshake or B handshake after scoreboard validation."""
        self._time(at, channel)
        key = channel, tag
        require(key in self.accepted and key not in self.completed, "unmatched/duplicate completion")
        require(physical_ok and integrity_ok, "benchmark data/protocol/integrity failure")
        self.completed.add(key)
        self.outstanding[channel] -= 1
        # End is half-open. A completion at 300000 belongs to the tail.
        if at < self.periods * self.period_ck:
            self.counts[channel][at // self.period_ck] += self.accepted[key][1]

    def boundary(self, at: int, channel: int) -> None:
        self._time(at, channel)
        require(at % self.period_ck == 0 and (channel, at) not in self.boundaries, "boundary cadence/duplicate")
        self.boundaries[channel, at] = self.outstanding[channel]

    def stall(self, reason: str, ck: int = 1) -> None:
        require(
            reason in {"refresh", "row", "zq", "mr4", "cdc", "selection", "response", "data", "turnaround", "cap"}
            and type(ck) is int
            and ck > 0,
            "unexplained stall",
        )
        self.stalls[reason] = self.stalls.get(reason, 0) + ck

    def report(self, observed_until: int) -> dict:
        require(observed_until >= max(self.last_time, self.periods * self.period_ck), "truncated benchmark")
        start, end = self.warmup, self.warmup + self.measured
        target_bursts = 339 if self.service_div == 4 else 375
        expected = target_bursts * 64 * 2 * self.measured
        total = sum(sum(c[start:end]) for c in self.counts)
        backlog = []
        for channel in range(2):
            keys = [(channel, p * self.period_ck) for p in range(start, end + 1)]
            require(all(key in self.boundaries for key in keys), "missing backlog boundary")
            values = [self.boundaries[key] for key in keys]
            # A periodic saturated source must not build debt each period. Requiring
            # equal endpoints also catches a slow positive slope hidden under credits.
            require(values[-1] <= values[0], "growing steady backlog")
            backlog.append(values)
        missing = max(0, expected - total)
        require(not missing or bool(self.stalls), "missing payload without stall causes")
        return {
            "kind": "measurement_only",
            "direction": self.direction,
            "service_div": self.service_div,
            "start_ck": start * self.period_ck,
            "end_ck_exclusive": end * self.period_ck,
            "completed_bytes": total,
            "reference_bytes": expected,
            "missing_bytes": missing,
            "threshold_passed": total * 100 >= expected * 99,
            "gb_per_second": total / (self.measured * self.period_ck * 1.25),
            "max_outstanding": self.maximum,
            "period_backlog": backlog,
            "completed_bytes_per_channel_period": self.counts,
            "stall_ck": self.stalls,
            "controller_qualified": False,
        }


@dataclass
class Fairness:
    depth: int = 64
    age_threshold: int = 32
    tick_div: int = 8
    issue_gap: int = 64
    eligible: dict[int, int] = field(default_factory=dict)
    suspended: dict[str, int] = field(default_factory=dict)
    last_grant: int = 0
    elapsed: int = 0
    worst_gap: int = 0

    @property
    def bound(self) -> int:
        return (self.age_threshold + 1) * self.tick_div + self.depth * self.issue_gap

    def step(self, eligible_tags: set[int], grant: int | None = None, suspended: str | None = None) -> None:
        self.elapsed += 1
        if suspended:
            require(
                suspended in {"overlap", "cap", "missing_w", "response", "ecc", "ownership", "maintenance"},
                "fairness suspension reason",
            )
            require(grant is None, "grant in suspended interval")
            self.suspended[suspended] = self.suspended.get(suspended, 0) + 1
            return
        if eligible_tags:
            self.last_grant += 1
            require(self.last_grant <= self.issue_gap, "unproved issue gap G including trace tail")
        else:
            self.last_grant = 0
        for tag in list(self.eligible):
            if tag not in eligible_tags:
                del self.eligible[tag]
        for tag in eligible_tags:
            self.eligible[tag] = self.eligible.get(tag, 0) + 1
            require(self.eligible[tag] <= self.bound, "conditional starvation including trace tail")
        if grant is not None:
            require(grant in eligible_tags, "ineligible grant")
            self.worst_gap = max(self.worst_gap, self.last_grant)
            self.last_grant = 0
            self.eligible.pop(grant, None)


@dataclass
class Caps:
    window: int
    limits: tuple[int, int]
    work_conserving: bool = False
    used: list[int] = field(default_factory=lambda: [0, 0])
    current: int = -1
    last_ck: int = -1

    def issue(self, ck: int, direction: int, amount: int, opposite_eligible: bool) -> None:
        require(ck >= self.last_ck and direction in (0, 1) and amount in (32, 64) and self.window > 0, "cap input")
        self.last_ck = ck
        if ck // self.window != self.current:
            self.used = [0, 0]
            self.current = ck // self.window
        limit = self.limits[direction]
        require(limit > 0, "zero disables direction; negative cap is invalid")
        if self.used[direction] + amount > limit:
            require(
                self.work_conserving and not opposite_eligible and sum(self.used) + amount <= sum(self.limits),
                "actual issue exceeds byte cap",
            )
        self.used[direction] += amount
