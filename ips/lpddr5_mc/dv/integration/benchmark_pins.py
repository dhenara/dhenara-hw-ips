"""Bounded pin/DFI replay for the plain, single-direction benchmark binding.

CA timing and physical bytes use the independent DRAM model. Every scheduled
word, CS slot and raw return must actually appear; AXI completions cannot create
physical evidence. The observer's WCK controls are expanded to half-WCK edges
using the qualified digital path, rather than synthesizing edges from DQ.
"""

import copy
import heapq
from collections import Counter

from system.dram import decode
from system.host import require
from system.phy import DigitalPhy
from system.return_timing import ReturnTiming


def number(value):
    return int(value, 0) if isinstance(value, str) else value


class PinChannel:
    def __init__(self, profile, channel, owners):
        self.p = copy.deepcopy(profile)
        self.p["ranks"] = 1
        self.p["device"]["mr57"] = 3
        require(
            self.p["ratio"] == 4
            and self.p["always_on"]
            and not any(self.p[k] for k in ("read_link", "write_link", "read_dbi", "write_dbi")),
            "plain ratio-four always-on pin binding",
        )
        self.model = DigitalPhy(self.p)
        self.timing = ReturnTiming(self.p)
        self.reads = [[], []]
        self.serial = 0
        self.read_pointer = [0, 0]
        self.channel, self.owners = channel, owners
        self.bus, self.actions = {}, {}
        self.last_ck, self.origin = -1, None
        self.epoch = None
        self.last_pin = -1
        self.last_cs = -1
        self.counts = Counter()
        self.half_edges = 0

    def slot(self, word):
        ck, lane = divmod(word, 4)
        require(ck > self.last_ck, "scheduled DFI output in the past")
        return self.bus.setdefault(ck, {"write": 0, "read": 0}), lane

    def command_words(self, ck, payload, write, rank, burst=None, owner=None):
        d = self.p["dfi_wck"]
        words = len(payload) // 4
        for offset in range(words + (8 if words == 16 else 0)):
            at = ck * 4 + d["tphy_wrcslat" if write else "tphy_rdcslat"] + offset
            output, lane = self.slot(at)
            if write:
                output["write"] |= (1 << rank) << (16 + lane * 2)
            else:
                output["read"] |= ((1 << rank) * 5) << (lane * 4)
            self.last_cs = max(self.last_cs, 2 * (at + 1))
        for word in range(words):
            enable = ck * 4 + d["tphy_wrlat" if write else "trddata_en"] + word
            if words == 16 and word >= 8:
                enable += 8
            output, lane = self.slot(enable)
            output["write" if write else "read"] |= 3 << ((24 if write else 16) + lane * 2)
            value = int.from_bytes(payload[word * 4 : word * 4 + 4], "little")
            if write:
                at = enable + d["tphy_wrdata"]
                output, lane = self.slot(at)
                output["write"] |= value << (48 + lane * 32)
                self.actions.setdefault(at // 4, []).append((burst, owner, word, lane, at))
            else:
                for byte_slice in range(2):
                    value16 = ((value >> (byte_slice * 8)) & 255) | ((value >> (16 + byte_slice * 8) & 255) << 8)
                    self.serial += 1
                    heapq.heappush(self.reads[byte_slice], (enable, self.serial, value16, owner))

    def returned(self, event):
        raw = number(event["return"])
        valid = raw >> 32 & 255
        self.timing.sample(event["ck"], event["cs"], event["ca"], valid)
        require(raw & 0xFFFFFFFF == 0, "benchmark returned parity/DNV")
        active_data = 0
        for byte_slice in range(2):
            for ordinal in range(4):
                lane = (self.read_pointer[byte_slice] + ordinal) % 4
                if not valid >> (lane * 2 + byte_slice) & 1:
                    continue
                require(bool(self.reads[byte_slice]), "unsolicited benchmark returned word")
                _, _, expected, owner = heapq.heappop(self.reads[byte_slice])
                actual = 0
                for half in range(2):
                    shift = 40 + lane * 32 + half * 16 + byte_slice * 8
                    actual |= (raw >> shift & 255) << (half * 8)
                    active_data |= 255 << shift
                require(actual == expected, "physical read data/word/slice identity")
                if owner is not None:
                    owner["raw_slices"] += 1
                    owner["pin_end_ps"] = max(owner["pin_end_ps"], event["ps"])
            self.read_pointer[byte_slice] = (self.read_pointer[byte_slice] + (valid >> byte_slice & 85).bit_count()) % 4
        require(raw >> 40 & ~(active_data >> 40) == 0, "inactive returned DQ")

    def wck(self, event, operation):
        ck = event["ck"]
        if self.epoch is not None and ck * 4 >= self.epoch["disable"]:
            self.epoch = None
        if operation and operation.kind == "CAS":
            mode = operation.operand
            if mode in (1, 2):
                require(self.epoch is None, "fresh WCK sync before full disable")
                direction = "write" if mode == 1 else "read"
                # Table 204/205: 4CK write or 7CK plain read, static4CK,
                # half-rate1CK. Rank selection is registered one CK after CAS.
                enable = ck * 4 + (16 if mode == 1 else 28)
                self.epoch = {
                    "rank": event["cs"] - 1,
                    "direction": direction,
                    "selected": (ck + 1) * 4,
                    "enable": enable,
                    "slow": enable + 16,
                    "fast": enable + 20,
                    "disable": float("inf"),
                }
                self.counts["wck_sync"] += 1
            elif mode == 7:
                require(
                    self.epoch is not None and self.epoch["disable"] == float("inf"),
                    "WS_OFF without running WCK",
                )
                at_pin = ck * 8 + self.p["phy_half_wck"]["command"]
                require(at_pin >= self.last_pin + 6, "WS_OFF before final data/postamble")
                self.epoch["disable"] = ck * 4 + 20
                require(
                    self.epoch["disable"] * 2 >= self.last_cs,
                    "WCK disable before data-CS tail",
                )
                self.counts["ws_off"] += 1
            else:
                raise ValueError("standalone synchronization is outside this benchmark binding")
        raw, expected = number(event["wck"]), 0
        for lane in range(4):
            at = ck * 4 + lane
            epoch = self.epoch
            if epoch is None or not epoch["selected"] <= at < epoch["disable"]:
                continue
            toggle = 0 if at < epoch["slow"] else 2 if at < epoch["fast"] else 3
            for byte_slice in range(2):
                expected |= (1 << epoch["rank"]) << (16 + lane * 4 + byte_slice * 2)
                expected |= int(at >= epoch["enable"]) << (32 + lane * 2 + byte_slice)
                expected |= toggle << (lane * 4 + byte_slice * 2)
                self.half_edges += toggle.bit_count()
        require(
            raw == expected,
            f"actual WCK controls/half-edges channel{self.channel} CK{ck}",
        )

    def sample(self, event):
        ck, ps = event["ck"], event["ps"]
        require(ck == self.last_ck + 1, "missing/repeated PHY CK")
        if self.origin is None:
            self.origin = ps - ck * 1250
        require(ps == self.origin + ck * 1250, "actual CK clock")
        operation = decode(event["ca"]) if event["cs"] else None
        self.wck(event, operation)
        burst = self.model.command(ck, event["cs"], event["ca"])
        if operation:
            self.counts[operation.kind] += 1
        if burst is not None:
            require(
                burst.beats == 32 and not burst.masked and burst.column % 2 == 0,
                "benchmark physical burst",
            )
            physical = (burst.rank << 30) | (burst.row << 15) | (burst.bank << 11) | (burst.column << 5)
            logical = (physical & 63) | (self.channel << 6) | ((physical & ~63) << 1)
            require(logical in self.owners, "CA has no accepted AXI owner")
            owner = self.owners[logical]
            require(
                not owner["issued"] and owner["write"] == burst.write,
                "duplicate/wrong-direction physical issue",
            )
            require(
                self.epoch is not None
                and self.epoch["rank"] == burst.rank
                and self.epoch["direction"] == ("write" if burst.write else "read"),
                "WCK reuse direction/rank",
            )
            require(
                self.epoch["fast"] * 2 + self.p["phy_half_wck"]["wck"] <= burst.times[0],
                "data before full-rate WCK",
            )
            payload = owner["payload"]
            if burst.write:
                require(
                    len(owner["written"]) == 64 and owner["written"] == payload,
                    "pin write before complete validated W",
                )
            else:
                # Explicit fixture initial contents, keyed by independently
                # decoded physical address; no DUT read/completion supplies it.
                for i, value in enumerate(payload):
                    self.model.dram.memory[burst.rank, burst.bank, burst.row, burst.column * 32 + i] = value
                observed = b"".join(
                    self.model.dram.beat(burst, beat, burst.times[beat]).to_bytes(2, "little") for beat in range(32)
                )
                require(observed == payload, "physical seeded read bytes")
            owner["issued"] = True
            end = burst.times[-1] + 1
            if burst.write:
                d = self.p["dfi_wck"]
                flush = 2 * (ck * 4 + d["tphy_wrlat"] + d["tphy_wrdata"] + d["twrdata_delay"] + 24)
                end = max(end, flush)
            owner["pin_end_ps"] = self.origin + end * 1250 / 8
            self.last_pin = max(self.last_pin, burst.times[-1])
            self.command_words(ck, payload, burst.write, burst.rank, burst, owner)
        elif operation and operation.kind == "MRR":
            require(operation.operand == 4, "unexpected benchmark MRR")
            require(
                self.epoch is not None and self.epoch["direction"] == "read",
                "MRR without read WCK",
            )
            payload = bytes([9, 218, 9, 218]) * 4 + bytes([218]) * 16
            self.command_words(ck, payload, False, event["cs"] - 1)
            last = (ck + 2 + self.p["rl_ck"]) * 8 + self.p["phy_half_wck"]["dq_output"] + 15
            self.last_pin = max(self.last_pin, last)
        expected = self.bus.pop(ck, {"write": 0, "read": 0})
        for field, raw in expected.items():
            require(
                number(event[field]) == raw,
                f"actual {field} word/CS/parity channel{self.channel} CK{ck}",
            )
        self.returned(event)
        for burst, owner, word, lane, at in self.actions.pop(ck, []):
            if owner is None:
                continue
            owner["raw_words"] += 1
            if burst.write:
                value = number(event["write"]) >> (48 + lane * 32) & 0xFFFFFFFF
                self.model.write_word(burst, word, 2 * at, value)
            else:
                owner["pin_end_ps"] = max(owner["pin_end_ps"], ps)
        self.last_ck = ck

    def finish(self):
        require(
            not self.bus and not self.actions and not any(self.reads) and self.epoch is None,
            "unsettled physical/WCK trace tail",
        )
        timing = self.timing.finish()
        self.model.finish()
        return {
            "commands": dict(self.counts),
            "wck_half_edges_both_slices": self.half_edges,
            "last_ck": self.last_ck,
            "physical_return_timing": timing,
        }
