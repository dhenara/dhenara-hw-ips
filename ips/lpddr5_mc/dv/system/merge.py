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
        self.epoch = host.epoch
        self.members: dict[int, int] = {}
        self.used: set[int] = set()
        self.contexts: dict[int, Context] = {}
        self.pending: dict[int, tuple[list[int], dict[int, int], set[int]]] = {}

    def _current(self) -> None:
        require(self.host.epoch == self.epoch and not self.host.faulted, "stale/faulted merge epoch")

    def reset(self) -> None:
        """Observe an already confirmed, host-settled destructive reset.

        Physical correlation IDs can be reused only in this new host epoch;
        a live slot must include its generation in the adapter's integer ID.
        """
        require(self.host.epoch == self.epoch + 1 and not self.host.faulted, "unconfirmed merge reset")
        self.epoch = self.host.epoch
        self.contexts.clear()
        self.pending.clear()
        self.members.clear()
        self.used.clear()

    def context(self, tag: int, value: Context) -> None:
        self._current()
        require(tag in self.host.transactions and tag not in self.contexts, "merge context identity")
        self.contexts[tag] = value

    def begin(self, physical: int, originals: list[int]) -> None:
        self._current()
        require(
            physical not in self.used and len(originals) >= 2 and originals == sorted(set(originals)),
            "merge original identity/order",
        )
        require(not any(t in self.members for t in originals), "original already owns pending physical merge")
        h = self.host
        require(all(t in self.contexts for t in originals), "missing original merge context")
        require(len({self.contexts[t] for t in originals}) == 1, "merge context mismatch")
        values = [h.transactions[t] for t in originals]
        require(
            all(
                t.write
                and not t.error
                and not t.failed
                and not t.pin_seen
                and not t.physical
                and not t.done
                and len(t.w) == len(t.ranges)
                for t in values
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
        self.members.update((tag, physical) for tag in originals)
        self.used.add(physical)

    def _originals(self, physical: int) -> None:
        self._current()
        require(physical in self.pending, "unknown/duplicate merged physical completion")
        require(
            all(
                self.members.get(t) == physical
                and t in self.host.transactions
                and not self.host.transactions[t].physical
                and not self.host.transactions[t].pin_seen
                and not self.host.transactions[t].done
                and not self.host.transactions[t].error
                and not self.host.transactions[t].failed
                for t in self.pending[physical][0]
            ),
            "duplicate/stale original physical ownership",
        )

    def pin(self, physical: int, address: int, data: bytes, enables: int, epoch: int) -> None:
        self._originals(physical)
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
        self._originals(physical)
        originals, expected, seen = self.pending[physical]
        require(seen == expected.keys(), "merged completion before all bytes")
        del self.pending[physical]
        for tag in originals:
            del self.members[tag]
            t = self.host.transactions[tag]
            t.pin_seen.update(t.expected)
            t.physical = True
            self.host.serial.update(t.expected)
        # Integrity and each original B must still be observed individually.

    def finish(self) -> None:
        self._current()
        require(not self.pending and not self.members, "unsettled merged physical write")
