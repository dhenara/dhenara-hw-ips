"""Independent lifetime ledger across channel-to-host storage transfer.

The key includes generation AND both epochs. Transfer may release a channel
slot, but only an explicitly reserved owner inherits all remaining obligations.
Posted MR43 membership survives response handshake and slot reuse.
"""

from dataclasses import dataclass

from system.host import require


@dataclass(frozen=True)
class Identity:
    tag: int
    generation: int
    boot: int
    config: int


@dataclass
class Record:
    rank: int
    slot: int
    posted: bool
    link: bool
    physical: bool = False
    integrity: bool = False
    owner: str = "channel"
    response: int = 0
    exposed: bool = False
    handshaken: bool = False


class LifetimeMonitor:
    def __init__(self, ranks: int = 2, host_credits: int = 64, *, tmrr: int = 8, tmrw: int = 12):
        require(tmrr > 0 and tmrw > 0, "MR43 timing profile")
        self.tmrr, self.tmrw = tmrr, tmrw
        self.ranks, self.credits = ranks, host_credits
        self.live: dict[Identity, Record] = {}
        self.slots: dict[int, Identity] = {}
        self.epoch_members: list[set[Identity]] = [set() for _ in range(ranks)]
        self.first_write: list[int | None] = [None] * ranks
        self.late_errors: set[Identity] = set()
        self.boot, self.config = 0, 1
        self.polling: list[int | None] = [None] * ranks
        self.reopen = [0] * ranks
        self.last_ck = -1
        self.faulted = False

    def allocate(self, key: Identity, rank: int, slot: int, *, posted: bool, link: bool, ck: int) -> None:
        self._time(ck)
        require(not self.faulted and (key.boot, key.config) == (self.boot, self.config), "stale allocation epoch")
        require(key not in self.live and slot not in self.slots and 0 <= rank < self.ranks, "live slot/identity reuse")
        require(0 <= key.generation < 65536 and 0 <= key.tag < 1 << 64, "identity width")
        if link:
            require(self.polling[rank] is None and ck >= self.reopen[rank], "write during MR43 poll/read-clear guard")
            require(len(self.epoch_members[rank]) < 16, "MR43 per-rank epoch exceeds sixteen writes")
            self.epoch_members[rank].add(key)
            if self.first_write[rank] is None:
                self.first_write[rank] = ck
        self.live[key] = Record(rank, slot, posted, link)
        self.slots[slot] = key
        self.last_ck = ck

    def physical(self, key: Identity) -> None:
        require(key in self.live and not self.live[key].physical, "stale/duplicate physical witness")
        record = self.live[key]
        record.physical = True
        if not record.link:
            record.integrity = True

    def transfer(self, key: Identity, *, reserved: bool) -> None:
        require(key in self.live, "unknown completion transfer identity")
        record = self.live[key]
        require(
            record.owner == "channel" and reserved and (record.physical or record.response != 0),
            "completion transfer without reserved owner",
        )
        require(
            sum(r.owner == "host" and not r.handshaken for r in self.live.values()) < self.credits,
            "host response transfer credit overflow",
        )
        record.owner = "host"
        del self.slots[record.slot]

    def expose(self, key: Identity, response: int, *, handshaken: bool = False) -> None:
        require(key in self.live, "stale response feedback")
        record = self.live[key]
        require(not record.handshaken, "duplicate host handshake")
        require(record.owner == "host", "host response without owned storage")
        if record.exposed:
            require(response == record.response, "exposed response retracted")
        else:
            require(
                response == record.response
                and (record.physical or response != 0)
                and (record.integrity or record.posted or response != 0),
                "premature response exposure",
            )
            record.exposed = True
        record.handshaken = handshaken
        self._retire(key)

    def _retire(self, key: Identity) -> None:
        record = self.live[key]
        if record.integrity and record.handshaken:
            del self.live[key]

    def poll(self, rank: int, ck: int) -> None:
        self._time(ck)
        require(0 <= rank < self.ranks and self.first_write[rank] is not None, "MR43 without epoch")
        require(self.polling[rank] is None and ck >= self.reopen[rank], "duplicate MR43/read-clear guard")
        require(0 <= ck - self.first_write[rank] <= 256, "late MR43 poll")
        require(all(self.live[k].physical for k in self.epoch_members[rank]), "MR43 before final pin completion")
        self.polling[rank] = ck
        self.last_ck = ck

    def result(self, rank: int, ck: int, failed: bool, *, read_clear_ck: int) -> None:
        self._time(ck)
        require(0 <= rank < self.ranks and self.polling[rank] is not None, "MR43 result without poll")
        require(
            type(read_clear_ck) is int
            and ck >= self.polling[rank] + self.tmrr
            and read_clear_ck >= self.polling[rank] + self.tmrr + self.tmrw
            and read_clear_ck > ck,
            "MR43 result/read-clear timing",
        )
        for key in list(self.epoch_members[rank]):
            record = self.live[key]
            record.integrity = True
            if failed:
                if record.exposed and record.response == 0:
                    self.late_errors.add(key)
                elif not record.exposed:
                    record.response = 2
            self._retire(key)
        self.epoch_members[rank].clear()
        self.first_write[rank] = None
        self.polling[rank] = None
        self.reopen[rank] = read_clear_ck
        self.last_ck = ck

    def mr43(self, rank: int, ck: int, failed: bool) -> None:
        """Compatibility helper for old transaction fixtures, not pin binding.

        Live adapters must call poll/result with their sampled times and the
        qualified read-clear completion. This helper uses the configured tMRR
        return and tMRR+tMRW guard for transaction-level fixtures.
        """
        self.poll(rank, ck)
        self.result(rank, ck + self.tmrr, failed, read_clear_ck=ck + self.tmrr + self.tmrw)

    def _time(self, ck: int) -> None:
        # One simulation clock across ranks and APIs; equal-edge observations
        # are legal. A future read-clear guard is not an observed event.
        require(type(ck) is int and ck >= 0 and ck >= self.last_ck, "lifetime time reversal/invalid CK")
        require(
            all(
                first is None or self.polling[r] is not None or ck - first <= 256
                for r, first in enumerate(self.first_write)
            ),
            "missing MR43 poll at bound",
        )

    def advance(self, ck: int) -> None:
        self._time(ck)
        self.last_ck = ck

    def ownership_loss(self) -> None:
        self.faulted = True
        for key, record in self.live.items():
            if record.exposed and record.response == 0:
                self.late_errors.add(key)
            elif not record.exposed:
                record.response = 2

    def reset(self, *, destructive_done: bool) -> None:
        require(
            self.faulted and destructive_done and all(r.handshaken for r in self.live.values()),
            "reset before destructive confirmation/host settlement",
        )
        require(self.boot < (1 << 32) - 1, "boot epoch exhausted")
        self.live.clear()
        self.slots.clear()
        for members in self.epoch_members:
            members.clear()
        self.first_write = [None] * self.ranks
        self.polling = [None] * self.ranks
        self.reopen = [0] * self.ranks
        self.boot += 1
        self.config = 0
        self.faulted = False

    def finish(self) -> None:
        require(
            not self.live
            and not self.slots
            and not any(self.epoch_members)
            and not any(x is not None for x in self.polling),
            "unsettled lifetime/MR43 trace tail",
        )
