"""Wire ECC and persistent inline-ECC transaction contracts.

Link matrices are JEDEC-owned reference data. Inline SECDED layout is this
controller's explicit choice: Hamming positions 1..71, parity at powers of two,
and overall even parity at position 72. Physical 8-bit checks are packed into
reserved 64-byte lines, one check byte per protected 8-byte word.
"""

from dataclasses import dataclass, field

from contracts.link_ecc import decode, encode, read_parity, write_parity


def dbi_encode(value: int) -> tuple[int, int]:
    if not 0 <= value < 256:
        raise ValueError("byte")
    invert = int(value.bit_count() > 4)
    return value ^ (255 if invert else 0), invert


def write_byte(value: int, enabled: bool, dbi: bool, masked_command: bool) -> tuple[int, int]:
    if not enabled:
        if not masked_command:
            raise ValueError("mask requires MWR")
        return (0xFC, 0) if dbi else (0, 1)
    return dbi_encode(value) if dbi else (value, 0)


def receive_byte(dq: int, dmi: int, dbi: bool, masked_command: bool) -> int | None:
    if not 0 <= dq < 256 or dmi not in (0, 1):
        raise ValueError("wire byte")
    if masked_command and ((not dbi and dmi) or (dbi and not dmi and (dq >> 2).bit_count() >= 5)):
        return None
    return dq ^ (255 if dbi and dmi else 0)


@dataclass(frozen=True)
class WireBeat:
    tag: int
    beat: int
    byte: int
    dq: int
    dmi: int
    parity: int


