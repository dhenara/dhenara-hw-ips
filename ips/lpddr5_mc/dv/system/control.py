"""Observation-only configuration, epoch, recovery and PHY response seams."""

from dataclasses import dataclass, field

from system.host import require


@dataclass
class PublicationMonitor:
    channels: int = 2
    boot_epoch: int = 0
    config_epoch: int = 0
    prepared: set[int] = field(default_factory=set)
    pending: tuple[int, int] | None = None
    committed: set[int] = field(default_factory=set)
    faulted: bool = False

    def prepare(self, epoch: int, crc: int, *, drained: bool) -> None:
        require(
            not self.pending and drained and not self.faulted and self.config_epoch < epoch < 1 << 32,
            "configuration prepare/drain/epoch",
        )
        require(0 <= crc < 1 << 32, "configuration CRC width")
        self.pending = epoch, crc
        self.prepared.clear()
        self.committed.clear()

    def acknowledge(self, channel: int, epoch: int, crc: int) -> None:
        require(
            0 <= channel < self.channels and self.pending == (epoch, crc) and channel not in self.prepared,
            "stale/duplicate/partial configuration ACK",
        )
        self.prepared.add(channel)

    def commit(self, channel: int, epoch: int) -> None:
        require(
            self.pending is not None and self.pending[0] == epoch and len(self.prepared) == self.channels,
            "commit before all channels prepare",
        )
        require(0 <= channel < self.channels and channel not in self.committed, "duplicate channel commit")
        self.committed.add(channel)
        if len(self.committed) == self.channels:
            self.config_epoch, self.pending = epoch, None

    def traffic(self, boot: int, config: int) -> None:
        require(
            not self.pending
            and not self.faulted
            and self.config_epoch > 0
            and (boot, config) == (self.boot_epoch, self.config_epoch),
            "traffic during partial publication/stale epoch",
        )

    def abort(self) -> None:
        self.pending, self.faulted = None, True
        self.config_epoch = 0

    def reset(self, *, host_settled: bool, destructive_done: bool) -> None:
        require(host_settled and destructive_done and self.boot_epoch < (1 << 32) - 1, "unconfirmed recovery/wrap")
        self.boot_epoch += 1
        self.config_epoch, self.pending, self.faulted = 0, None, False
        self.prepared.clear()
        self.committed.clear()


@dataclass
class RecoveryMonitor:
    boot_epoch: int = 0
    profile_epoch: int = 0
    retired_profile: int = 0
    faulted: bool = False
    trained: bool = False
    trusted: bool = False
    diagnostic: str | None = None

    def fail(self, cause: str) -> None:
        if self.diagnostic is None:
            self.diagnostic = cause
        self.faulted, self.trusted, self.trained = True, False, False
        self.retired_profile = max(self.retired_profile, self.profile_epoch)

    def reset(self, *, settled: bool, confirmed: bool, volatile_cleared: bool) -> None:
        require(self.faulted and settled and confirmed and volatile_cleared, "incomplete destructive recovery")
        require(self.boot_epoch < (1 << 32) - 1, "boot epoch wrap")
        self.boot_epoch += 1
        self.faulted = False

    def boot(self, training: int, profile: int, *, known_state: bool, wck_disabled: bool) -> None:
        require(
            not self.faulted
            and training == self.boot_epoch
            and self.retired_profile < profile < 1 << 32
            and known_state
            and wck_disabled,
            "stale/unknown boot handover",
        )
        self.profile_epoch, self.trained, self.trusted = profile, True, True

    def enable(self) -> None:
        require(not self.faulted and self.trained and self.trusted, "unsafe traffic resume")


@dataclass(frozen=True)
class ResponseScript:
    """Controllable sampled-edge PHY stimulus, including faults and declines.

    `start` is relative to the observed request edge. A None start declines an
    optional request. A None end holds acknowledgement indefinitely for fault
    testing. Frequency change uses complete high -> low -> high, rather than
    treating an old high level as acceptance.
    """

    start: int | None
    end: int | None
    initial: bool = False

    def value(self, edge: int) -> bool:
        require(edge >= 0, "negative PHY script edge")
        active = self.start is not None and edge >= self.start and (self.end is None or edge < self.end)
        return bool(active) ^ self.initial

    def samples(self, maximum: int) -> list[bool]:
        return [self.value(i) for i in range(maximum + 1)]


