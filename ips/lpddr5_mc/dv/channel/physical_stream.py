"""Independent CA/row, data-CS, pin-byte and returned-slice replay for C2.

This adapter accepts serialized inline backend requests only as *expectations*.
Their results cannot retire an expectation without independently decoded CA,
all physical write beats or returned read slices, and the declared path tail.
Faults leave these obligations live. Only the stream owner may cancel on reset.
"""

from collections import deque

from continuous import identity, offset
from contracts.lifecycle import Maintenance
from contracts.read_completion import CompletionOracle, ExpectedWord, ReadBusOracle
from system.adapters import returned_snapshot
from system.dram import decode
from system.host import require
from system.phy import DigitalPhy
from system.return_timing import ReturnTiming
from thermal import ThermalReturns
from wck_observations import WckObservations


def number(value):
    return int(value, 0) if isinstance(value, str) else value


class Retention(Maintenance):
    """Suspend retention deadlines after observed trust loss, retaining rows/data.

    RFM counts, row recovery, burst obligations and WCK remain active. A lost
    retention promise never authorizes resumed service without a fresh reset.
    """

    suspended = True

    def advance(self, to):
        if self.suspended:
            require(to >= self.now, "retention time reversal")
            self.now = to
        else:
            super().advance(to)


class PhysicalStream:
    def __init__(self, profile):
        self.p = profile
        require(
            not profile["read_link"] and not profile["write_link"] and not profile["read_dbi"],
            "C2 physical binding excludes link/read DBI",
        )
        self.phy = DigitalPhy(profile)
        self.phy.dram.maintenance = Retention(
            ranks=profile["ranks"],
            mr27=profile["device"]["mr27"],
            mr57=profile["device"]["mr57"],
        )
        self.thermal = ThermalReturns(self.phy.dram.maintenance)
        self.timing = ReturnTiming(profile)
        self.wck = WckObservations(profile)
        self.completion = CompletionOracle([], 16, profile["dfi_wck"]["tphy_rdlat"])
        self.bus = ReadBusOracle(profile["ratio"], self.completion)
        self.requests = deque()
        self.bursts = []
        self.expected = {}
        self.serial = self.ck = self.words = self.slices = self.commands = 0
        self.wck_origin = 0
        self.prior_wck = []
        self.was_trusted = False
        self.retention_suspensions = []

    def expect(self, q):
        require(not self.requests, "second backend request before physical result")
        self.requests.append(dict(q=q, bursts=[], bytes=0, received=bytearray(64), seen=set(), final=-1))

    def result(self, e):
        require(bool(self.requests), "backend result without independent request")
        t = self.requests[0]
        require(
            identity(t["q"]) == identity(e) and t["q"]["write"] == e["write"],
            "physical result identity",
        )
        # A failed internal result is accounted separately and cannot erase pins.
        if not e["failed"]:
            require(
                e["physical"] and e["integrity"] and not e["response"],
                "backend result stages",
            )
            self.ready(t)
            if not e["write"]:
                require(
                    number(e["data"]).to_bytes(64, "little") == t["received"],
                    "backend read result differs from independent returned slices",
                )
        else:
            require(
                e["response"] == 2 and "failed_result" not in t,
                "backend failure response",
            )
            # Internal failure never cancels the independently decoded bus tail.
            # Keep this expectation and its pin/return obligations until reset.
            t["failed_result"] = e
            return
        self.requests.popleft()

    def ready(self, t):
        require(
            t["bytes"] == t["q"]["bytes"] and len(t["seen"]) == t["q"]["bytes"] and self.ck > t["final"],
            "internal result before independent physical data/return/path tail",
        )

    def row_recovery(self):
        """Report closure/readiness from decoded CA in the DRAM CK domain.

        Closing a row does not end PRE/AP/REF recovery or the last ACT's tRC.
        Use last_ck, the sample just replayed through the explicit PHY delay;
        self.ck already points at the *next* DFI sample after sample() returns.
        Neither a DUT drained flag nor an internal result establishes this.
        """
        dram = self.phy.dram
        trc = self.p["dram_ck"]["trc"] + dram.maintenance.timing_derating().get("trc", 0)
        ready = max(
            dram.zq_ready,
            *dram.rank_ready,
            *(max(b.ready, b.act + trc) for rank in dram.banks for b in rank),
            *(at + self.p["dram_ck"]["tpbr2act"] for at in dram.last_pb),
        )
        opened = [
            dict(rank=r, bank=n, row=b.row)
            for r, rank in enumerate(dram.banks)
            for n, b in enumerate(rank)
            if b.row is not None
        ]
        return dict(at_ck=dram.last_ck, ready_ck=ready, open_banks=opened, ready=not opened and dram.last_ck >= ready)

    def drained(self):
        return (
            not self.requests
            and not any(self.completion.pending.values())
            and all(self.ck > t["end"] for t in self.bursts)
            and self.phy.dram.pending is None
            and not (self.phy.dram.sync and self.phy.dram.sync[2] in (1, 2))
            and self.wck.epoch is None
            and self.row_recovery()["ready"]
        )

    def profile(self, p):
        require(
            self.drained(),
            "profile change before independent physical/row/WCK drain",
        )
        require(
            all((mr.get(16, 0) & 0xF0) == (p["mr"]["16"] & 0xF0) for mr in self.phy.dram.mr),
            "profile change before target normal VRCG/control restore",
        )
        require(p["ck_ps"] == self.p["ck_ps"], "unsupported CK change")
        self.prior_wck.append(self.wck.finish())
        self.p = self.phy.p = self.phy.dram.p = p
        self.phy.dram.ck = 2 * p["ratio"]
        # Full CS/data drain makes old half-WCK slots historical. The CK row
        # recovery timers, memory, maintenance and MR state remain authoritative.
        self.phy.dram.bus_slots.clear()
        self.wck = WckObservations(p)
        self.wck_origin = self.ck
        self.timing = ReturnTiming(p)
        self.timing.last_ck = self.ck - 1
        self.completion = CompletionOracle([], 16, p["dfi_wck"]["tphy_rdlat"])
        self.bus = ReadBusOracle(p["ratio"], self.completion)

    def sample(self, e, *, command=None):
        ck, p, model = self.ck, self.p, self.phy
        r, d = p["ratio"], p["dfi_wck"]
        m = model.dram.maintenance
        if e["trusted"] and not self.was_trusted:
            m.last_refresh = [ck + p["management_ck"]["tctrl_delay"]] * p["ranks"]
        suspended = not e["trusted"]
        if suspended and (not self.retention_suspensions or self.retention_suspensions[-1]["end"] is not None):
            self.retention_suspensions.append(dict(start=ck, end=None))
        elif not suspended and self.retention_suspensions and self.retention_suspensions[-1]["end"] is None:
            self.retention_suspensions[-1]["end"] = ck
        m.suspended = suspended
        self.was_trusted = bool(e["trusted"])
        self.wck.sample(ck - self.wck_origin, e["cs"], e["ca"], number(e["wck"]))
        # A wrapper may bind an independently captured physical CA mux to
        # this command's delayed DRAM CK. Only CA changes: DFI data/return,
        # WCK, memory, maintenance and pending obligations keep this owner.
        cs, ca = (e["cs"], e["ca"]) if command is None else command
        b = model.command(ck, cs, ca)
        op = decode(e["ca"]) if e["cs"] else None
        self.commands += bool(e["cs"])
        if b is not None:
            require(bool(self.requests), "pin access without inline physical expectation")
            t = self.requests[0]
            address = (b.row << 15) | (b.bank << 11) | (b.column << 5)
            q = t["q"]
            require(
                b.write == bool(q["write"]) and b.rank == q["rank"] and address == offset(q) + t["bytes"],
                "CA physical request address/order",
            )
            t["bytes"] += b.beats * 2
            require(t["bytes"] <= q["bytes"], "duplicate/excess physical burst")
            self.add(b, ck, address, t)
        elif op and op.kind == "MRR":
            self.add(
                None,
                ck,
                0,
                None,
                mr=e["mr43"] if op.operand == 43 else e["mr4"],
                cs=e["cs"],
            )
            self.thermal.begin(self.serial, ck, e["cs"], op.operand)
        raw_write, raw_read, raw_return = (number(e[k]) for k in ("write", "read", "return"))
        self.timing.sample(ck, e["cs"], e["ca"], raw_return >> 32 & 255)
        expected_write = expected_read = expected_wcs = expected_rcs = 0
        for a in self.bursts:
            burst, origin, words = a["burst"], a["origin"], a["words"]
            write = burst is not None and burst.write
            for lane in range(r):
                at = ck * r + lane
                start = origin * r + d["tphy_wrcslat" if write else "tphy_rdcslat"]
                span = words + (8 if r == 4 and words == 16 else 0)
                if start <= at < start + span:
                    if write:
                        expected_wcs |= a["cs"] << (lane * 2)
                    else:
                        expected_rcs |= (a["cs"] * 5) << (lane * 4)
                for w in range(words):
                    enable = origin * r + d["tphy_wrlat" if write else "trddata_en"] + w
                    enable += 8 if r == 4 and words == 16 and w >= 8 else 0
                    if at == enable:
                        if write:
                            expected_write |= 3 << (lane * 2)
                        else:
                            expected_read |= 3 << (lane * 2)
                    if write and at == enable + d["tphy_wrdata"]:
                        value = raw_write >> (48 + lane * 32) & 0xFFFFFFFF
                        mask = raw_write >> (32 + lane * 4) & 15
                        model.write_word(burst, w, 2 * at, value, mask)
                        t = a["request"]
                        for byte in range(4):
                            raw = value >> (byte * 8) & 255
                            invert = mask >> byte & 1
                            masked = burst.masked and (
                                (raw >> 2).bit_count() >= 5 and not invert if p["write_dbi"] else invert
                            )
                            i = a["address"] + w * 4 + byte - offset(t["q"])
                            require(
                                not masked
                                and (raw ^ (255 if p["write_dbi"] and invert else 0))
                                == number(t["q"]["data"]) >> (i * 8) & 255,
                                "physical data/shared-check write mismatch",
                            )
                            t["seen"].add(i)
                        self.words += 1
        require(raw_write >> 24 & 255 == expected_write, "physical write enable mismatch")
        require(
            raw_write >> 16 & 255 == expected_wcs,
            "physical continuous write CS mismatch",
        )
        require(raw_read >> 16 == expected_read, "physical read enable mismatch")
        require(raw_read & 65535 == expected_rcs, "physical continuous read CS mismatch")
        require(raw_write & 65535 == 0, "unexpected write link parity")
        decoded = returned_snapshot(
            r,
            data=raw_return >> 40,
            valid=raw_return >> 32 & 255,
            dnv=raw_return >> 16 & 65535,
            link_parity=raw_return & 65535,
        )
        require(raw_return & 0xFFFFFFFF == 0, "unexpected return DNV/parity")
        before = {(s, w, lane) for s, data in self.completion.received.items() for w, lane in data}
        self.bus.snapshot(ck, decoded["data"], decoded["dnv"])
        for serial, received in self.completion.received.items():
            a = self.expected[serial]
            for (word, lane), value in received.items():
                if (serial, word, lane) in before:
                    continue
                raw = a["values"][word]
                require(
                    value == ((raw >> (lane * 8) & 255) | (raw >> (16 + lane * 8) & 255) << 8),
                    "returned slice differs from independent physical memory",
                )
                self.thermal.receive(
                    serial,
                    word,
                    lane,
                    value,
                    ck,
                    e.get("time_ps", ck * self.p["ck_ps"]),
                )
                if a["request"] is not None:
                    t = a["request"]
                    i = a["address"] + word * 4 + lane - offset(t["q"])
                    for half in range(2):
                        t["received"][i + half * 2] = value >> (half * 8) & 255
                        t["seen"].add(i + half * 2)
                    t["final"] = max(t["final"], ck)
                self.slices += 1
        self.ck += 1
        # Bound the live CS/data schedule; retained expectations and memory stay.
        self.bursts = [a for a in self.bursts if a["end"] >= ck]

    def add(self, b, ck, address, request, *, mr=0, cs=0):
        self.serial += 1
        r, d = self.p["ratio"], self.p["dfi_wck"]
        words = b.beats // 2 if b else 8
        write = b is not None and b.write
        span = words + (8 if r == 4 and words == 16 else 0)
        # End is exclusive in WCK units at the DFI data/CS interface.
        end = ck * r + max(
            d["tphy_wrcslat" if write else "tphy_rdcslat"] + span,
            d["tphy_wrlat"] + d["tphy_wrdata"] + span if write else 0,
        )
        end_ck = (end + r - 1) // r
        a = dict(
            burst=b,
            origin=ck,
            address=address,
            request=request,
            words=words,
            cs=(1 << b.rank) if b else cs,
            end=end_ck,
        )
        self.bursts.append(a)
        if request is not None:
            request["bursts"].append(a)
            if write:
                # DFI 5.0 3.2 / Table 12 / 4.9.3 measures twrdata_delay
                # from DFI write enable, not from the final DRAM pin beat.
                # Keep the declared conservative data-end-plus-delay flush,
                # independently of the decoded pin endpoint. Adding the delay
                # to that endpoint counts the serialization path twice.
                physical = (b.times[-1] + 1 + 2 * r - 1) // (2 * r)
                flush = (ck * r + d["tphy_wrlat"] + d["tphy_wrdata"] + span + d["twrdata_delay"] + r - 1) // r
                request["final"] = max(request["final"], end_ck, physical, flush)
        if not write:
            if b:
                beats = [self.phy.dram.beat(b, i, b.times[i]) for i in range(b.beats)]
                a["values"] = [beats[i] | beats[i + 1] << 16 for i in range(0, b.beats, 2)]
            else:
                a["values"] = [0xDA00DA00 | mr | mr << 16 if w < 4 else 0xDADADADA for w in range(8)]
            self.expected[self.serial] = a
            for w in range(words):
                enable = ck * r + d["trddata_en"] + w + (8 if r == 4 and words == 16 and w >= 8 else 0)
                for lane in range(2):
                    self.completion.pending[lane].append(ExpectedWord(enable, self.serial, w, lane))
            for lane in range(2):
                self.completion.pending[lane] = deque(sorted(self.completion.pending[lane], key=lambda w: w.enable))
            self.completion.received[self.serial] = {}

    def summary(self, *, cancelled=False):
        pending_beats = sum(b.beats for b in self.phy.dram.bursts) - len(self.phy.dram.seen)
        if not cancelled:
            require(not self.requests, "unsettled physical request tail")
            require(not self.bursts, "incomplete physical CS/data tail")
            self.phy.finish()
            self.timing.finish()
            self.thermal.finish()
            self.wck.finish()
            require(self.wck.epoch is None, "unfinished WCK ownership tail")
        return dict(
            row_recovery=self.row_recovery(),
            commands=self.commands,
            write_words=self.words,
            returned_slices=self.slices,
            wck=dict(
                epochs=self.wck.epochs,
                accesses=self.wck.accesses,
                half_wck_samples=self.wck.samples,
                disabled_epochs=self.wck.disabled,
                active_at_end=self.wck.epoch is not None,
            ),
            prior_wck_profiles=self.prior_wck,
            wck_total={
                k: sum(w[k] for w in self.prior_wck) + value
                for k, value in dict(
                    epochs=self.wck.epochs,
                    accesses=self.wck.accesses,
                    half_wck_samples=self.wck.samples,
                    disabled_epochs=self.wck.disabled,
                ).items()
            },
            retention_suspensions=[
                dict(i, end=self.ck if i["end"] is None else i["end"]) for i in self.retention_suspensions
            ],
            temperature_returns=self.thermal.observations,
            cancelled=dict(
                requests=len(self.requests),
                pin_beats=pending_beats,
                return_slices=sum(len(q) for q in self.timing.pending),
                ca_pairs=int(self.phy.dram.pending is not None),
                wck_epochs=int(self.wck.epoch is not None),
            )
            if cancelled
            else None,
        )
