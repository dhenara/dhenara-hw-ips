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
