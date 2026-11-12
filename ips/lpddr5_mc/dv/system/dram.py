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
        require(fall == 0, "unsupported power command operands")
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
    ap: bool = False


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
        self.recent: deque[Burst] = deque()
        self.last_access_rank: int | None = None
        self.bursts: list[Burst] = []
        self.seen: set[tuple[int, int]] = set()
        self.bus_slots: set[int] = set()
        self.mr = [{} for _ in range(profile["ranks"])]
        self.sync: tuple[int, int, int] | None = None
        self.rank_ready = [0] * profile["ranks"]
        # Unlike SR recovery, FSP/VRCG recovery permits DES only, including
        # no selected NOP. Origins are the rising edge carrying MRW2.
        self.des_until = [0] * profile["ranks"]
        self.sr_recovery = [False] * profile["ranks"]
        self.last_valid = [-100000] * profile["ranks"]
        self.power: list[tuple[str, int] | None] = [None] * profile["ranks"]
        self.last_pb = [-100000] * profile["ranks"]
        self.last_pre = [-100000] * profile["ranks"]
        self.last_mr: list[tuple[str, int] | None] = [None] * profile["ranks"]
        self.zq_start: int | None = None
        self.zq_ready = 0
        self.pdx_mrr = [0] * profile["ranks"]

    def command(self, at: int, cs: int, ca: int) -> Burst | None:
        require(at > self.last_ck, "repeated/out-of-order CA sample")
        self.last_ck = at
        self.maintenance.advance(at)
        # Timing/resource comparisons have finite horizons. Retained burst
        # identity and beat evidence remain complete, while continuous traffic
        # must not rescan every burst emitted since boot.
        horizon = (
            max(self.p["rl_ck"], self.p["wl_ck"])
            + 64
            + max(self.p["dram_ck"][k] for k in ("twtr_l", "twr", "trbtp", "tzqlat"))
        )
        while self.recent and self.recent[0].command_ck < at - horizon:
            self.recent.popleft()
        self.bus_slots = {slot for slot in self.bus_slots if slot >= at * self.ck}
        require(cs in (0, 1, 2), "CA rank select")
        if not cs:
            require(self.pending is None or at < self.pending[0] + 1, "missing reserved pair")
            return None
        rank = cs - 1
        require(rank < len(self.banks), "CA absent rank")
        c, d = decode(ca), dict(self.p["dram_ck"])
        for name, extra in self.maintenance.timing_derating().items():
            d[name] += extra
        require(at >= self.des_until[rank], "FSP/VRCG DES-only recovery")
        if c.kind == "NOP":
            require(self.pending is None, "NOP overwrote reserved pair")
            return None
        previous_valid = self.last_valid[rank]
        self.last_valid[rank] = at
        state = self.power[rank]
        if state:
            kind, entered = state
            require(c.kind == "SRX", "command while rank in self-refresh/power-down")
            require(kind == "SRE", "power-down exit requires sampled CS pulse")
            require((at - entered) * self.p["ck_ps"] >= 15000, "minimum self-refresh residence")
            self.power[rank] = None
            self.rank_ready[rank] = at + d["txsr"]
            self.sr_recovery[rank] = True
            if kind == "SRE":
                self.maintenance.exit_sr(rank)
            return None
        require(c.kind != "SRX", "exit without self-refresh/power-down")
        require(at >= self.zq_ready, "ZQ latch recovery")
        if not self.sr_recovery[rank] or c.kind not in ("MRW1", "MRW2", "MRR", "MPC", "CAS"):
            require(at >= self.rank_ready[rank], "rank recovery")
        old_mr = self.last_mr[rank]
        if old_mr and c.kind not in ("MRW2",):
            kind, origin = old_mr
            gap = d["tmrw"] if kind == "MRW" and c.kind == "MRW1" else d["tmrd"] if kind == "MRW" else d["tmrr"]
            if kind == "MRR" and c.kind in ("MRW1", "RD", "WR", "MWR"):
                dq_max = 1600 if self.p["ratio"] == 4 else 1900
                delay = (dq_max + self.p["ck_ps"] - 1) // self.p["ck_ps"]
                gap = self.p["rl_ck"] + (4 if self.p["ratio"] == 4 else 8) + 2
                if c.kind == "MRW1":
                    gap += delay
                elif c.kind in ("WR", "MWR"):
                    gap += delay - self.p["wl_ck"] + int(self.p["write_link"])
            require(at - origin >= gap, "MR command recovery")
        if c.kind in ("MRR", "MRW1"):
            if c.kind == "MRR":
                require(at >= self.pdx_mrr[rank], "PDX to MRR recovery")
            for old in self.recent:
                if old.rank == rank:
                    span = old.beats // 4
                    gap = (
                        self.p["wl_ck"] + span + d["twtr_l"]
                        if old.write and c.kind == "MRR"
                        else self.p["rl_ck"] + span + 2
                        if c.kind == "MRR"
                        else (self.p["wl_ck"] if old.write else self.p["rl_ck"] + 2) + span + 6
                    )
                    if c.kind == "MRW1" and old.ap:
                        gap += d["twr"] if old.write else d["trbtp"]
                    require(at - old.command_ck >= gap, "data to MR recovery")
        if self.pending:
            start, old_rank, first = self.pending
            require(at == start + 1 and rank == old_rank, "pair adjacency/rank")
            require(c.kind == {"ACT1": "ACT2", "MRW1": "MRW2"}[first.kind], "reserved pair overwritten")
            self.pending = None
            if first.kind == "MRW1":
                if first.operand == 16:
                    prior = self.mr[rank].get(16, 0)
                    op_changed = (prior >> 2 & 3) != (c.operand >> 2 & 3)
                    vrcg_changed = (prior ^ c.operand) & 64
                    require(c.operand & 3 != 3 and c.operand >> 2 & 3 != 3, "reserved FSP selection")
                    until = at
                    if op_changed:
                        require(c.operand & 64, "FSP-OP change without high-current VRCG")
                        # Figure 200 / Tables 256-257. CK remains running at
                        # 800 MHz, so entry and exit clock guards fit *within*
                        # tFC, rather than adding a second tFC/clock timer.
                        # The first valid command is Tc2, after the final
                        # DES sample at the clock-exit endpoint Tc1.
                        until = max(until, at + max(d["tfc"], d["tckfspe"] + d["tckfspx"] + 1))
                    if vrcg_changed:
                        # Figures 242/243 / Table 298: 150ns enable, 100ns
                        # disable, each from its own MRW2. Enable overlaps tFC
                        # when both bits change in that command.
                        ps = 150000 if c.operand & 64 else 100000
                        until = max(until, at + (ps + self.p["ck_ps"] - 1) // self.p["ck_ps"])
                    self.des_until[rank] = until
                    self.rank_ready[rank] = max(self.rank_ready[rank], until)
                self.mr[rank][first.operand] = c.operand
                self.last_mr[rank] = ("MRW", at)
                return None
            bank = self.banks[rank][first.bank]
            require(bank.row is None and start >= bank.ready and start - bank.act >= d["trc"], "ACT bank recovery")
            history = self.act_history[rank]
            require(not history or start - history[-1] >= d["trrd"], "tRRD")
            while history and start - history[0] >= d["tfaw"]:
                history.popleft()
            require(len(history) < 4, "tFAW")
            history.append(start)
            self.maintenance.activate(rank, first.bank)
            bank.row, bank.act = first.operand | c.operand, start
            bank.pre_after = start + d["tras_min"]
            return None
        require(c.kind not in ("ACT2", "MRW2"), "orphan pair completion")
        if self.sync and self.sync[2] in (1, 2):
            start, target, kind = self.sync
            # JESD209-5C Figure 195 (p340) explicitly uses CAS(WS_RD)
            # immediately before MRR, whose data/preamble follow a read.
            require(
                at == start + 1 and target == rank and c.kind in (("WR", "MWR") if kind == 1 else ("RD", "MRR")),
                "CAS/access adjacency/direction/rank",
            )
            self.sync = None
        if c.kind in ("ACT1", "MRW1"):
            if c.kind == "ACT1":
                require(at >= self.last_pb[rank] + d["tpbr2act"], "REFpb to ACT")
                require(at >= self.banks[rank][c.bank].ready, "ACT bank recovery")
            self.pending = at, rank, c
        elif c.kind == "CAS":
            self.sync = at, rank, c.operand
        elif c.kind in ("PRE", "REF", "RFM"):
            indices = range(16) if c.all_banks else (c.bank,) if c.kind == "PRE" else (c.bank, c.bank + 8)
            for n in indices:
                b = self.banks[rank][n]
                require(at >= b.ready, "bank busy")
                require(at >= b.pre_after if c.kind == "PRE" else b.row is None, "PRE/REF recovery or open bank")
            if c.kind == "PRE":
                require(at - self.last_pre[rank] >= d["tppd"], "PRE to PRE spacing")
                self.last_pre[rank] = at
            elif not c.all_banks:
                require(
                    not self.act_history[rank] or at - self.act_history[rank][-1] >= d["trrd"], "ACT to REFpb/RFMpb"
                )
                require(at - self.last_pb[rank] >= d["tpbr2pbr"], "REFpb/RFMpb spacing")
                self.last_pb[rank] = at
            for n in indices:
                b = self.banks[rank][n]
                b.row = None
                b.ready = (
                    at + d[{"PRE": "trp", "REF": "trfc", "RFM": "trfm"}[c.kind] + ("_ab" if c.all_banks else "_pb")]
                )
            if c.kind in ("REF", "RFM"):
                pair = None if c.all_banks else c.bank
                if c.kind == "REF":
                    self.maintenance.refresh(rank, pair=pair, extra=self.maintenance.extra[rank])
                else:
                    self.maintenance.rfm(rank, pair=pair)
        elif c.kind in ("RD", "WR", "MWR"):
            b = self.banks[rank][c.bank]
            require(b.row is not None and at >= b.ready and at - b.act >= d["trcd"], "access without ready open row")
            require(self.maintenance.service_allowed(), "urgent maintenance bypass")
            write = c.kind != "RD"
            # Intervening traffic to another bank cannot erase an older bank's
            # column/turnaround constraint. Keep the actual command history.
            for old in self.recent:
                old_at, old_rank, old_bank, old_write = (
                    old.command_ck,
                    old.rank,
                    old.bank,
                    old.write,
                )
                if old_rank != rank:
                    continue
                same_group = self.p["ratio"] == 2 or old_bank // 4 == c.bank // 4
                gap = old.beats // 4 if same_group else 2
                if old_write and c.kind == "MWR":
                    if old_bank == c.bank:
                        gap = max(gap, (4 if old.beats == 16 else 2.5) * (old.beats // 4))
                    elif same_group:
                        gap = max(gap, old.beats // 4)
                require(at - old_at >= gap, "column/physical mask spacing")
                if old_write and not write:
                    # tWTR is measured after the final physical write beat, not
                    # after its CA command. Include the entire split span.
                    span = old.beats // 4 if same_group else (6 if old.beats == 32 else 2)
                    end = old_at + self.p["wl_ck"] + span
                    require(at >= end + d["twtr_l" if same_group else "twtr_s"], "write/read turnaround")
                elif not old_write and write:
                    span = old.beats // 4 if same_group else (6 if old.beats == 32 else 2)
                    dq_max = 1600 if self.p["ratio"] == 4 else 1900
                    gap = self.p["rl_ck"] + span + (dq_max + self.p["ck_ps"] - 1) // self.p["ck_ps"] - self.p["wl_ck"]
                    gap += int(self.p["write_link"])
                    require(at - old_at >= gap, "read/write turnaround")
            if self.last_access_rank is not None and self.last_access_rank != rank:
                require(not self.bus_slots or at * self.ck > max(self.bus_slots), "rank overlap")
            first = at * self.ck + self.p["wl_ck" if write else "rl_ck"] * self.ck
            first += self.p["phy_half_wck"]["dq_input" if write else "dq_output"]
            times = tuple(
                first + i + (16 if self.p["ratio"] == 4 and c.beats == 32 and i >= 16 else 0) for i in range(c.beats)
            )
            require(not self.bus_slots.intersection(times), "pin DQ collision")
            for old in self.recent:
                if old.write != write or old.rank != rank:
                    require(times[0] > old.times[-1], "DQ turnaround/rank recovery")
            self.bus_slots.update(times)
            burst = Burst(
                len(self.bursts), rank, c.bank, b.row, c.operand, at, write, c.kind == "MWR", c.beats, times, c.ap
            )
            self.bursts.append(burst)
            span = c.beats // (2 * self.p["ratio"]) + (4 if self.p["ratio"] == 4 and c.beats == 32 else 0)
            pre = at + (self.p["wl_ck"] + span + 1 + d["twr"] if write else span + d["trbtp"])
            b.pre_after = max(b.pre_after, pre)
            if c.ap:
                b.row, b.ready = None, b.pre_after + d["trp_pb"]
            self.last_access_rank = rank
            self.recent.append(burst)
            return burst
        elif c.kind in ("SRE", "PDE"):
            if c.kind == "PDE":
                require(at - previous_valid >= 3, "valid command to PDE")
                if old_mr and old_mr[0] == "MRW":
                    require(
                        (at - old_mr[1]) * self.p["ck_ps"] >= max(14000, 6 * self.p["ck_ps"]),
                        "MRW to PDE",
                    )
            require(
                all(at * self.ck >= b.times[-1] + 6 for b in self.recent if b.rank == rank),
                "power entry during physical data/postamble",
            )
            require(all(at >= b.ready for b in self.banks[rank]), "power entry before bank recovery")
            require(all(at >= b.pre_after for b in self.banks[rank]), "power entry before data recovery")
            if c.kind == "SRE":
                require(all(b.row is None for b in self.banks[rank]), "SRE open bank")
                self.maintenance.hold_sr(rank)
            self.power[rank] = c.kind, at
        elif c.kind == "MRR":
            self.last_mr[rank] = "MRR", at
        elif c.kind == "MPC":
            # Only ZQ start/latch are admitted here. Training MPCs belong to
            # external PHY ownership and cannot silently pass normal traffic.
            if c.operand == 0x85:
                require(self.zq_start is None, "duplicate ZQ start")
                self.zq_start = at
            elif c.operand == 0x86:
                background = self.p["mr"]["28"] & 1 == 0
                require(
                    background or self.zq_start is not None and at - self.zq_start >= d["tzqcal"],
                    "ZQ calibration recovery",
                )
                require(not self.bus_slots or at * self.ck > max(self.bus_slots), "ZQ latch during DQ")
                self.zq_start = None
                self.zq_ready = at + d["tzqlat"]
            else:
                raise ValueError("unsupported MPC operation")
        else:
            require(c.kind in ("ACT1", "MRW1", "CAS"), "unsupported command")
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

    def power_exit(self, at: int, rank: int, *, cs_high: int, cs_low: int, ca_low: int) -> None:
        """Sampled PDX CS pulse; PDX is not an SRX CA command (Table 245).

        Times are CK rising-edge indices; integer conversion rounds physical
        minima up. Caller must observe continuous low CA during the pulse.
        """
        require(
            0 <= rank < len(self.banks) and self.power[rank] is not None and self.power[rank][0] == "PDE",
            "PDX without power-down",
        )
        entered = self.power[rank][1]
        ps = self.p["ck_ps"]
        require(cs_high >= entered and (cs_high - entered) * ps >= 10000 + ps, "PDE to CS high")
        require((cs_low - cs_high) * ps >= 3000 and (at - cs_low) * ps >= 4000, "PDX CS pulse")
        require((cs_high - ca_low) * ps >= 1750 and ca_low >= entered, "PDX CA low interval")
        require(at > self.last_ck, "PDX time reversal")
        self.maintenance.advance(at)
        self.last_ck = at
        self.power[rank] = None
        self.rank_ready[rank] = at + self.p["dram_ck"]["txp"]
        self.sr_recovery[rank] = False
        self.pdx_mrr[rank] = self.rank_ready[rank] + self.p["dram_ck"]["trcd"] + 2

    def logical_beat(self, burst: Burst, beat: int, at: int, dq: int, enables: int) -> None:
        """Already corrected/mask-decoded link bytes, before physical storage."""
        require(burst.write and 0 <= dq < 65536 and 0 <= enables < 4, "logical write width")
        require(0 <= beat < burst.beats and at == burst.times[beat], "pin beat time/identity")
        require((burst.index, beat) not in self.seen, "duplicate pin beat")
        self.seen.add((burst.index, beat))
        size = burst.beats * 2
        offset = burst.column * 32 // size * size + (burst.column * 32 + beat * 2) % size
        for lane in range(2):
            if enables >> lane & 1:
                self.memory[burst.rank, burst.bank, burst.row, offset + lane] = dq >> (8 * lane) & 255

    def finish(self) -> None:
        require(self.pending is None, "unfinished command pair")
        require(not self.sync or self.sync[2] not in (1, 2), "unfinished CAS pair")
        require(len(self.seen) == sum(b.beats for b in self.bursts), "missing pin data at trace end")
