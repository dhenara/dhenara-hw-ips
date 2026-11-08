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


class WriteOwnership:
    def __init__(self, host):
        self.host = host
        self.cursor = {}
        self.bursts = {}
        self.last_ps = {}

    def begin(self, identity, addresses, final_ps):
        require(identity not in self.bursts, "duplicate physical write identity")
        addresses = set(addresses)
        tag = select(self.host, addresses, True, self.cursor)
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