class ManagementPort:
    """Sample primitive pins before each CK and drive the next response level.

    One instance observes one operation, including its idle tail. FSP and PM
    release before completion; update, training and master release afterwards.
    FSP target/clock witnesses are optional for pin-only replay, but when bound
    they must be supplied consistently. No clock or target witness is inferred
    from request release. Full rank/refresh/training checks remain separate.
    """

    def __init__(
        self,
        kind: str,
        script: ResponseScript,
        minimum: int = 4,
        maximum: int = 32,
        *,
        completion_limit: int | None = None,
    ):
        require(kind in ("update", "training", "pm", "fsp", "master"), "management kind")
        require(minimum >= 2 and maximum >= minimum + 2, "management bounds")
        require(script.initial == (kind in ("training", "fsp")), "management idle polarity")
        self.kind, self.script, self.minimum, self.maximum = kind, script, minimum, maximum
        self.completion_limit = maximum if completion_limit is None else completion_limit
        require(self.completion_limit > 0, "management completion bound")
        self.start: int | None = None
        self.last = -1
        self.samples: list[bool] = []
        self.release: int | None = None
        self.accepted: int | None = None
        self.returned: int | None = None
        self.closed = False
        self.disposition: str | None = None
        self.target: tuple[int, int, int] | None = None
        self.clock_bound: bool | None = None
        self.previous_clock_done = False

    def sample(
        self,
        ck: int,
        *,
        request: bool,
        response: bool,
        traffic: bool = False,
        drained: bool = True,
        wck_disabled: bool = True,
        clock_change_done: bool | None = None,
        target: tuple[int, int, int] | None = None,
        returned_state_known: bool = True,
    ) -> bool:
        require(ck == self.last + 1, "missing/repeated management sample")
        self.last = ck
        if self.start is None:
            require(response == self.script.initial, "unsolicited management response")
            if not request:
                return self.script.initial
            require(not self.closed and drained and wck_disabled and not traffic, "unsafe management admission")
            self.start = ck
            self.target = target
            self.clock_bound = clock_change_done is not None
        if self.closed:
            require(not request and response == self.script.initial, "management reassertion after return")
            if self.disposition == "declined" and self.kind == "fsp":
                require(not clock_change_done, "clock change on declined FSP")
            return self.script.initial
        # Return/release edges are still owned: traffic can resume next CK.
        require(drained and wck_disabled and not traffic, "unsafe management ownership/drain/WCK/traffic")
        age = ck - self.start
        self.samples.append(response)
        if self.kind == "update":
            self._update(age, request, response)
        elif self.kind == "fsp":
            self._fsp(age, request, response, clock_change_done, target)
        elif self.kind == "pm":
            self._pm(age, request, response)
        else:
            self._pulse(age, request, response, returned_state_known)
        # FSP/PM scripts must keep running after release until actual return.
        return self.script.value(age + 1) if not self.closed else self.script.initial

    def _close(self, disposition: str) -> None:
        self.closed, self.disposition = True, disposition

    def _update(self, age: int, request: bool, response: bool) -> None:
        if not request:
            from contracts.update import check_update

            padded = self.samples + [False] * max(0, self.maximum + 1 - len(self.samples))
            result = check_update(self.minimum, self.maximum, padded)
            require(result.fault is None and age == result.release + 1, "unsafe/late update request release")
            self.release = age
            self._close("acknowledged" if any(self.samples) else "declined")
        else:
            require(age <= self.maximum, "management timeout")

    def _fsp(self, age, request, response, clock_done, target) -> None:
        require((clock_done is not None) == self.clock_bound, "missing FSP clock witness")
        if self.release is None:
            require(target == self.target, "FSP target changed while requested")
            if target is not None:
                frequency, ratio, fsp = target
                require(0 <= frequency <= 31 and ratio in (2, 4) and 0 <= fsp <= 2, "FSP target encoding")
        if self.accepted is None and not response:
            require(1 <= age < self.minimum and self.release is None, "late FSP acknowledgement")
            self.accepted = age
        require(self.accepted is not None or not clock_done, "clock change before FSP acceptance")
        if self.release is None:
            if self.accepted is not None:
                require(not response, "FSP completion before release")
            if not request:
                self.release = age
                if self.accepted is None:
                    require(age == self.minimum and response, "invalid FSP decline release")
                    self._close("declined")
                else:
                    require(age >= self.minimum, "early FSP request release")
                    if self.clock_bound:
                        require(self.previous_clock_done, "FSP released before registered clock change")
            else:
                require(age <= self.maximum, "FSP request timeout")
        else:
            require(not request, "FSP request reassertion")
            require(age - self.release <= self.completion_limit, "FSP completion timeout")
            if response:
                self.returned = age
                self._close("acknowledged")
        self.previous_clock_done = bool(clock_done)

    def _pm(self, age: int, request: bool, response: bool) -> None:
        if self.accepted is None and response:
            require(1 <= age <= self.minimum and self.release is None, "late PM acknowledgement")
            self.accepted = age
        if self.release is None:
            if not request:
                self.release = age
                require(age >= self.minimum, "early PM request release")
                if self.accepted is None:
                    require(age == self.minimum, "invalid PM decline release")
                    self._close("declined")
                else:
                    require(self.accepted < age, "PM acknowledgement at request release")
            elif self.accepted is not None:
                require(response, "PM completion before release")
            require(age <= self.maximum, "PM request timeout")
        else:
            require(not request, "PM request reassertion")
        if self.release is not None and not self.closed:
            require(age - self.release <= self.completion_limit, "PM wakeup timeout")
            if not response:
                self.returned = age
                self._close("acknowledged")

    def _pulse(self, age: int, request: bool, response: bool, known: bool) -> None:
        # Training completion has high idle polarity. Master response is an
        # active-ownership pulse; rank-state/refresh checks use check_master.
        active = response != self.script.initial
        if self.accepted is None and active:
            bound = self.minimum if self.kind == "master" else self.maximum
            require(1 <= age <= bound, "late management acknowledgement")
            self.accepted = age
        if self.accepted is not None and not active and self.returned is None:
            self.returned = age
        require(self.returned is None or not active, "management response reassertion")
        if not request:
            require(self.returned is not None and age > self.returned, "stale/early management release")
            require(known, "unknown management returned state")
            self.release = age
            self._close("acknowledged")
        require(age <= self.maximum, "management timeout")
        if self.kind == "master" and self.accepted is None:
            require(age <= self.minimum, "master acknowledgement timeout")

    def finish(self) -> None:
        require(self.start is not None and self.closed, "unfinished management request")
