"""Transaction-level AXI4 order/error oracle for later front-end RTL.

AW/AR reserves the full byte hazard before W arrives. Same-edge arbitration is
port 0 before port 1, AW before AR per port. No exclusive monitor is implemented:
AxLOCK is accepted with normal side effects and successful responses are OKAY.
"""

from dataclasses import dataclass, field

from contracts.configuration import uint


@dataclass(frozen=True)
class Address:
    port: int
    axi_id: int
    address: int
    beats: int
    size: int
    burst: str = "INCR"
    lock: bool = False


def beat_bytes(a: Address, bus_bytes: int) -> list[tuple[int, ...]]:
    uint(a.address, 40, "address")
    uint(a.axi_id, 16, "ID")
    if a.port not in (0, 1) or a.beats < 1 or a.size < 1 or a.size & (a.size - 1) or a.size > bus_bytes:
        raise ValueError("dimensions")
    if a.burst not in ("INCR", "FIXED", "WRAP") or a.beats > (256 if a.burst == "INCR" else 16):
        raise ValueError("burst")
    if a.burst == "WRAP" and (a.beats not in (2, 4, 8, 16) or a.address % a.size):
        raise ValueError("WRAP alignment/length")
    aligned = a.address // a.size * a.size
    boundary = a.address // (a.size * a.beats) * (a.size * a.beats)
    result = []
    for i in range(a.beats):
        at = a.address if a.burst == "FIXED" or i == 0 else aligned + i * a.size
        if a.burst == "WRAP":
            at = boundary + (a.address - boundary + i * a.size) % (a.size * a.beats)
        end = (at // a.size + 1) * a.size
        values = tuple(range(at, end))
        if any(x >> 12 != a.address >> 12 or x >= 1 << 40 for x in values):
            raise ValueError("4KB/overflow")
        result.append(values)
    return result


@dataclass
class Transaction:
    tag: int
    a: Address
    write: bool
    addresses: list[tuple[int, ...]]
    response: str = "OKAY"
    incoming: list[tuple[int, int]] = field(default_factory=list)
    done: bool = False
    rindex: int = 0
    rdata: list[int] = field(default_factory=list)

    @property
    def hazard(self) -> set[int]:
        return {x for beat in self.addresses for x in beat}


class HostOracle:
    def __init__(
        self,
        bus_bytes: int = 16,
        ports: int = 2,
        depth: int = 64,
        regions: tuple[tuple[int, int], ...] = ((0, 1 << 30),),
    ):
        if bus_bytes not in (8, 16, 32) or ports not in (1, 2) or not 1 <= depth <= 64:
            raise ValueError("parameters")
        self.bus_bytes = bus_bytes
        self.ports = ports
        self.depth = depth
        self.regions = regions
        self.transactions: dict[int, Transaction] = {}
        self.next_tag = 0
        self.memory: dict[int, int] = {}
        self.fatal = False

    def admit(self, a: Address, write: bool) -> int:
        if self.fatal or a.port >= self.ports or len(self.transactions) >= self.depth:
            raise ValueError("admission stopped")
        if self.next_tag >= 1 << 64:
            raise ValueError("tag wrap requires drained reset")
        try:
            addresses = beat_bytes(a, self.bus_bytes)
            decoded = all(
                any(base <= x < base + size for base, size in self.regions) for beat in addresses for x in beat
            )
            response = "OKAY" if decoded else "DECERR"
        except ValueError:
            if not 1 <= a.beats <= 256:
                raise
            addresses = [() for _ in range(a.beats)]
            response = "DECERR"
        tag = self.next_tag
        self.next_tag += 1
        self.transactions[tag] = Transaction(tag, a, write, addresses, response)
        return tag

    def edge(self, requests: list[tuple[Address, bool]]) -> list[int]:
        return [self.admit(a, w) for a, w in sorted(requests, key=lambda x: (x[0].port, not x[1]))]

    def w(self, tag: int, data: int, strobes: int, last: bool) -> None:
        t = self.transactions[tag]
        if not t.write or t.done or len(t.incoming) >= t.a.beats:
            raise ValueError("unsolicited W")
        # AXI4 has no WID. A port consumes W in its admitted AW order.
        if any(
            x.write and x.a.port == t.a.port and len(x.incoming) < x.a.beats and x.tag < tag
            for x in self.transactions.values()
        ):
            raise ValueError("W must follow AW order")
        uint(data, self.bus_bytes * 8, "WDATA")
        uint(strobes, self.bus_bytes, "WSTRB")
        i = len(t.incoming)
        allowed = sum(1 << (x % self.bus_bytes) for x in t.addresses[i])
        if strobes & ~allowed or last != (i == t.a.beats - 1):
            t.response = "SLVERR" if t.response == "OKAY" else t.response
        t.incoming.append((data, strobes))

    def execute(self, tag: int) -> None:
        t = self.transactions[tag]
        if t.done:
            raise ValueError("duplicate execution")
        if t.write and len(t.incoming) != t.a.beats:
            raise ValueError("missing W beats")
        if any(x.tag < tag and not x.done and x.hazard & t.hazard for x in self.transactions.values()):
            raise ValueError("older overlapping transaction")
        if self.fatal and t.response == "OKAY":
            t.response = "SLVERR"
        if t.write:
            if t.response == "OKAY":
                for addresses, (data, strobes) in zip(t.addresses, t.incoming, strict=True):
                    for x in addresses:
                        lane = x % self.bus_bytes
                        if strobes >> lane & 1:
                            self.memory[x] = data >> (lane * 8) & 255
        else:
            t.rdata = [
                sum(self.memory.get(x, 0) << (8 * (x % self.bus_bytes)) for x in addresses)
                if t.response == "OKAY"
                else 0
                for addresses in t.addresses
            ]
        t.done = True

    def preview_forwarding(self, tag: int) -> tuple[list[int], set[int]]:
        """Expected byte overlay and unresolved write dependencies; no retirement."""
        t = self.transactions[tag]
        if t.write or t.done or t.response != "OKAY" or self.fatal:
            raise ValueError("forwarding target")
        image = dict(self.memory)
        dependencies = set()
        for old in sorted(self.transactions.values(), key=lambda x: x.tag):
            if old.tag >= tag or not old.write or old.done or not old.hazard & t.hazard:
                continue
            if old.response != "OKAY" or len(old.incoming) != old.a.beats:
                raise ValueError("forwarding dependency unavailable")
            dependencies.add(old.tag)
            for addresses, (data, strobes) in zip(old.addresses, old.incoming, strict=True):
                for address in addresses:
                    lane = address % self.bus_bytes
                    if strobes >> lane & 1:
                        image[address] = data >> (8 * lane) & 255
        return (
            [sum(image.get(x, 0) << (8 * (x % self.bus_bytes)) for x in addresses) for addresses in t.addresses],
            dependencies,
        )

    def _ordered(self, t: Transaction) -> None:
        if any(
            x.tag < t.tag and x.a.port == t.a.port and x.a.axi_id == t.a.axi_id and x.write == t.write
            for x in self.transactions.values()
        ):
            raise ValueError("same-ID response order")

    def response(self, tag: int) -> tuple[int, int, str, bool]:
        t = self.transactions[tag]
        self._ordered(t)
        if not t.done:
            raise ValueError("not complete")
        if t.write:
            del self.transactions[tag]
            return t.a.axi_id, 0, t.response, True
        i = t.rindex
        t.rindex += 1
        last = t.rindex == t.a.beats
        value = t.rdata[i]
        result = t.a.axi_id, value, t.response, last
        if last:
            del self.transactions[tag]
        return result

    def fail(self) -> None:
        self.fatal = True
        for t in self.transactions.values():
            if t.response == "OKAY":
                t.response = "SLVERR"
            if not t.write:
                t.rdata = [0] * t.a.beats
                t.done = True
            # Writes still consume remaining W beats before B can complete.

    def recover(self) -> None:
        if self.transactions:
            raise ValueError("responses/W still outstanding")
        self.memory.clear()
        self.fatal = False
        self.next_tag = 0
