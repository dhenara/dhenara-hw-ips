"""Pin-command memory model, independent of controller queues/completions.

CA is decoded from sampled 14-bit values (JESD209-5C Table 201). The
restricted adjacent-pair policy is stronger than JEDEC's ACT maximum gap.
Memory is keyed by decoded rank/bank/row/byte; pin timing is half-WCK.
The supplied independent waveform oracle remains the WCK/path authority.
"""

from collections import deque
from dataclasses import dataclass

from contracts.lifecycle import Maintenance
from contracts.profiles import admit

from system.host import require


@dataclass(frozen=True)
class Command:
    kind: str
    bank: int = 0
    operand: int = 0
    beats: int = 0
    ap: bool = False
    all_banks: bool = False


def decode(ca: int) -> Command:
    require(type(ca) is int and 0 <= ca < 16384, "CA width")
    rise, fall = ca & 127, ca >> 7
    if rise & 7 == 7:
        return Command("ACT1", fall & 15, ((rise >> 3) << 14) | ((fall >> 4) << 11))
    if rise & 7 == 3:
        return Command("ACT2", operand=((rise >> 3) << 7) | fall)
    if rise & 15 == 12:
        require(fall == 0 and rise >> 4 in (1, 2, 4, 7), "unsupported CAS operands")
        return Command("CAS", operand=rise >> 4)
    if rise & 7 in (1, 2, 4, 5, 6):
        op = rise & 7
        require(op != 4 or rise & 8 == 0, "WR32 C0 reserved")
        column = ((rise >> 4) << 3) | ((fall >> 4 & 3) << 1) | (rise >> 3 & 1)
        return Command(
            {1: "RD", 2: "MWR", 4: "WR", 5: "RD", 6: "WR"}[op],
            fall & 15,
            column,
            32 if op in (4, 5) else 16,
            bool(fall & 64),
        )
    if rise == 120:
        return Command("PRE", fall & 15, all_banks=bool(fall & 64))
    if rise == 56:
        require(fall & 48 == 0, "unsupported refresh extension")
        return Command("RFM" if fall & 8 else "REF", fall & 7, all_banks=bool(fall & 64))
    if rise == 88:
        return Command("MRW1", operand=fall)
    if rise & 63 == 8:
        return Command("MRW2", operand=(rise >> 6) << 7 | fall)
    if rise == 24:
        return Command("MRR", operand=fall)
    if rise in (0, 40, 104, 64):
        return Command({0: "NOP", 40: "SRX", 104: "SRE", 64: "PDE"}[rise])
    if rise & 63 == 48:
        return Command("MPC", operand=(rise >> 6) << 7 | fall)
    raise ValueError("unknown/unsupported CA opcode")


@dataclass
class Bank:
    row: int | None = None
    act: int = -100000
    ready: int = 0
    pre_after: int = 0


@dataclass(frozen=True)
class Burst:
    index: int
    rank: int
    bank: int
    row: int
    column: int
    command_ck: int
    write: bool
    masked: bool
    beats: int
    times: tuple[int, ...]


