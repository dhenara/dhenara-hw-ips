"""DRAM-side checks transcribed independently from JESD209-5C.

No dependency on the DFI event generator. Integer time is half a WCK. This
oracle qualifies the declared digital fixtures, not electrical timing/training.
"""

from dataclasses import dataclass
from fractions import Fraction

# Tables 204/205/208/209: ratio, RL, WL, EN_RD, EN_WR, EN_FS, static.
ROWS = {(4, False): (4, 17, 9, 7, 4, 2, 4), (4, True): (4, 19, 9, 9, 4, 2, 4), (2, False): (2, 18, 10, 5, 3, 2, 4)}


def check(p: dict, e, fast_sync: bool = False) -> None:
    r, rl, wl, rd, wr, fs, static = ROWS[(p["ratio"], p["read_link"])]
    if p["read_dbi"]:
        rl += 1 if r == 4 else 2
        rd += 1 if r == 4 else 2
    ck = 2 * r
    read = e.direction == "read"
    latency = rl if read else wl
    if e.ratio != r or e.beats not in (16, 32) or not 0 <= e.rank < p["ranks"]:
        raise ValueError("pin configuration")
    if e.cas != p["phy_half_wck"]["command"]:
        raise ValueError("command path")
    if e.command - e.cas != (12 if fast_sync else 1) * ck:
        raise ValueError("CAS adjacency/standalone timing")
    if e.enable != e.cas + (fs if fast_sync else rd if read else wr) * ck:
        raise ValueError("WCK enable")
    if e.slow != e.enable + static * ck or e.fast != e.slow + ck:
        raise ValueError("static/half-rate preamble")
    offset = p["phy_half_wck"]["dq_output" if read else "dq_input"]
    picoseconds = Fraction(offset * p["ck_ps"], ck)
    bounds = (650, 1600 if r == 4 else 1900) if read else (300, 700 if r == 4 else 900)
    if not bounds[0] <= picoseconds <= bounds[1]:
        raise ValueError("tWCK2DQ outside Table 462")
    if e.first != e.command + latency * ck + offset or e.fast > e.first - offset:
        raise ValueError("DQ latency/synchronization")
    expected = tuple(
        e.command + latency * ck + offset + b + (16 if r == 4 and e.beats == 32 and b >= 16 else 0)
        for b in range(e.beats)
    )
    if e.data != expected:
        raise ValueError("pin beat identity/split")
    if e.stop < e.data[-1] + 1 + 5 or e.disabled < e.stop:
        raise ValueError("postamble/disable")
    if p["always_on"]:
        if e.ws_off is None or e.ws_off < e.data[-1] + 1 + 5 or e.stop < e.ws_off + 5 * ck:
            raise ValueError("always-on WS_OFF/tWCKSTOP")
    elif e.ws_off is not None:
        raise ValueError("unexpected WS_OFF fixture")
    if e.cs != ((e.cas, e.rank), (e.disabled, e.rank)):
        raise ValueError("WCK CS changes before complete disable")
    if not e.edges or e.edges[0] != e.slow or e.edges[-1] != e.stop - 1:
        raise ValueError("WCK missing endpoint")
    for a, b in zip(e.edges, e.edges[1:], strict=False):
        if b - a != (2 if a < e.fast else 1):
            raise ValueError("missing/extra/late WCK transition")
    if e.fast not in e.edges:
        raise ValueError("half-rate to full-rate transition")
    # End-to-end data path is independently checked against pin beat positions.
    # Write data remains in pin order, with no inferred PHY split reshaping.
    d = p["dfi_wck"]
    phy = p["phy_half_wck"]
    origin = e.command - e.cas
    expected_cs = tuple(
        (
            origin
            + 2 * d["tphy_rdcslat" if read else "tphy_wrcslat"]
            + 2 * i
            + (16 if r == 4 and e.beats == 32 and i >= 8 else 0),
            e.rank,
        )
        for i in range(e.beats // 2)
    )
    if e.data_cs != expected_cs:
        raise ValueError("DFI data chip-select timing/identity")
    if len(e.dfi_enable) != e.beats // 2:
        raise ValueError("DFI enable count")
    for i, en in enumerate(e.dfi_enable):
        gap = 16 if r == 4 and e.beats == 32 and i >= 8 else 0
        if en != origin + 2 * d["trddata_en" if read else "tphy_wrlat"] + 2 * i + gap:
            raise ValueError("DFI word cadence")
        if read:
            returned = e.returned[i]
            earliest = e.data[2 * i + 1] + phy["read"]
            if (
                not earliest <= returned < earliest + ck
                or returned % ck
                or not en <= returned <= en + 2 * d["tphy_rdlat"]
            ):
                raise ValueError("read path/deadline")
        elif e.dfi_data[i] + phy["write"] != e.data[2 * i] or e.dfi_data[i] - en != 2 * d["tphy_wrdata"]:
            raise ValueError("write data path/enable")
    if not read and e.data[-1] + 1 - e.dfi_enable[0] > 2 * d["twrdata_delay"]:
        raise ValueError("false write drain")


@dataclass
class SyncState:
    """Restricted single-rank-at-a-time policy; method 1 rank transitions.

    Reuse only same direction and same rank; direction changes stop and restart.
    Normal mode expiry follows the conservative same-BG Table 211/212 maximum.
    Synchronized-static restart is deliberately unavailable, rather than relying
    on zero timing fields. Always-on state requires continuous full-rate edges.
    """

    ratio: int
    always_on: bool = False
    rank: int | None = None
    direction: str | None = None
    last: int | None = None
    expires: int = -1
    end: int = -1
    off_at: int | None = None

    def access(self, at: int, rank: int, direction: str, beats: int, latency: int, new_sync: bool) -> None:
        if direction not in ("read", "write") or beats not in (16, 32) or rank not in (0, 1):
            raise ValueError("access")
        if self.rank is not None and rank != self.rank:
            raise ValueError("rank change requires full stop/fresh sync")
        live = self.rank is not None and (self.always_on or at <= self.expires)
        if live and new_sync:
            raise ValueError("duplicate sync within window")
        if not live and not new_sync:
            raise ValueError("expired/missing synchronization")
        if live and direction != self.direction:
            raise ValueError("restricted policy requires stop at turnaround")
        if self.last is not None and at - self.last < (beats // 4 if self.ratio == 4 else beats // (2 * self.ratio)):
            raise ValueError("same-BG spacing")
        self.rank = rank
        self.direction = direction
        self.last = at
        span = beats + (16 if self.ratio == 4 and beats == 32 else 0)
        self.end = at + (
            latency * 2 * self.ratio
            + span
            + (
                6
                if direction == "read" and self.ratio == 4
                else 3
                if direction == "read"
                else 2
                if self.ratio == 4
                else 1
            )
            + 5
            + 2 * self.ratio
            - 1
        ) // (2 * self.ratio)
        # Table 339: BG BL/n_max is BL/4 (8CK for BL32), whereas
        # the physical split span BL/n_min is only 6CK. 16B is BL/4.
        # floor(2.5WCK/CK) supplies the selected postamble term.
        self.expires = at + latency + beats // 4 + (2 // self.ratio)

    def stop(self, ws_off: int, disabled: int) -> None:
        if self.rank is None or ws_off < self.end or disabled < ws_off + 5:
            raise ValueError("early stop/ownership transfer")
        self.rank = None
        self.direction = None
        self.last = None
        self.off_at = disabled

    def fresh_after_stop(self, cas: int) -> None:
        if self.off_at is None or cas < self.off_at:
            raise ValueError("fresh sync before full disable")

    def static_restart(self) -> None:
        raise ValueError("use WS_OFF, full disable and fresh synchronization")

    def ownership(self, at: int, wck_disabled: bool) -> None:
        if not wck_disabled or self.rank is not None or (self.off_at is not None and at < self.off_at):
            raise ValueError("DFI ownership with WCK active")
