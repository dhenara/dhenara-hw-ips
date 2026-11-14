"""Correlate decoded accesses with source order before response handshakes.

An exposed B/R can retain its source credit after its physical work completes.
Overlapping accesses therefore need physical retirement independent of READY.
The binding intentionally expects the present per-transaction granules; a
forwarding or merging implementation needs its own observed dependency binding.
"""

from system.host import require


def select(host, addresses, write, cursors):
    candidates = [
        tag
        for tag, t in host.transactions.items()
        if not (t.done or t.physical or t.error) and t.write == write and addresses & t.hazard
    ]
    require(bool(candidates), "physical access has no admitted owner")
    tag = min(candidates)
    t = host.transactions[tag]
    # Overlapping transactions have a serial order. Disjoint narrow ranges
    # inside one overfetched granule are ambiguous without another witness.
    require(
        all(t.hazard & host.transactions[n].hazard for n in candidates),
        "physical access has ambiguous disjoint owners",
    )
    host._older_overlap(tag)
    cursor = cursors.get(tag, 0)
    require(cursor < len(t.ranges), "excess physical read/write after all source beats")
    require(set(t.ranges[cursor]) <= addresses, "physical access misses next source beat")
    granule = len(addresses)
    require(granule in (32, 64), "physical access granule size")
    coalesced = (
        t.address.burst == 1
        and 1 << t.address.size == host.bus_bytes
        and t.ranges[cursor][0] % granule == 0
        and len(t.ranges) - cursor >= granule // host.bus_bytes
    )
    count = granule // host.bus_bytes if coalesced else 1
    require(
        all(set(beat) <= addresses for beat in t.ranges[cursor : cursor + count]),
        "physical access misses coalesced source beats",
    )
    cursors[tag] = cursor + count
    return tag


class GranuleOwnership:
    """Keep split physical fragments in one configured source granule.

    A masked 64-byte write uses two MWR16 commands, even when all requested
    bytes are in one half. The other, fully masked half still owns a command
    and pin tail. Advance the source cursor only after both halves bind.
    No DUT tag or completion signal is used to infer this relationship.
    """

    def __init__(self, host, granule_bytes=64):
        require(granule_bytes in (32, 64), "configured physical ownership granule")
        self.host = host
        self.granule_bytes = granule_bytes
        self.cursor = {}
        self.fragments = {}

    def begin(self, addresses, write):
        addresses = set(addresses)
        require(len(addresses) in (32, 64), "physical access granule size")
        start = min(addresses)
        require(
            start % len(addresses) == 0 and addresses == set(range(start, start + len(addresses))),
            "physical fragment alignment/coverage",
        )
        base = start // self.granule_bytes * self.granule_bytes
        complete = set(range(base, base + self.granule_bytes))
        require(addresses <= complete, "physical fragment exceeds configured granule")
        next_cursor = dict(self.cursor)
        tag = select(self.host, complete, write, next_cursor)
        seen = self.fragments.setdefault(tag, (base, set()))
        require(seen[0] == base, "physical access skips incomplete granule")
        require(not seen[1] & addresses, "duplicate physical granule fragment")
        seen[1].update(addresses)
        if seen[1] == complete:
            self.cursor[tag] = next_cursor[tag]
            del self.fragments[tag]
        return tag


class WriteOwnership:
    def __init__(self, host, granule_bytes=64):
        self.host = host
        self.source = GranuleOwnership(host, granule_bytes)
        self.cursor = self.source.cursor
        self.bursts = {}
        self.last_ps = {}

    def begin(self, identity, addresses, final_ps):
        require(identity not in self.bursts, "duplicate physical write identity")
        addresses = set(addresses)
        tag = self.source.begin(addresses, True)
        t = self.host.transactions[tag]
        require(len(t.w) == len(t.ranges), "write command before full validation")
        self.bursts[identity] = (tag, addresses)
        self.last_ps[tag] = max(self.last_ps.get(tag, 0), final_ps)

    def pin(self, identity, address, value):
        require(identity in self.bursts, "write data without decoded access")
        tag, addresses = self.bursts[identity]
        require(address in addresses, "write byte outside decoded access")
        self.host.pin_write(tag, address, bytes([value]), 1, self.host.epoch)

    def ready(self, tag, ps):
        t = self.host.transactions[tag]
        return (
            self.cursor.get(tag) == len(t.ranges)
            and ps >= self.last_ps[tag]
            and all(t.pin_counts.get(b, 0) == len(values) for b, values in t.write_values.items())
        )

    def complete_before(self, tag, ps):
        require(self.ready(tag, ps), "AXI write before complete physical payload and pin tail")