class DramModel:
    def __init__(self, profile: dict):
        admit(profile)
        self.p = profile
        self.ck = 2 * profile["ratio"]
        self.banks = [[Bank() for _ in range(16)] for _ in range(profile["ranks"])]
        self.memory: dict[tuple[int, int, int, int], int] = {}
        self.maintenance = Maintenance(
            ranks=profile["ranks"], mr27=profile["device"]["mr27"], mr57=profile["device"]["mr57"]
        )
        self.pending: tuple[int, int, Command] | None = None
        self.last_ck = -1
        self.act_history = [deque() for _ in range(profile["ranks"])]
        self.access_history: list[tuple[int, int, int, bool, bool]] = []
        self.bursts: list[Burst] = []
        self.seen: set[tuple[int, int]] = set()
        self.bus_slots: set[int] = set()
        self.mr = [{} for _ in range(profile["ranks"])]
        self.sync: tuple[int, int, int] | None = None

    def command(self, at: int, cs: int, ca: int) -> Burst | None:
        require(at > self.last_ck, "repeated/out-of-order CA sample")
        self.last_ck = at
        self.maintenance.advance(at)
        require(cs in (0, 1, 2), "CA rank select")
        if not cs:
            require(self.pending is None or at < self.pending[0] + 1, "missing reserved pair")
            return None
        rank = cs - 1
        require(rank < len(self.banks), "CA absent rank")
        c, d = decode(ca), dict(self.p["dram_ck"])
        for name, extra in self.maintenance.timing_derating().items():
            d[name] += extra
        if self.pending:
            start, old_rank, first = self.pending
            require(at == start + 1 and rank == old_rank, "pair adjacency/rank")
            require(c.kind == {"ACT1": "ACT2", "MRW1": "MRW2"}[first.kind], "reserved pair overwritten")
            self.pending = None
            if first.kind == "MRW1":
                self.mr[rank][first.operand] = c.operand
                return None
            bank = self.banks[rank][first.bank]
            require(bank.row is None and at >= bank.ready and at - bank.act >= d["trc"], "ACT bank recovery")
            history = self.act_history[rank]
            require(not history or at - history[-1] >= d["trrd"], "tRRD")
            while history and at - history[0] >= d["tfaw"]:
                history.popleft()
            require(len(history) < 4, "tFAW")
            history.append(at)
            self.maintenance.activate(rank, first.bank)
            bank.row, bank.act = first.operand | c.operand, at
            bank.pre_after = at + d["tras_min"]
            return None
        require(c.kind not in ("ACT2", "MRW2"), "orphan pair completion")
        if self.sync and self.sync[2] in (1, 2):
            start, target, kind = self.sync
            require(
                at == start + 1 and target == rank and c.kind in (("WR", "MWR") if kind == 1 else ("RD",)),
                "CAS/access adjacency/direction/rank",
            )
            self.sync = None
        if c.kind in ("ACT1", "MRW1"):
            self.pending = at, rank, c
        elif c.kind == "CAS":
            self.sync = at, rank, c.operand
        elif c.kind in ("PRE", "REF", "RFM"):
            indices = range(16) if c.all_banks else (c.bank,) if c.kind == "PRE" else (c.bank, c.bank + 8)
            for n in indices:
                b = self.banks[rank][n]
                require(at >= b.ready, "bank busy")
                require(at >= b.pre_after if c.kind == "PRE" else b.row is None, "PRE/REF recovery or open bank")
            for n in indices:
                b = self.banks[rank][n]
                b.row = None
                b.ready = (
                    at + d[{"PRE": "trp", "REF": "trfc", "RFM": "trfm"}[c.kind] + ("_ab" if c.all_banks else "_pb")]
                )
            if c.kind in ("REF", "RFM"):
                pair = None if c.all_banks else c.bank
                if c.kind == "REF":
                    self.maintenance.refresh(rank, pair=pair)
                else:
                    self.maintenance.rfm(rank, pair=pair)
        elif c.kind in ("RD", "WR", "MWR"):
            b = self.banks[rank][c.bank]
            require(b.row is not None and at >= b.ready and at - b.act >= d["trcd"], "access without ready open row")
            require(self.maintenance.service_allowed(), "urgent maintenance bypass")
            write = c.kind != "RD"
            # Intervening traffic to another bank cannot erase an older bank's
            # column/turnaround constraint. Keep the actual command history.
            for old_at, old_rank, old_bank, old_write, old_masked in self.access_history:
                if old_rank != rank:
                    continue
                same_group = self.p["ratio"] == 2 or old_bank // 4 == c.bank // 4
                gap = 8 if old_bank == c.bank else 4 if same_group else 2
                if old_masked or c.kind == "MWR":
                    gap = max(gap, 16 if old_bank == c.bank else 4 if same_group else 2)
                require(at - old_at >= gap, "column/physical mask spacing")
                if old_write and not write:
                    require(at - old_at >= d["twtr_l" if same_group else "twtr_s"], "write/read turnaround")
            if self.access_history and self.access_history[-1][1] != rank:
                require(not self.bus_slots or at * self.ck > max(self.bus_slots), "rank overlap")
            first = at * self.ck + self.p["wl_ck" if write else "rl_ck"] * self.ck
            first += self.p["phy_half_wck"]["dq_input" if write else "dq_output"]
            times = tuple(
                first + i + (16 if self.p["ratio"] == 4 and c.beats == 32 and i >= 16 else 0) for i in range(c.beats)
            )
            require(not self.bus_slots.intersection(times), "pin DQ collision")
            self.bus_slots.update(times)
            burst = Burst(len(self.bursts), rank, c.bank, b.row, c.operand, at, write, c.kind == "MWR", c.beats, times)
            self.bursts.append(burst)
            end_ck = (times[-1] + self.ck) // self.ck
            b.pre_after = max(b.pre_after, end_ck + (d["twr"] if write else d["trbtp"]))
            if c.ap:
                b.row, b.ready = None, b.pre_after + d["trp_pb"]
            self.access_history.append((at, rank, c.bank, write, c.kind == "MWR"))
            return burst
        elif c.kind == "SRE":
            require(all(b.row is None for b in self.banks[rank]), "SRE open bank")
            self.maintenance.hold_sr(rank)
        elif c.kind == "SRX":
            self.maintenance.exit_sr(rank)
        return None

    def beat(self, burst: Burst, beat: int, at: int, dq: int | None = None, dmi: int = 0) -> int:
        require(0 <= beat < burst.beats and at == burst.times[beat], "pin beat time/identity")
        require((burst.index, beat) not in self.seen, "duplicate pin beat")
        self.seen.add((burst.index, beat))
        # READ32 C0 selects the first 32B half. Beat sequence wraps within 64B.
        offset = (burst.column * 32) % (64 if burst.beats == 32 else 32) + beat * 2
        base = burst.column * 32 // (64 if burst.beats == 32 else 32) * (64 if burst.beats == 32 else 32)
        offset = base + offset % (burst.beats * 2)
        if burst.write:
            require(dq is not None and 0 <= dq < 65536 and 0 <= dmi < 4, "write pin data")
            for lane in range(2):
                value, mask = dq >> (8 * lane) & 255, dmi >> lane & 1
                if burst.masked and ((value >> 2).bit_count() >= 5 and not mask if self.p["write_dbi"] else mask):
                    continue
                if self.p["write_dbi"] and mask:
                    value ^= 255
                self.memory[(burst.rank, burst.bank, burst.row, offset + lane)] = value
        return sum(
            self.memory.get((burst.rank, burst.bank, burst.row, offset + lane), 0) << (8 * lane) for lane in range(2)
        )

    def finish(self) -> None:
        require(self.pending is None, "unfinished command pair")
        require(not self.sync or self.sync[2] not in (1, 2), "unfinished CAS pair")
        require(len(self.seen) == sum(b.beats for b in self.bursts), "missing pin data at trace end")
