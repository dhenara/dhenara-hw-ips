"""Generic digital PHY seams: sampled waveform checking and returned words.

No vendor training or electrical model is implied. Raw pin parity stays raw;
controller ECC is checked by the separate standard-matrix oracle. This model
uses decoded physical commands and pin memory, not queue completion signals.
"""

import heapq
from collections import deque
from dataclasses import dataclass
from types import SimpleNamespace

from contracts.integrity import WireBeat, dbi_encode, transport
from contracts.waveform_oracle import check, check_data_cs_sequence

from system.dram import Burst, DramModel
from system.host import require
from system.write_link import receive


class WaveCapture:
    """Reconstruct one isolated transfer from actual edge/phase observations.

    Time is relative half-WCK, DFI CAS origin zero. Continuous data-CS samples
    include every asserted data-clock slot, not only enabled words. These are
    isolated synchronization tests; long reused-WCK streams use command/resource
    checks separately and must not be forced into this isolated-transfer form.
    """

    scalar = {"cas", "command", "enable", "slow", "fast", "first", "ws_off", "stop", "disabled", "data_cs_end"}
    sequence = {"data", "edges", "dfi_enable", "dfi_data", "returned", "cs", "data_cs"}

    def __init__(self, ratio: int, direction: str, beats: int, rank: int):
        require(ratio in (2, 4) and direction in ("read", "write") and beats in (16, 32), "wave configuration")
        self.fields = {"ratio": ratio, "direction": direction, "beats": beats, "rank": rank}
        self.values = {name: [] for name in self.sequence}

    def sample(self, kind: str, at: int, rank: int | None = None) -> None:
        require(type(at) is int and at >= 0, "wave sample time")
        if kind in self.scalar:
            require(kind not in self.fields, "duplicate waveform edge")
            self.fields[kind] = at
        else:
            require(kind in self.sequence, "unknown waveform sample")
            self.values[kind].append((at, rank) if kind in ("cs", "data_cs") else at)

    def finish(self, profile: dict, *, fast_sync: bool = False) -> SimpleNamespace:
        require(self.scalar - {"ws_off"} <= self.fields.keys(), "incomplete waveform capture")
        wave = SimpleNamespace(**self.fields, **{k: tuple(v) for k, v in self.values.items()})
        if not hasattr(wave, "ws_off"):
            wave.ws_off = None
        check(profile, wave, fast_sync)
        return wave

    @staticmethod
    def ranks(profile: dict, observations: list[tuple[int, SimpleNamespace]]) -> None:
        check_data_cs_sequence(profile, observations)


@dataclass(frozen=True)
class ReturnedSlice:
    available_ck: int
    deadline_ck: int
    burst: int
    word: int
    slice: int
    value: int
    dnv: int = 0
    parity: int = 0
    dmi: int = 0


