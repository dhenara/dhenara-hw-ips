"""One physical write may serve several original AXI writes, never their errors.

A merge context is supplied from independently decoded address/configuration,
not an opaque DUT merge key. Keep every original response/integrity obligation
and reject an intervening overlapping read even if its response has retired.
"""

from dataclasses import dataclass

from system.host import HostScoreboard, require


@dataclass(frozen=True)
class Context:
    region: int
    channel: int
    rank: int
    ecc: int
    profile: int
    boot: int
    config: int


class MergeMonitor:
    def __init__(self, host: HostScoreboard):
        self.host = host
        self.contexts: dict[int, Context] = {}
        self.pending: dict[int, tuple[list[int], dict[int, int], set[int]]] = {}

    def context(self, tag: int, value: Context) -> None:
        require(tag in self.host.transactions and tag not in self.contexts, "merge context identity")
        self.contexts[tag] = value

    def begin(self, physical: int, originals: list[int]) -> None:
        require(
            physical not in self.pending and len(originals) >= 2 and originals == sorted(set(originals)),
            "merge original identity/order",
        )
        h = self.host
        require(all(t in self.contexts for t in originals), "missing original merge context")
        require(len({self.contexts[t] for t in originals}) == 1, "merge context mismatch")
        values = [h.transactions[t] for t in originals]
        require(
            all(
                t.write and not t.error and not t.physical and not t.done and len(t.w) == len(t.ranges) for t in values
            ),
            "merge before complete validation",
        )
        hazard = set.union(*(t.hazard for t in values))
        require(len({b // 64 for b in hazard}) == 1, "merge crossed physical granule")
        require(
            not any(
                not t.write and originals[0] < n < originals[-1] and t.hazard & hazard
                for n, t in h.transactions.items()
            ),
            "intervening overlapping read",
        )
        require(
            not any(
                n < originals[-1] and n not in originals and t.hazard & hazard and not (t.physical and t.integrity)
                for n, t in h.transactions.items()
            ),
            "unmerged older hazard",
        )
        composed = {}
        for t in values:
            composed.update(t.expected)
        self.pending[physical] = list(originals), composed, set()

    def pin(self, physical: int, address: int, data: bytes, enables: int, epoch: int) -> None:
        require(
            physical in self.pending and epoch == self.host.epoch and not self.host.faulted,
            "stale merge physical epoch",
        )
        require(0 <= enables < 1 << len(data), "merged enable width")
        _, expected, seen = self.pending[physical]
        for i, value in enumerate(data):
            if enables >> i & 1:
                b = address + i
                require(b in expected and expected[b] == value and b not in seen, "wrong/duplicate merged byte")
                seen.add(b)
                self.host.memory[b] = value

    def physical(self, physical: int) -> None:
        require(physical in self.pending, "unknown merged physical completion")
        originals, expected, seen = self.pending.pop(physical)
        require(seen == expected.keys(), "merged completion before all bytes")
        for tag in originals:
            t = self.host.transactions[tag]
            t.pin_seen.update(t.expected)
            t.physical = True
            self.host.serial.update(t.expected)
        # Integrity and each original B must still be observed individually.

    def finish(self) -> None:
        require(not self.pending, "unsettled merged physical write")
