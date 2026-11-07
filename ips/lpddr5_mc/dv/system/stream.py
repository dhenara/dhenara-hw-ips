"""Continuous WCK and DFI data-CS observation across reused synchronization.

An epoch begins from a qualified isolated preamble. Subsequent accesses share
that physical clock, rather than synthesizing another preamble per burst. All
half-WCK edges and every data-clock CS sample are observed, including holes.
"""

from contracts.waveform_oracle import SyncState

from system.dram import Burst
from system.host import require


class StreamMonitor:
    def __init__(self, profile: dict):
        self.p = profile
        self.ck = 2 * profile["ratio"]
        self.sync = SyncState(profile["ratio"], profile["always_on"])
        self.windows: list[tuple[int, int, int]] = []
        self.selected: int | None = None
        self.first: int | None = None
        self.fast: int | None = None
        self.disabled: int | None = None
        self.stop: int | None = None
        self.last_sample = -1
        self.last_data = -1
        self.last_cs_end = -1

    def begin(
        self, *, cas: int, enable: int, slow: int, fast: int, rank: int, direction: str, standalone: bool = False
    ) -> None:
        require(
            self.first is None and rank in range(self.p["ranks"]) and direction in ("read", "write"),
            "WCK epoch ownership",
        )
        if self.disabled is not None:
            require(cas >= self.disabled and cas >= self.last_cs_end, "fresh sync before disable/CS release")
        cycles = (
            2
            if standalone
            else (7 + int(self.p["read_link"]) * 2 + int(self.p["read_dbi"]))
            if direction == "read" and self.p["ratio"] == 4
            else (5 + int(self.p["read_dbi"]) * 2)
            if direction == "read"
            else 4
            if self.p["ratio"] == 4
            else 3
        )
        require(
            enable == cas + cycles * self.ck and slow == enable + 4 * self.ck and fast == slow + self.ck,
            "continuous preamble timing",
        )
        self.disabled = None
        self.selected, self.first, self.fast = cas, slow, fast
        self.first_command = cas // self.ck + (12 if standalone else 1)
        self.stop = None
        self.epoch_rank, self.direction = rank, direction
        self.new_sync = True
        self.last_data = -1

    def access(self, burst: Burst) -> None:
        require(
            self.first is not None
            and burst.rank == self.epoch_rank
            and ("write" if burst.write else "read") == self.direction,
            "reuse rank/direction without resync",
        )
        require(not self.new_sync or burst.command_ck == self.first_command, "first access CAS adjacency")
        latency = self.p["wl_ck" if burst.write else "rl_ck"]
        # SyncState checks expiry and always-on reuse. Different-BG scheduling
        # may be closer than its conservative same-BG spacing, so spacing is
        # owned by DramModel, which has the actual decoded bank history.
        self.sync.last = None
        self.sync.access(burst.command_ck, burst.rank, self.direction, burst.beats, latency, self.new_sync)
        self.new_sync = False
        require(self.fast <= burst.times[0], "data before full-rate WCK")
        dfi_origin = burst.command_ck * self.ck - self.p["phy_half_wck"]["command"]
        d = self.p["dfi_wck"]
        start = dfi_origin + 2 * d["tphy_wrcslat" if burst.write else "tphy_rdcslat"]
        span = burst.beats + (16 if self.p["ratio"] == 4 and burst.beats == 32 else 0)
        self.windows.append((start, start + span, burst.rank))
        self.last_cs_end = max(self.last_cs_end, start + span)
        self.last_data = max(self.last_data, burst.times[-1])

    def end(self, *, ws_off: int | None, stop: int, disabled: int) -> None:
        require(
            self.first is not None and stop >= self.last_data + 1 + 5 and disabled >= max(stop, self.last_cs_end),
            "continuous postamble/disable",
        )
        if self.p["always_on"]:
            require(
                ws_off is not None and ws_off >= self.last_data + 1 + 5 and stop >= ws_off + 5 * self.ck,
                "WS_OFF/stop recovery",
            )
        else:
            require(ws_off is None, "unexpected WS_OFF")
        self.stop, self.disabled = stop, disabled

    def sample(self, at: int, *, edge: bool, rank: int | None, data_cs: int | None = None) -> None:
        require(at == self.last_sample + 1, "missing continuous half-WCK sample")
        self.last_sample = at
        running = self.first is not None and at >= self.first and (self.stop is None or at < self.stop)
        expected = running and (at >= self.fast or (at - self.first) % 2 == 0)
        require(edge == expected, "missing/extra continuous WCK edge")
        require(
            rank
            == (
                self.epoch_rank
                if self.first is not None and at >= self.selected and (self.disabled is None or at < self.disabled)
                else None
            ),
            "WCK rank changed before disable",
        )
        if at % 2 == 0:
            ranks = {r for begin, end, r in self.windows if begin <= at < end}
            require(
                len(ranks) <= 1 and data_cs == (next(iter(ranks)) if ranks else None),
                "continuous CS hole/early release/wrong rank",
            )
        if self.disabled is not None and at >= self.disabled:
            self.first = None
            self.sync.rank, self.sync.direction, self.sync.last = None, None, None

    def finish(self) -> None:
        require(
            self.disabled is not None and self.last_sample >= max(self.disabled, self.last_cs_end),
            "continuous stream missing disable/CS tail",
        )
