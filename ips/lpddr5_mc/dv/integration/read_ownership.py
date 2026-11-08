"""Bind complete physical reads, including padding, to admitted AXI ranges."""

from system.host import HostScoreboard, require


class ReadOwnership:
    def __init__(self, host: HostScoreboard):
        self.host = host
        self.bursts = {}
        self.pending = {}
        self.seen = {}
        self.last_ps = {}
        self.claimed = {}

    def begin(self, identity, addresses):
        addresses = set(addresses)
        candidates = [
            tag
            for tag, t in self.host.transactions.items()
            if not (t.done or t.physical or t.error or t.write) and addresses & t.hazard
        ]
        require(len(candidates) == 1 and identity not in self.bursts, "physical read has no unique admitted owner")
        tag = candidates[0]
        require(not addresses & self.claimed.get(tag, set()), "duplicate physical read range")
        self.claimed.setdefault(tag, set()).update(addresses)
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

    def complete_before(self, tag, ps):
        require(
            self.pending.get(tag) == 0
            and self.seen.get(tag, set()) == self.host.transactions[tag].hazard
            and ps >= self.last_ps[tag],
            "AXI read before complete physical payload including padding",
        )

    def finish(self):
        require(not any(self.pending.values()), "unsettled physical read padding")
