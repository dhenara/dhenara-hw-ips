"""Serial host oracle with a distinct physical-memory witness.

Adapters call admission only on AW/AR handshakes, W in per-port AW order,
and offer/handshake independently. Pin writes are observed memory effects,
never inferred from a DUT completion flag. Byte ranges include unstrobed bytes
until the complete W stream validates. This intentionally uses a serial
reference, rather than reproducing a frontend's forwarding/merge algorithm.
"""

from collections import deque
from dataclasses import dataclass, field


class Violation(ValueError):
    """An observable interface contract was violated."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise Violation(reason)


@dataclass(frozen=True)
class Address:
    port: int
    id: int
    address: int
    length: int = 0
    size: int = 4
    burst: int = 1
    lock: bool = False


def byte_ranges(a: Address, bus_bytes: int, address_bits: int = 40) -> list[tuple[int, ...]]:
    require(bus_bytes in (8, 16, 32), "bus width")
    require(0 <= a.length <= 255 and 0 <= a.size <= bus_bytes.bit_length() - 1, "length/size")
    require(a.burst in (0, 1, 2) and 0 <= a.address < 1 << address_bits, "burst/address")
    count, width = a.length + 1, 1 << a.size
    require(a.burst != 0 or count <= 16, "FIXED length")
    require(a.burst != 2 or count in (2, 4, 8, 16) and a.address % width == 0, "WRAP alignment/length")
    aligned = a.address // width * width
    wrap = aligned // (width * count) * width * count
    result = []
    for n in range(count):
        start = a.address if n == 0 or a.burst == 0 else aligned + n * width
        if a.burst == 2:
            start = wrap + (a.address - wrap + n * width) % (width * count)
        end = start // width * width + width
        require(end <= 1 << address_bits and start // 4096 == a.address // 4096 == (end - 1) // 4096, "4KB/overflow")
        result.append(tuple(range(start, end)))
    return result


@dataclass
class Transaction:
    address: Address
    write: bool
    ranges: list[tuple[int, ...]]
    error: int = 0
    w: list[tuple[int, int]] = field(default_factory=list)
    expected: dict[int, int] = field(default_factory=dict)
    pin_seen: set[int] = field(default_factory=set)
    physical: bool = False
    integrity: bool = False
    failed: bool = False
    read_data: list[int] = field(default_factory=list)
    response_index: int = 0
    offered: tuple[int, int, bool] | None = None
    done: bool = False
    dependencies: set[int] = field(default_factory=set)

    @property
    def hazard(self) -> set[int]:
        return {b for beat in self.ranges for b in beat}


class HostScoreboard:
    def __init__(self, bus_bytes: int = 16, ports: int = 2, capacity: int = 64, mapped_end: int = 1 << 30):
        require(ports in (1, 2) and capacity > 0 and bus_bytes in (8, 16, 32), "host configuration")
        self.bus_bytes, self.ports, self.capacity, self.mapped_end = bus_bytes, ports, capacity, mapped_end
        self.transactions: dict[int, Transaction] = {}
        self.aw = [deque() for _ in range(ports)]
        self.memory: dict[int, int] = {}
        self.serial: dict[int, int] = {}
        self.next_tag = 0
        self.epoch = 0
        self.faulted = False
        self.late_errors: set[int] = set()
        self.handshaken_bytes = {False: 0, True: 0}

    def admit(self, a: Address, write: bool) -> int:
        require(not self.faulted and 0 <= a.port < self.ports and 0 <= a.id < 65536, "admission/port/ID")
        require(sum(not t.done for t in self.transactions.values()) < self.capacity, "response credit overflow")
        require(0 <= a.length <= 255, "AXI LEN representation")
        try:
            ranges = byte_ranges(a, self.bus_bytes)
            decoded = all(b < self.mapped_end for beat in ranges for b in beat)
        except Violation:
            # Invalid bursts still settle the full admitted AXI count.
            ranges, decoded = [()] * (a.length + 1), False
        tag = self.next_tag
        self.next_tag += 1
        self.transactions[tag] = Transaction(a, write, ranges, 0 if decoded else 3)
        if write:
            self.aw[a.port].append(tag)
        return tag

    def edge(self, requests: list[tuple[Address, bool]]) -> list[int]:
        """One testbench-global sampled instant: port 0 first, AW before AR."""
        return [self.admit(a, write) for a, write in sorted(requests, key=lambda item: (item[0].port, not item[1]))]

    def w(self, port: int, value: int, strobe: int, last: bool) -> int:
        require(0 <= port < self.ports and bool(self.aw[port]), "W without AW")
        tag = self.aw[port][0]
        t = self.transactions[tag]
        i = len(t.w)
        require(0 <= value < 1 << (8 * self.bus_bytes) and 0 <= strobe < 1 << self.bus_bytes, "W representation")
        allowed = sum(1 << (b % self.bus_bytes) for b in t.ranges[i])
        if (strobe & ~allowed or last != (i == t.address.length)) and t.error != 3:
            t.error = 2
        t.w.append((value, strobe))
        if i == t.address.length:
            self.aw[port].popleft()
            if not t.error:
                for beat, (data, mask) in zip(t.ranges, t.w, strict=True):
                    for b in beat:
                        lane = b % self.bus_bytes
                        if mask >> lane & 1:
                            t.expected[b] = data >> (lane * 8) & 255
        return tag

    def _older_overlap(self, tag: int) -> None:
        t = self.transactions[tag]
        require(
            not any(
                not (old.done or old.physical and old.integrity) and old.hazard & t.hazard
                for n, old in self.transactions.items()
                if n < tag
            ),
            "older unfinished overlap",
        )

    def pin_write(self, tag: int, address: int, data: bytes, enables: int, epoch: int) -> None:
        require(epoch == self.epoch and tag in self.transactions, "stale pin identity")
        require(address >= 0 and 0 <= enables < 1 << len(data), "pin byte-enable width")
        t = self.transactions[tag]
        require(
            t.write and len(t.w) == len(t.ranges) and not t.error and not self.faulted, "write before full validation"
        )
        self._older_overlap(tag)
        for i, value in enumerate(data):
            if enables >> i & 1:
                b = address + i
                require(b in t.expected and t.expected[b] == value, "unexpected physical write byte")
                require(b not in t.pin_seen, "duplicate physical byte")
                self.memory[b] = value
                t.pin_seen.add(b)

    def physical_done(self, tag: int) -> None:
        t = self.transactions[tag]
        require(not t.physical and not t.error, "duplicate/error physical completion")
        self._older_overlap(tag)
        if t.write:
            require(t.pin_seen == t.expected.keys(), "physical completion before all pin bytes")
            self.serial.update(t.expected)
        else:
            t.read_data = []
            for beat in t.ranges:
                value = 0
                for b in beat:
                    require(self.memory.get(b, 0) == self.serial.get(b, 0), "physical/serial memory mismatch")
                    value |= self.serial.get(b, 0) << (b % self.bus_bytes * 8)
                t.read_data.append(value)
        t.physical = True

    def integrity_done(self, tag: int, failed: bool = False) -> None:
        t = self.transactions[tag]
        require(t.physical and not t.integrity, "integrity without physical completion")
        require(
            all(self.transactions[n].physical and self.transactions[n].integrity for n in t.dependencies),
            "forwarding dependency has no physical/integrity outcome",
        )
        require(
            failed or not any(self.transactions[n].failed for n in t.dependencies),
            "failed forwarding dependency returned success",
        )
        t.integrity, t.failed = True, failed
        if failed and t.offered is not None and t.offered[1] == 0:
            self.late_errors.add(tag)

    def forwarded(self, tag: int) -> None:
        """Expected read snapshot; successful retirement still waits on writers.

        This overlays accepted byte writes in serial order and does not inspect
        the DUT's forwarding decisions. Physical readiness is inherited through
        every contributing writer rather than invented from a read completion.
        """
        t = self.transactions[tag]
        require(not t.write and not t.error and not t.physical, "invalid forwarded read")
        snapshot = dict(self.serial)
        for n, older in self.transactions.items():
            if n >= tag or not older.hazard & t.hazard or not older.write:
                continue
            require(len(older.w) == len(older.ranges) and not older.error, "forwarding before complete validated W")
            snapshot.update(older.expected)
            t.dependencies.add(n)
        require(bool(t.dependencies), "forwarding without a write dependency")
        t.read_data = [sum(snapshot.get(b, 0) << (b % self.bus_bytes * 8) for b in beat) for beat in t.ranges]
        t.physical = True

    def offer(self, tag: int, data: int, response: int, last: bool, *, posted: bool = False) -> None:
        t = self.transactions[tag]
        require(not t.done, "duplicate response")
        require(
            not any(
                not old.done
                and old.write == t.write
                and old.address.port == t.address.port
                and old.address.id == t.address.id
                for n, old in self.transactions.items()
                if n < tag
            ),
            "same-ID response reorder",
        )
        payload = (data, response, last)
        if t.offered is not None:
            require(t.offered == payload, "VALID payload changed under backpressure")
            return
        if t.write:
            require(len(t.w) == len(t.ranges), "B before accepted W count")
        expected_error = t.error or (2 if t.failed or self.faulted else 0)
        require(response == expected_error, "response code/EXOKAY")
        if not response:
            require(t.physical and (posted and t.write or t.integrity), "response before physical/integrity outcome")
        if t.write:
            require(data == 0 and last, "B representation")
        else:
            expected = 0 if response else t.read_data[t.response_index]
            require(data == expected and last == (t.response_index == t.address.length), "R data/count/RLAST")
        t.offered = payload

    def handshake(self, tag: int) -> None:
        t = self.transactions[tag]
        require(t.offered is not None and not t.done, "handshake without VALID")
        if t.write:
            t.done = True
            self.handshaken_bytes[True] += sum(mask.bit_count() for _, mask in t.w) if t.offered[1] == 0 else 0
        else:
            if t.offered[1] == 0:
                self.handshaken_bytes[False] += len(t.ranges[t.response_index])
            t.response_index += 1
            t.done = t.response_index == len(t.ranges)
        # An exposed posted OKAY must remain identifiable after its handshake.
        if not t.done:
            t.offered = None

    def fail(self) -> None:
        self.faulted = True
        for tag, t in self.transactions.items():
            if t.write and t.offered is not None and t.offered[1] == 0 and not (t.done and t.integrity):
                self.late_errors.add(tag)

    def reset(self, *, destructive: bool) -> None:
        require(destructive and all(t.done for t in self.transactions.values()), "reset before host settlement")
        self.epoch += 1
        self.faulted = False
        self.transactions.clear()
        self.memory.clear()
        self.serial.clear()

    def finish(self) -> None:
        require(all(t.done for t in self.transactions.values()), "unsettled host transaction at trace end")
        require(
            self.faulted or all(t.integrity or t.error for t in self.transactions.values()),
            "unsettled posted integrity at trace end",
        )
