"""Bind complete physical reads, including padding, to admitted AXI ranges."""

from physical_ownership import select
from system.host import HostScoreboard, require


class ReadOwnership:
    def __init__(self, host: HostScoreboard):
        self.host = host
        self.bursts = {}
        self.pending = {}
        self.seen = {}
        self.last_ps = {}
        self.cursor = {}

    def begin(self, identity, addresses):
        addresses = set(addresses)
        require(identity not in self.bursts, "duplicate physical read identity")
        tag = select(self.host, addresses, False, self.cursor)
        self.bursts[identity] = (tag, addresses, set())
        self.pending[tag] = self.pending.get(tag, 0) + len(addresses)

    def observe(self, identity, addresses, ps):
        require(identity in self.bursts, "physical return without read ownership")
        tag, allowed, received = self.bursts[identity]
        addresses = set(addresses)
        require(addresses <= allowed and not addresses & received, "unexpected/duplicate physical read bytes")
        received.update(addresses)
        self.pending[tag] -= len(addresses)
        self.seen.setdefault(tag, set()).update(addresses & self.host.transactions[tag].hazard)
        self.last_ps[tag] = max(self.last_ps.get(tag, 0), ps)

    def ready(self, tag, ps):
        return (
            self.pending.get(tag) == 0
            and self.cursor.get(tag) == len(self.host.transactions[tag].ranges)
            and self.seen.get(tag, set()) == self.host.transactions[tag].hazard
            and ps >= self.last_ps[tag]
        )

    def complete_before(self, tag, ps):
        require(
            self.ready(tag, ps),
            "AXI read before complete physical payload including padding",
        )

    def finish(self):
        require(not any(self.pending.values()), "unsettled physical read padding")