def transport(
    rows: dict, tag: int, data: bytes, enables: list[bool], *, write: bool, dbi: bool = False
) -> list[WireBeat]:
    if len(data) not in (32, 64) or len(enables) != len(data) or any(type(x) is not bool for x in enables):
        raise ValueError("burst")
    if not write and dbi:
        raise ValueError("read link excludes DBI")
    beats = len(data) // 2
    result = []
    masked = not all(enables)
    for group in range(beats // 16):
        for byte in range(2):
            wire = [
                write_byte(data[(group * 16 + i) * 2 + byte], enables[(group * 16 + i) * 2 + byte], dbi, masked)
                if write
                else (data[(group * 16 + i) * 2 + byte], 0)
                for i in range(16)
            ]
            payload = sum(dq << (8 * i) for i, (dq, _) in enumerate(wire))
            dm = sum(dmi << i for i, (_, dmi) in enumerate(wire))
            checks = encode(rows["data_rows"], payload)
            parity = write_parity(checks, encode(rows["dmi_rows"], dm)) if write else read_parity(checks)
            result.extend(WireBeat(tag, group * 16 + i, byte, dq, dmi, parity[i]) for i, (dq, dmi) in enumerate(wire))
    return sorted(result, key=lambda b: (b.beat, b.byte))


def check_transport(
    rows: dict, wire: list[WireBeat], *, write: bool, dbi: bool = False, masked: bool = False
) -> tuple[bytes, list[str]]:
    if len(wire) not in (32, 64):
        raise ValueError("wire length")
    beats = len(wire) // 2
    tags = {b.tag for b in wire}
    identities = {(b.beat, b.byte) for b in wire}
    if len(tags) != 1 or identities != {(i, j) for i in range(beats) for j in range(2)}:
        raise ValueError("split/byte identity")
    if not write and dbi:
        raise ValueError("read link DBI conflict")
    lookup = {(b.beat, b.byte): b for b in wire}
    output = bytearray(len(wire))
    statuses = []
    for group in range(beats // 16):
        for byte in range(2):
            block = [lookup[(group * 16 + i, byte)] for i in range(16)]
            if any(not 0 <= b.dq < 256 or b.dmi not in (0, 1) or b.parity not in (0, 1) for b in block):
                raise ValueError("pin range")
            if any(block[i].parity for i in ((0,) if write else range(7))):
                raise ValueError("reserved parity beat")
            payload = sum(b.dq << (8 * i) for i, b in enumerate(block))
            checks = sum(block[7 + i].parity << i for i in range(9))
            decoded = decode(rows["data_rows"], payload | (checks << 128))
            statuses.append(decoded.status)
            dm = sum(b.dmi << i for i, b in enumerate(block))
            if write:
                dm_checks = sum(block[1 + i].parity << i for i in range(6))
                dmi_decoded = decode(rows["dmi_rows"], dm | (dm_checks << 16))
                statuses.append(dmi_decoded.status)
                dm = dmi_decoded.payload
            if "uncorrectable" in statuses:
                raise ValueError("uncorrectable link block")
            for i in range(16):
                value = (
                    receive_byte(decoded.payload >> (8 * i) & 255, dm >> i & 1, dbi, masked)
                    if write
                    else decoded.payload >> (8 * i) & 255
                )
                output[(group * 16 + i) * 2 + byte] = 0 if value is None else value
    return bytes(output), statuses


def inline_encode(data: int) -> int:
    if not 0 <= data < 1 << 64:
        raise ValueError("inline data")
    word = 0
    bit = 0
    for pos in range(1, 72):
        if pos & (pos - 1):
            word |= (data >> bit & 1) << (pos - 1)
            bit += 1
    for i in range(7):
        parity = sum(word >> (pos - 1) & 1 for pos in range(1, 72) if pos >> i & 1) & 1
        word |= parity << ((1 << i) - 1)
    return word | ((word.bit_count() & 1) << 71)


def inline_decode(word: int) -> tuple[int, str]:
    if not 0 <= word < 1 << 72:
        raise ValueError("inline code")
    syndrome = 0
    for pos in range(1, 72):
        if word >> (pos - 1) & 1:
            syndrome ^= pos
    odd = word.bit_count() & 1
    status = "clean"
    if syndrome:
        if not odd or syndrome > 71:
            return 0, "uncorrectable"
        word ^= 1 << (syndrome - 1)
        status = "corrected"
    elif odd:
        status = "corrected"
    data = 0
    bit = 0
    for pos in range(1, 72):
        if pos & (pos - 1):
            data |= (word >> (pos - 1) & 1) << bit
            bit += 1
    return data, status


def inline_location(word: int, physical_bytes: int) -> tuple[int, int]:
    # Whole 9-line groups: eight 64-byte data lines plus one check line.
    groups = physical_bytes // 576
    data_bytes = groups * 512
    if word < 0 or word * 8 >= data_bytes:
        raise ValueError("reserved capacity access")
    return word * 8, data_bytes + word


@dataclass
class InlineStore:
    capacity: int = 4096
    cache_lines: int = 8
    words: dict[int, int] = field(default_factory=dict)
    versions: dict[int, int] = field(default_factory=dict)
    scrub: dict[int, tuple[int, int]] = field(default_factory=dict)
    dirty: set[int] = field(default_factory=set)
    pending: dict[int, tuple[int, int, int]] = field(default_factory=dict)
    locks: set[int] = field(default_factory=set)
    poisoned: set[int] = field(default_factory=set)

    def read(self, word: int) -> int:
        inline_location(word, self.capacity)
        if word in self.locks or word in self.poisoned:
            raise ValueError("locked/poisoned word")
        data, status = inline_decode(self.words.get(word, inline_encode(0)))
        if status == "uncorrectable":
            self.poisoned.add(word)
            raise ValueError("inline DBE")
        if status == "corrected":
            self.scrub[word] = (self.versions.get(word, 0), inline_encode(data))
        return data

    def begin(self, tag: int, word: int, value: int, mask: int) -> None:
        inline_location(word, self.capacity)
        if tag in self.pending or word in self.locks or not 0 <= mask < 256 or not 0 <= value < 1 << 64:
            raise ValueError("write reservation")
        if word in self.poisoned and mask != 255:
            raise ValueError("partial overwrite of poisoned word")
        # Cache-line reservation includes dirty/check lines; never evict dirty.
        line = word // 64
        if line not in self.dirty and len(self.dirty) >= self.cache_lines:
            raise ValueError("ECC cache pressure")
        old = self.read(word) if mask != 255 else 0
        merged = sum(((value if mask >> i & 1 else old) >> (8 * i) & 255) << (8 * i) for i in range(8))
        self.locks.add(word)
        self.dirty.add(line)
        self.pending[tag] = (word, merged, self.versions.get(word, 0) + 1)

    def commit(self, tag: int, data_ok: bool = True, check_ok: bool = True) -> str:
        word, value, version = self.pending.pop(tag)
        self.locks.remove(word)
        if data_ok and check_ok:
            self.words[word] = inline_encode(value)
            self.versions[word] = version
            self.poisoned.discard(word)
            response = "OKAY"
        else:
            # DRAM has no atomic data/check write. A partial physical failure
            # poisons the word until a full rewrite or destructive recovery.
            self.poisoned.add(word)
            response = "SLVERR"
        if not any(w // 64 == word // 64 for w, _, _ in self.pending.values()):
            self.dirty.discard(word // 64)
        return response

    def scrub_one(self, word: int) -> bool:
        if word in self.locks:
            return False
        version, code = self.scrub.pop(word)
        if self.versions.get(word, 0) != version or word in self.poisoned:
            return False
        self.words[word] = code
        return True

    def drain(self) -> bool:
        return not (self.pending or self.dirty or self.scrub or self.locks)

    def reset(self) -> None:
        if not self.drain():
            raise ValueError("destructive reset required; dirty state")


@dataclass
class WriteEpoch:
    """Rank-local link-write epoch, including AXI response backpressure.

    ``pin_end`` is the supplied physical-completion witness, in CK. Advancing
    time makes a posted response eligible only at that edge. ``responses`` is
    internal eligibility, ``offered`` is stable BVALID, and ``acknowledged`` is
    the actual B handshake. Neither admission nor MR43 settlement implies one.
    """

    posted: bool = False
    limit: int = 16
    tags: list[int] = field(default_factory=list)
    responses: dict[int, str] = field(default_factory=dict)
    offered: dict[int, str] = field(default_factory=dict)
    acknowledged: dict[int, str] = field(default_factory=dict)
    pin_ends: dict[int, int] = field(default_factory=dict)
    completed: set[int] = field(default_factory=set)
    now: int = 0
    fault: bool = False
    late_error: bool = False
    first_ck: int | None = None
    last_pin_end: int = 0
    poll_at: int | None = None

    def advance(self, at: int) -> None:
        if type(at) is not int or at < self.now:
            raise ValueError("write epoch time reversal")
        self.now = at
        if not self.fault:
            for tag, end in self.pin_ends.items():
                if at >= end and tag not in self.completed:
                    self.completed.add(tag)
                    if self.posted:
                        self.responses[tag] = "OKAY"

    def add(self, tag: int, at: int, pin_end: int) -> None:
        if (
            self.fault
            or tag in self.tags
            or tag in self.responses
            or tag in self.acknowledged
            or self.poll_at is not None
            or not 1 <= self.limit <= 16
            or len(self.tags) >= self.limit
        ):
            raise ValueError("write epoch admission")
        if self.first_ck is not None and at - self.first_ck > 256:
            raise ValueError("epoch poll deadline")
        if type(pin_end) is not int or pin_end < at:
            raise ValueError("write completion")
        self.advance(at)
        self.first_ck = at if self.first_ck is None else self.first_ck
        self.tags.append(tag)
        self.pin_ends[tag] = pin_end
        self.last_pin_end = max(self.last_pin_end, pin_end)
        self.advance(at)

    def offer(self, tag: int, at: int) -> str:
        """Assert BVALID; the value cannot change while BREADY is low."""
        self.advance(at)
        if tag not in self.responses:
            raise ValueError("write response not eligible/already acknowledged")
        self.offered.setdefault(tag, self.responses[tag])
        return self.offered[tag]

    def acknowledge(self, tag: int, at: int) -> str:
        """Complete BVALID/BREADY, optionally offering on the same edge."""
        value = self.offer(tag, at)
        self.acknowledged[tag] = value
        del self.offered[tag]
        del self.responses[tag]
        return value

    def poll(self, at: int) -> None:
        if (
            self.fault
            or self.poll_at is not None
            or not self.tags
            or at < self.last_pin_end
            or at - self.first_ck > 256
        ):
            raise ValueError("MR43 before drain/late")
        self.advance(at)
        self.poll_at = at

    def _outcome(self, tag: int, error: bool) -> str:
        # An exposed OKAY cannot be retracted, even if BREADY has not yet
        # arrived. An internal, unoffered response can still report SLVERR.
        exposed = self.acknowledged.get(tag, self.offered.get(tag))
        if exposed is not None:
            self.late_error |= error and exposed == "OKAY"
            return exposed
        value = "SLVERR" if error else "OKAY"
        self.responses[tag] = value
        return value

    def _close(self) -> None:
        self.tags.clear()
        self.pin_ends.clear()
        self.completed.clear()
        self.first_ck = None
        self.poll_at = None
        self.last_pin_end = 0

    def settle(self, at: int, mr43: int, tmrr: int, tmrw: int) -> dict[int, str]:
        if self.fault or tmrr < 1 or tmrw < 1 or self.poll_at is None or at < self.poll_at + tmrr + tmrw:
            raise ValueError("MR43 clear delay")
        if type(mr43) is not int or not 0 <= mr43 < 256:
            raise ValueError("MR43 value")
        self.advance(at)
        error = bool(mr43 & 128)
        result = {tag: self._outcome(tag, error) for tag in self.tags}
        self._close()
        return result

    def fail(self, at: int | None = None) -> dict[int, str]:
        """Fault the epoch; preserve exposed B, fail all unoffered responses.

        Omitting ``at`` faults at the current sampled time. No timer or late
        completion can reopen this instance; destructive recovery creates a
        new epoch owner after the old host responses have been settled.
        """
        self.advance(self.now if at is None else at)
        affected = set(self.tags) | self.responses.keys()
        result = {tag: self._outcome(tag, True) for tag in sorted(affected)}
        self.fault = True
        self._close()
        return result
