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
    """Clocked bounded adapter for observed requests and driven PHY responses.

    sample() consumes the pins before a CK edge; its returned level is driven
    after that edge and observed on the next edge. Inputs are primitive port
    signals, so no producer module name is required. The monitor checks the
    *observed* returned pin rather than assuming its own drive reached the DUT.
    """

    def __init__(self, kind: str, script: ResponseScript, minimum: int = 4, maximum: int = 32):
        require(kind in ("update", "training", "pm", "fsp", "master"), "management kind")
        require(minimum >= 2 and maximum >= minimum + 2, "management bounds")
        self.kind, self.script, self.minimum, self.maximum = kind, script, minimum, maximum
        self.start: int | None = None
        self.last = -1
        self.samples: list[bool] = []
        self.release: int | None = None
        self.closed = False
        self.saw_low = False

    def sample(
        self,
        ck: int,
        *,
        request: bool,
        response: bool,
        traffic: bool = False,
        drained: bool = True,
        wck_disabled: bool = True,
    ) -> bool:
        require(ck == self.last + 1, "missing/repeated management sample")
        self.last = ck
        if self.start is None:
            require(response == self.script.initial, "unsolicited management response")
            if not request:
                return self.script.initial
            require(not self.closed and drained and wck_disabled and not traffic, "unsafe management admission")
            self.start = ck
        age = ck - self.start
        self.samples.append(response)
        require(not traffic or self.closed, "traffic during management ownership")
        if self.kind == "update":
            if not request and self.release is None:
                from contracts.update import check_update

                # Complete the observation horizon with low idle samples. Any
                # real later reassertion is still rejected below.
                padded = self.samples + [False] * max(0, self.maximum + 1 - len(self.samples))
                result = check_update(self.minimum, self.maximum, padded)
                require(result.fault is None and age == result.release + 1, "unsafe/late update request release")
                self.release, self.closed = age, True
            if self.closed:
                require(not request and not response, "update reassertion after release")
            else:
                require(age <= self.maximum, "management timeout")
        else:
            # FSP/training complete starts high and must be observed low before
            # a fresh high is completion. Optional PM may simply be declined.
            active = response != self.script.initial
            if active:
                self.saw_low = True
            if not request:
                require(self.saw_low or self.kind == "pm", "stale management completion")
                require(response == self.script.initial, "request release before management completion")
                self.closed = True
            require(self.closed or age <= self.maximum, "management timeout")
        # The next pre-edge sample observes this after-edge drive.
        return self.script.value(age + 1) if not self.closed else self.script.initial

    def finish(self) -> None:
        require(self.start is not None and self.closed, "unfinished management request")
