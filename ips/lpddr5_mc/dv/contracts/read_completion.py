"""Word/slice completion oracle for a future pin-order DFI transport fixture.

An enable timeline must be supplied independently. Each slice has its own
ordered expected stream. Finishing one slice or one BL32 half cannot retire
the request. Clock units here are DFI data-clock cycles (WCK), not CK.
"""

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ExpectedWord:
    enable: int
    tag: int
    word: int
    slice_index: int


class CompletionOracle:
    def __init__(self, expected: Sequence[ExpectedWord], words_per_tag: int, maximum_latency: int):
        if words_per_tag not in (1, 8, 16) or maximum_latency < 0:
            raise ValueError("unsupported completion configuration")
        self.maximum_latency = maximum_latency
        self.words_per_tag = words_per_tag
        self.pending: dict[int, deque[ExpectedWord]] = {0: deque(), 1: deque()}
        self.received: dict[int, dict[tuple[int, int], int]] = {}
        self.last_valid: dict[int, int] = {}
        identities: set[tuple[int, int, int]] = set()
        for item in expected:
            if item.slice_index not in (0, 1) or not 0 <= item.word < words_per_tag:
                raise ValueError("invalid word/slice")
            identity = (item.tag, item.word, item.slice_index)
            if identity in identities:
                raise ValueError("duplicate expected word")
            identities.add(identity)
            stream = self.pending[item.slice_index]
            if stream and stream[-1].enable >= item.enable:
                raise ValueError("enable stream must be strictly ordered per slice")
            stream.append(item)
            self.received.setdefault(item.tag, {})
        for tag in self.received:
            if sum(item[0] == tag for item in identities) != words_per_tag * 2:
                raise ValueError("incomplete expected request")

    def valid(self, at: int, slice_index: int, value: int) -> tuple[int, bool]:
        if slice_index not in self.pending or not 0 <= value < 65536:
            raise ValueError("invalid slice/data")
        if not self.pending[slice_index]:
            raise ValueError("unsolicited or duplicate valid")
        item = self.pending[slice_index][0]
        if not item.enable <= at <= item.enable + self.maximum_latency:
            raise ValueError("valid outside enable/deadline window")
        if at <= self.last_valid.get(slice_index, -1):
            raise ValueError("more than one word per slice per data cycle")
        self.pending[slice_index].popleft()
        self.last_valid[slice_index] = at
        self.received[item.tag][(item.word, slice_index)] = value
        return item.tag, len(self.received[item.tag]) == self.words_per_tag * 2

    def check_deadlines(self, at: int) -> None:
        if any(stream and at > stream[0].enable + self.maximum_latency for stream in self.pending.values()):
            raise ValueError("missing read valid")


class ReadBusOracle:
    """Check a CK snapshot against DFI 5.0 4.10.4 word rotation.

    Each list element is a word lane, containing two independent byte slices.
    A slice carries two memory beats (16 bits). Enable times and deadlines use
    WCK cycles; snapshot CK edges are converted explicitly. Expected streams
    describe physical transfer order, including both BL32 halves, independently
    of returned lane placement. DNV consumes a word but marks it unavailable.
    """

    def __init__(self, ratio: int, completion: CompletionOracle):
        if ratio not in (2, 4):
            raise ValueError("unsupported data word count")
        self.ratio = ratio
        self.completion = completion
        self.pointer = [0, 0]
        self.last_ck = -1
        self.unavailable: set[tuple[int, int, int]] = set()

    def snapshot(self, ck: int, lanes: Sequence[Sequence[int | None]], dnv: Sequence[Sequence[int]]) -> list[int]:
        if type(ck) is not int or ck <= self.last_ck or len(lanes) != self.ratio or len(dnv) != self.ratio:
            raise ValueError("invalid or repeated CK snapshot")
        if any(len(lane) != 2 for lane in (*lanes, *dnv)):
            raise ValueError("expected two byte slices")
        # Preflight the whole snapshot. A rejected trace must not consume a
        # prefix and make subsequent diagnostics depend on iteration order.
        actions = []
        counts = []
        for slice_index in range(2):
            order = [(self.pointer[slice_index] + i) % self.ratio for i in range(self.ratio)]
            asserted = [w for w in order if lanes[w][slice_index] is not None]
            if asserted != order[: len(asserted)]:
                raise ValueError("noncontiguous or wrong read word rotation")
            stream = self.completion.pending[slice_index]
            if len(asserted) > len(stream):
                raise ValueError("unsolicited or duplicate valid")
            if len(stream) > len(asserted):
                next_missing = stream[len(asserted)]
                if ck * self.ratio > next_missing.enable + self.completion.maximum_latency:
                    raise ValueError("missing read valid at deadline")
            for ordinal, word_lane in enumerate(asserted):
                item = stream[ordinal]
                value = lanes[word_lane][slice_index]
                if type(value) is not int or not 0 <= value < 65536:
                    raise ValueError("invalid slice data")
                if type(dnv[word_lane][slice_index]) is not int or not 0 <= dnv[word_lane][slice_index] <= 3:
                    raise ValueError("DNV must describe both bytes in this slice word")
                # All word lanes are sampled at the CK edge; they are not
                # observations from separate WCK edges inside this CK cycle.
                at = ck * self.ratio
                if not item.enable <= at <= item.enable + self.completion.maximum_latency:
                    raise ValueError("valid outside enable/deadline window")
                actions.append((slice_index, word_lane, item, value))
            counts.append(len(asserted))
        completed = []
        for slice_index, word_lane, item, value in actions:
            self.completion.pending[slice_index].popleft()
            received = self.completion.received[item.tag]
            received[(item.word, slice_index)] = value
            if dnv[word_lane][slice_index]:
                self.unavailable.add((item.tag, item.word, slice_index))
            if len(received) == self.completion.words_per_tag * 2:
                completed.append(item.tag)
        for slice_index, count in enumerate(counts):
            self.pointer[slice_index] = (self.pointer[slice_index] + count) % self.ratio
        self.last_ck = ck
        return completed

    def resynchronize(self, event: str) -> None:
        if event not in ("initialization", "dfi_init_start", "dfi_ctrlupd_req", "dfi_phyupd_ack"):
            raise ValueError("event does not establish read resynchronization")
        if any(self.completion.pending.values()):
            raise ValueError("drain or explicitly abort pending reads before resynchronization")
        self.pointer = [0, 0]
