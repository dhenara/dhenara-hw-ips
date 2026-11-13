"""Check serialized WCK controls from external CA and physical path timings.

Each packed phase carries two half-WCK edge indications for each byte slice.
No controller state, timer, reusable flag or completion indication is consumed.
This checks the declared digital PHY interpretation, not an electrical clock.
"""

from system.dram import decode
from system.host import require


class WckObservations:
    def __init__(self, profile: dict):
        self.p = profile
        self.ratio = profile["ratio"]
        self.ck = 2 * self.ratio
        self.last_ck = -1
        self.last_disabled = -1
        self.last_off = None
        self.epoch = None
        self.epochs = self.accesses = self.samples = self.disabled = 0

    def sample(self, ck: int, cs: int, ca: int, packed: int) -> None:
        require(ck == self.last_ck + 1, "missing WCK observation CK")
        require(0 <= packed < 1 << 40, "packed WCK width")
        self.last_ck = ck
        op = decode(ca) if cs else None
        # Process sampled outputs before this edge's command. The documented
        # registered digital producer can assert rank selection on the next
        # sample; its enable/preamble timing still has the actual CAS origin.
        for phase in range(4):
            for byte_slice in range(2):
                lane = phase * 2 + byte_slice
                enable = packed >> (32 + lane) & 1
                rank = packed >> (16 + 2 * lane) & 3
                toggle = packed >> (2 * lane) & 3
                if phase >= self.ratio:
                    require(not (enable or rank or toggle), "WCK inactive upper phase")
                    continue
                at = (ck * self.ratio + phase) * 2 + self.p["phy_half_wck"]["wck"]
                self._phase(at, byte_slice, enable, rank, toggle)
                self.samples += 2
        e = self.epoch
        if e is not None and all(e["released"]):
            self.last_disabled = max(e["released_at"])
            self.disabled += 1
            self.epoch = e = None
        if op is None:
            return
        if op.kind == "CAS":
            require(cs in (1, 2) and cs.bit_length() <= self.p["ranks"], "WCK CAS rank")
            if op.operand == 7:
                require(e is not None and e["rank"] == cs and e["accesses"], "WCK WS_OFF ownership")
                ws = ck * self.ck + self.p["phy_half_wck"]["command"]
                require(ws >= e["post"], "WCK WS_OFF before physical postamble")
                e["off"] = ws
                self.last_off = ws
            else:
                require(op.operand in (1, 2) and e is None, "WCK synchronization before full disable")
                direction = "write" if op.operand == 1 else "read"
                origin = ck * self.ck + self.p["phy_half_wck"]["command"]
                require(origin >= self.last_disabled, "WCK fresh CAS precedes final phase disable")
                # JESD209-5C Table 363: WS_OFF to a fresh sync is >=3 CK,
                # independently of serialized clock/rank release.
                require(
                    self.last_off is None or origin >= self.last_off + 3 * self.ck,
                    "WCK fresh CAS before WS_OFF command recovery",
                )
                # JEDEC Tables 204/205/208/209, independently transcribed in
                # the accepted waveform oracle. Static preamble is four CK,
                # followed by one CK of half-rate toggling.
                cycles = (
                    (4 if self.ratio == 4 else 3)
                    if direction == "write"
                    else (7 + 2 * int(self.p["read_link"]) + int(self.p["read_dbi"]))
                    if self.ratio == 4
                    else 5 + 2 * int(self.p["read_dbi"])
                )
                enable_at = origin + cycles * self.ck
                self.epoch = {
                    "rank": cs,
                    "direction": direction,
                    "command": ck,
                    "enable": enable_at,
                    "slow": enable_at + 4 * self.ck,
                    "fast": enable_at + 5 * self.ck,
                    "post": None,
                    "off": None,
                    "accesses": 0,
                    "last_access": None,
                    "seen": [False, False],
                    "stopped": [False, False],
                    "released": [False, False],
                    "released_at": [None, None],
                }
                self.epochs += 1
        elif op.kind in ("RD", "WR", "MWR", "MRR"):
            direction = "write" if op.kind in ("WR", "MWR") else "read"
            require(
                e is not None and e["rank"] == cs and e["direction"] == direction and e["off"] is None,
                "WCK access without matching synchronization",
            )
            require(not any(e["stopped"]), "WCK access after stopped clock")
            if e["last_access"] is None:
                require(ck == e["command"] + 1, "WCK CAS/access adjacency")
            elif not self.p["always_on"]:
                beats_before = e["last_beats"]
                latency = self.p["wl_ck" if direction == "write" else "rl_ck"]
                expires = e["last_access"] + latency + beats_before // 4 + 2 // self.ratio
                require(ck <= expires, "WCK synchronization expired")
            beats = 16 if op.kind == "MRR" else op.beats
            first = ck * self.ck + self.p["phy_half_wck"]["command"]
            first += self.p["wl_ck" if direction == "write" else "rl_ck"] * self.ck
            first += self.p["phy_half_wck"]["dq_input" if direction == "write" else "dq_output"]
            end = first + beats + (16 if self.ratio == 4 and beats == 32 else 0)
            require(e["fast"] <= first, "WCK data before full-rate synchronization")
            e["post"] = max(e["post"] or 0, end + 5)
            e["last_access"], e["last_beats"] = ck, beats
            e["accesses"] += 1
            self.accesses += 1

    def _phase(self, at: int, byte_slice: int, enable: int, rank: int, toggle: int) -> None:
        e = self.epoch
        if e is None:
            require(not (enable or rank or toggle), "WCK activity without decoded CAS")
            return
        s = byte_slice
        if rank == 0:
            require(not (enable or toggle), "WCK enable/edges without selected rank")
            if not e["seen"][s] and at < e["enable"]:
                return
            require(e["post"] is not None and at >= e["post"], "WCK rank release before physical postamble")
            if self.p["always_on"]:
                require(e["off"] is not None and at >= e["off"] + 5 * self.ck, "WCK disable before WS_OFF recovery")
            if not e["released"][s]:
                e["released_at"][s] = at
                e["released"][s] = True
            return
        require(rank == e["rank"] and not e["released"][s], "WCK rank change before full disable")
        e["seen"][s] = True
        require(enable == int(at >= e["enable"]), "WCK enable timing")
        if at < e["slow"]:
            expected = 0
        elif at < e["fast"]:
            expected = 2
        else:
            expected = 3
            if toggle == 0:
                minimum = (
                    e["off"] + 5 * self.ck
                    if self.p["always_on"] and e["off"] is not None
                    else None
                    if self.p["always_on"]
                    else e["post"]
                )
                require(minimum is not None and at >= minimum, "WCK missing edge before physical postamble/WS_OFF")
                e["stopped"][s] = True
                expected = 0
            require(not e["stopped"][s] or toggle == 0, "WCK restarted without fresh synchronization")
        require(toggle == expected, "WCK half/full-rate edge pattern")

    def finish(self) -> dict:
        if self.epoch is not None:
            require(self.epoch["accesses"] > 0, "WCK unfinished synchronization tail")
            end = (self.last_ck + 1) * self.ck + self.p["phy_half_wck"]["wck"]
            require(end >= self.epoch["post"], "WCK trace ends before physical postamble")
        return {
            "epochs": self.epochs,
            "accesses": self.accesses,
            "half_wck_samples": self.samples,
            "disabled_epochs": self.disabled,
            "active_at_trace_end": self.epoch is not None,
        }