class DigitalPhy:
    def __init__(self, profile: dict, link_rows: dict | None = None):
        self.dram = DramModel(profile)
        self.p = profile
        require(
            not (profile["read_link"] or profile["write_link"]) or link_rows is not None,
            "read-link PHY needs the raw parity matrix",
        )
        self.link_rows = link_rows
        self.pending: list[tuple[int, int, ReturnedSlice]] = []
        self.ready = [deque(), deque()]
        self.pointer = [0, 0]
        self.serial = 0
        self.last_return = -1
        self.raw_writes: dict[int, dict[int, WireBeat]] = {}
        self.write_status: dict[int, list[str]] = {}

    def command(self, dfi_ck: int, cs: int, ca: int) -> Burst | None:
        # Each CA sample is delayed into the DRAM clock domain by explicit CK.
        return self.dram.command(dfi_ck + self.p["management_ck"]["tctrl_delay"], cs, ca)

    def write_word(self, b: Burst, word: int, at_half_wck: int, value: int, mask: int = 0, parity: int = 0) -> None:
        require(
            b.write and 0 <= word < b.beats // 2 and 0 <= value < 1 << 32 and 0 <= mask < 16,
            "write word identity/width",
        )
        first = at_half_wck + self.p["phy_half_wck"]["write"]
        require(first == b.times[word * 2], "write serialization path")
        require(0 <= parity < 16, "raw write parity width")
        if self.p["write_link"]:
            require(self.p["ratio"] == 4, "write link ratio")
            capture = self.raw_writes.setdefault(b.index, {})
            require(word * 4 not in capture, "duplicate raw write word")
            for half in range(2):
                for lane in range(2):
                    i = half * 2 + lane
                    capture[word * 4 + i] = WireBeat(
                        b.index, word * 2 + half, lane, value >> (8 * i) & 255, mask >> i & 1, parity >> i & 1
                    )
            if len(capture) == b.beats * 2:
                data, enabled, status = receive(
                    self.link_rows, list(capture.values()), dbi=self.p["write_dbi"], masked=b.masked
                )
                # Correction and mask interpretation precede physical memory.
                # Pass logical bytes to the memory model without a second DBI
                # transform. No partial effect survives an uncorrectable block.
                for beat in range(b.beats):
                    self.dram.logical_beat(
                        b,
                        beat,
                        b.times[beat],
                        int.from_bytes(data[beat * 2 : beat * 2 + 2], "little"),
                        enabled >> (beat * 2) & 3,
                    )
                self.write_status[b.index] = status
                del self.raw_writes[b.index]
        else:
            require(parity == 0, "parity without admitted write link")
            for half in range(2):
                self.dram.beat(b, word * 2 + half, first + half, value >> (half * 16) & 65535, mask >> (half * 2) & 3)

    def schedule_read(
        self, b: Burst, *, skew_ck: tuple[int, int] = (0, 0), gaps: int = 0, dnv: set[tuple[int, int]] | None = None
    ) -> None:
        require(not b.write and min(skew_ck) >= 0 and gaps >= 0, "read return controls")
        r, ck = self.p["ratio"], self.dram.ck
        beats = [self.dram.beat(b, i, b.times[i]) for i in range(b.beats)]
        payload = b"".join(x.to_bytes(2, "little") for x in beats)
        wire = None
        if self.p["read_link"]:
            wire = transport(self.link_rows, b.index, payload, [True] * len(payload), write=False)
        for word in range(b.beats // 2):
            for slice_index in range(2):
                value, parity, dmi = 0, 0, 0
                for half in range(2):
                    byte = payload[(word * 2 + half) * 2 + slice_index]
                    encoded, invert = dbi_encode(byte) if self.p["read_dbi"] else (byte, 0)
                    value |= encoded << (half * 8)
                    dmi |= invert << half
                    if wire is not None:
                        parity |= wire[(word * 2 + half) * 2 + slice_index].parity << half
                at = (b.times[word * 2 + 1] + self.p["phy_half_wck"]["read"] + ck - 1) // ck
                at += skew_ck[slice_index] + (word // r) * gaps
                dfi_command = b.command_ck - self.p["management_ck"]["tctrl_delay"]
                offset = 2 * word + (16 if r == 4 and b.beats == 32 and word >= 8 else 0)
                enable = dfi_command * ck + 2 * self.p["dfi_wck"]["trddata_en"] + offset
                deadline = (enable + 2 * self.p["dfi_wck"]["tphy_rdlat"]) // ck
                require(at <= deadline, "configured read skew/gaps exceed PHY deadline")
                item = ReturnedSlice(
                    at,
                    deadline,
                    b.index,
                    word,
                    slice_index,
                    value,
                    3 if dnv and (word, slice_index) in dnv else 0,
                    parity,
                    dmi,
                )
                self.serial += 1
                heapq.heappush(self.pending, (at, self.serial, item))

    def returned(self, ck: int, quotas: tuple[int, int] | None = None) -> list[list[ReturnedSlice | None]]:
        r = self.p["ratio"]
        require(ck > self.last_return, "repeated PHY return edge")
        self.last_return = ck
        while self.pending and self.pending[0][0] <= ck:
            _, _, item = heapq.heappop(self.pending)
            self.ready[item.slice].append(item)
        result: list[list[ReturnedSlice | None]] = [[None, None] for _ in range(r)]
        for slice_index in range(2):
            quota = r if quotas is None else quotas[slice_index]
            require(0 <= quota <= r, "return quota")
            for _ in range(min(quota, len(self.ready[slice_index]))):
                item = self.ready[slice_index].popleft()
                require(ck <= item.deadline_ck, "missing PHY return at deadline")
                lane = self.pointer[slice_index]
                result[lane][slice_index] = item
                self.pointer[slice_index] = (lane + 1) % r
            require(
                not self.ready[slice_index] or ck < self.ready[slice_index][0].deadline_ck,
                "delayed PHY return misses deadline",
            )
        return result

    def reset_pointer(self, *, fatal_abort: bool = False) -> None:
        require(fatal_abort or not self.pending and not any(self.ready), "pointer reset loses live words")
        self.pending.clear()
        for queue in self.ready:
            queue.clear()
        self.pointer = [0, 0]

    def finish(self) -> None:
        require(not self.raw_writes, "incomplete raw parity write")
        require(not self.pending and not any(self.ready), "unreturned words at trace end")
        self.dram.finish()
