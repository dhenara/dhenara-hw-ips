"""Raw parity and physical inline-data/check witnesses for RTL adapters."""

from contracts.integrity import WireBeat, check_transport, inline_decode, inline_encode

from system.host import require


class LinkCapture:
    def __init__(self, rows: dict, tag: int, beats: int, *, write: bool, dbi: bool = False, masked: bool = False):
        require(beats in (16, 32), "link capture length")
        self.rows, self.tag, self.beats = rows, tag, beats
        self.write, self.dbi, self.masked = write, dbi, masked
        self.wire: dict[tuple[int, int], WireBeat] = {}

    def beat(self, tag: int, beat: int, lane: int, dq: int, dmi: int, parity: int) -> None:
        require(tag == self.tag and 0 <= beat < self.beats and lane in (0, 1), "link identity")
        require((beat, lane) not in self.wire, "duplicate raw parity beat")
        self.wire[beat, lane] = WireBeat(tag, beat, lane, dq, dmi, parity)

    def finish(self) -> tuple[bytes, list[str]]:
        require(len(self.wire) == self.beats * 2, "missing raw parity/split group")
        return check_transport(self.rows, list(self.wire.values()), write=self.write, dbi=self.dbi, masked=self.masked)


class InlineWitness:
    """Compare physical check bytes against explicit SECDED72 layout.

    Request/lock/version identities are independent testbench bookkeeping. Both
    physical outcomes are mandatory before a success. A partial failed pair
    poisons the word; a stale correction can never overwrite a newer host write.
    """

    parity_positions = {1, 2, 4, 8, 16, 32, 64}

    def __init__(self):
        self.words: dict[int, tuple[int, int]] = {}
        self.versions: dict[int, int] = {}
        self.pending: dict[int, tuple[int, int, int]] = {}
        self.locked: set[int] = set()
        self.outcomes: dict[int, dict[str, bool]] = {}
        self.poison: set[int] = set()
        self.scrubs: dict[int, tuple[int, int]] = {}

    @classmethod
    def codeword(cls, data: int, check: int) -> int:
        require(0 <= data < 1 << 64 and 0 <= check < 256, "inline physical width")
        value, ordinal = 0, 0
        for pos in range(1, 72):
            if pos in cls.parity_positions:
                value |= (check >> (pos.bit_length() - 1) & 1) << (pos - 1)
            else:
                value |= (data >> ordinal & 1) << (pos - 1)
                ordinal += 1
        return value | (check >> 7) << 71

    @classmethod
    def check_byte(cls, data: int) -> int:
        code = inline_encode(data)
        return sum((code >> ((1 << i) - 1) & 1) << i for i in range(7)) | (code >> 71) << 7

    def read(self, word: int) -> tuple[int, str]:
        require(word not in self.locked and word not in self.poison, "inline locked/poisoned read")
        data, check = self.words.get(word, (0, 0))
        corrected, status = inline_decode(self.codeword(data, check))
        if status == "corrected":
            self.scrubs[word] = self.versions.get(word, 0), corrected
        return corrected, status

    def begin(self, tag: int, word: int, value: int, mask: int) -> None:
        require(tag not in self.pending and word not in self.locked and 0 <= mask < 256, "inline RMW lock/identity")
        if mask != 255:
            old, status = self.read(word)
            require(status != "uncorrectable", "uncorrectable partial RMW")
            value = sum(((value if mask >> i & 1 else old) >> (i * 8) & 255) << (i * 8) for i in range(8))
        self.pending[tag] = word, value, self.check_byte(value)
        self.locked.add(word)
        self.outcomes[tag] = {}

    def physical(self, tag: int, kind: str, value: int, ok: bool) -> None:
        require(
            tag in self.pending and kind in ("data", "check") and kind not in self.outcomes[tag],
            "inline physical identity/duplicate",
        )
        word, data, check = self.pending[tag]
        require(not ok or value == (data if kind == "data" else check), "wrong physical inline data/check")
        self.outcomes[tag][kind] = ok
        if ok:
            old = self.words.get(word, (0, 0))
            self.words[word] = (data, old[1]) if kind == "data" else (old[0], check)

    def settle(self, tag: int, success: bool) -> None:
        require(tag in self.pending and len(self.outcomes[tag]) == 2, "inline success before both physical outcomes")
        word, _, _ = self.pending.pop(tag)
        actual = all(self.outcomes.pop(tag).values())
        require(success == actual, "inline success after partial physical failure")
        self.locked.remove(word)
        self.versions[word] = self.versions.get(word, 0) + 1
        if actual:
            self.poison.discard(word)
        else:
            self.poison.add(word)

    def scrub(self, word: int) -> bool:
        require(word in self.scrubs, "unsolicited correction scrub")
        version, value = self.scrubs[word]
        if word in self.locked:
            return False
        del self.scrubs[word]
        if version != self.versions.get(word, 0):
            return False
        self.words[word] = value, self.check_byte(value)
        return True

    def finish(self) -> None:
        require(not self.pending and not self.locked and not self.scrubs, "dirty ECC state at drain")
