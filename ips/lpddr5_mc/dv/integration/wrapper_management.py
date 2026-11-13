"""Four real-wrapper unsolicited-management cases, with independent settlement.

Falling-CK pins are replayed with the existing physical model. The later rising
edge records internal transfers; equality of both raw bus samples qualifies
that phase bridge. Source AW/W/AR/R/B are independently ordered by picoseconds.
Internal empty flags cannot retire source, pin, row, return or WCK obligations.
"""

import copy
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

from continuous import InlineDrain, offset
from system.host import Address, HostScoreboard, require
from system.trace import number

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "channel"))
from physical_stream import PhysicalStream  # noqa: E402

IP = Path(__file__).resolve().parents[2]
BUSES = "cs ca wck write read return".split()


def profile_binding():
    p = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"][0]
    p = copy.deepcopy(p)
    p["ranks"] = 1
    p["device"]["mr57"] = 3
    return p


def digest(p):
    return hashlib.sha256(json.dumps(p, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def remaining_budget(p):
    """Bound the selected, uncontended final-check schedule in CK.

    Origin is the first CK carrying CAS for the check write. Words 8..11
    have enable offsets 48..51 WCK, so the twelfth enable is in CK12.
    The request is driven 1ps after that falling-CK observation. R is already
    fully produced, B is held, and only the final check backend is outstanding.
    The source periods are 2500/3500ps; each FIFO crossing is allowed four
    destination edges (two pointer synchronizers, empty update, consumption).
    This is a source-derived upper schedule for this fixture, not measured fit.
    """
    require(p["ratio"] == 4 and not p["always_on"], "wrapper schedule profile")
    r, d, t = p["ratio"], p["dfi_wck"], p["dram_ck"]

    def ceil_ck(ps):
        return (ps + p["ck_ps"] - 1) // p["ck_ps"]

    request_ck = (d["tphy_wrlat"] + 8 + 8 + 3) // r
    flush = (d["tphy_wrlat"] + d["tphy_wrdata"] + 24 + d["twrdata_delay"] + r - 1) // r
    pin_end = (p["phy_half_wck"]["command"] + p["wl_ck"] * 2 * r + p["phy_half_wck"]["dq_input"] + 48 + 2 * r - 1) // (
        2 * r
    )
    # PREab is legal after the final pin + tWR. Two tPPD intervals and
    # three CK of dispatch/sample slack cover the two open banks. The data
    # row is older; at this point the check row has already served its read.
    rows = max(pin_end + t["twr"] + 2 * t["tppd"] + t["trp_ab"], t["trc"]) - request_ck + 3
    # safe_end comparison, adapter result selection/output, inline COMPLETE,
    # host completion/B selection: eight CK, with no second live backend.
    original = flush - request_ck + 8
    response_fifo = ceil_ck(4 * 3500)
    # Eight CK hold, next falling source edge, four R edges, final falling
    # edge, then the two per-port BREADY drives. Only the first R is stalled;
    # all four have been produced before the long channel0 operation starts.
    ready = 8 + ceil_ck(5 * 3500) + ceil_ck(2500 + 3500)
    last_b = max(original + response_fifo, ready) + ceil_ck(3500)
    # Final source event crosses to core in four CK. At most two event heads
    # share that port. Three CK cover arbitration/ack/retirement. Retirement
    # crosses back in four slow edges; two more source edges release ledger
    # slots/register settled. Two core sync edges plus three wrapper/host
    # reductions finish settlement. Keep extra two CK arbitration slack.
    retire = 4 + 2 + 3 + ceil_ck(6 * 3500) + 2 + 3 + 2
    source = last_b + retire
    # Backend emit_stop waits for data_drained (safe_end), then a full
    # tWCKDIS interval. Do not start this interval at CAS or the last enable.
    wck = flush + (p["wck"]["twck_dis"] + r - 1) // r - request_ck + 3
    entry = 8  # DRAIN -> UPDATE or MASTER_ENTER/GUARD, and sampled ownership
    total = max(rows, source, wck) + entry
    limits = {k: p["management_ck"][k] for k in ("tphyupd_resp", "tphymstr_resp")}
    require(total < min(limits.values()), "selected remaining drain exceeds management response")
    return dict(
        request_ck=request_ck,
        safe_flush_ck=flush,
        pin_end_ck=pin_end,
        original_remaining_ck=original,
        response_fifo_ck=response_fifo,
        ready_ck=ready,
        last_b_ck=last_b,
        retirement_ck=retire,
        row_remaining_ck=rows,
        source_remaining_ck=source,
        wck_remaining_ck=wck,
        entry_ck=entry,
        total_ck=total,
        response_limits_ck=limits,
        response_release_ck=8,
        basis="source-derived uncontended schedule; actual observations required",
    )


def qualify_phases(records, header):
    require(
        header.get("sample_phase") == "negedge_after_prepare_before_sample"
        and header.get("return_source") == "memory_output_after_prepare"
        and header.get("handshake_phase") == "posedge_before_NBA"
        and header["ck_ps"] == 1250,
        "unqualified wrapper observation phases",
    )
    samples, edges = {}, {}
    for e in records:
        if e["kind"] in ("sample", "edge"):
            key = e["channel"], e["tick"]
            target = samples if e["kind"] == "sample" else edges
            require(key not in target, "duplicate phase sample")
            target[key] = e
    require(samples and samples.keys() == edges.keys(), "missing memory/edge phase tail")
    for key, s in samples.items():
        e = edges[key]
        require(e["ps"] - s["ps"] == header["ck_ps"] // 2, "memory/handshake phase displacement")
        require(all(number(e[k]) == number(s[k]) for k in BUSES), "raw buses changed across qualified phase")
        if key[1]:
            require(
                (key[0], key[1] - 1) in samples and s["ps"] - samples[key[0], key[1] - 1]["ps"] == header["ck_ps"],
                "missing CK sample",
            )
    return len(samples)


class Source:
    """AXI-edge ledger with response continuity, including VALID withdrawal."""

    def __init__(self):
        self.host = HostScoreboard(bus_bytes=16, ports=2, capacity=64, id_bits=8)
        self.tags = {}
        self.held = {}
        self.flags = {}
        self.counts = defaultdict(int)
        self.retired_epochs = []

    def event(self, e):
        kind, h = e["kind"], self.host
        if kind == "axi":
            p, f = e["port"], e["flags"]
            for response, bit in (("b", 3), ("r", 1)):
                require((p, response) not in self.held or bool(f >> bit & 1), "exposed response VALID withdrawn")
            self.flags[p] = e
        elif kind in ("aw", "ar"):
            key = e["port"], e["id"], kind == "aw"
            require(key not in self.tags, "duplicate source identity")
            a = Address(e["port"], e["id"], number(e["address"]), e["length"], e["size"], e["burst"], bool(e["lock"]))
            self.tags[key] = h.admit(a, kind == "aw")
            self.counts[kind] += 1
        elif kind == "w":
            require(number(e["strobe"]) == 65535, "selected wrapper W must be fully effective")
            h.w(e["port"], number(e["data"]), number(e["strobe"]), bool(e["last"]))
            self.counts[kind] += 1
        elif kind in ("b", "r"):
            tag = self.tags[e["port"], e["id"], kind == "b"]
            h.offer(tag, number(e.get("data", 0)), e["response"], bool(e.get("last", 1)))
            key = e["port"], kind
            if e["ready"]:
                h.handshake(tag)
                self.held.pop(key, None)
                self.counts[kind] += 1
            else:
                self.held[key] = tag

    def settled(self):
        return all(t.done for t in self.host.transactions.values()) and not any(self.host.aw)

    def owner(self, q, channel):
        at = offset(q)
        address = (at & 63) | (channel << 6) | ((at & ~63) << 1)
        candidates = [
            tag
            for tag, t in self.host.transactions.items()
            if not t.done and not t.physical and t.write == bool(q["write"]) and t.address.address == address
        ]
        require(len(candidates) == 1, "inline original lacks unique admitted source owner")
        tag = candidates[0]
        t = self.host.transactions[tag]
        require(q["bytes"] == 64 and len(t.ranges) == 4, "wrapper selected granule")
        if t.write:
            require(len(t.w) == 4 and not t.error, "inline before effective W validation")
            raw = b"".join(v.to_bytes(16, "little") for v, _ in t.w)
            require(
                number(q["data"]).to_bytes(64, "little") == raw and number(q["mask"]) == (1 << 64) - 1,
                "inline/source payload identity",
            )
        return tag

    def completed(self, tag, q, result, expected):
        h, t = self.host, self.host.transactions[tag]
        require(
            not result["failed"] and result["physical"] and result["integrity"] and expected is not None,
            "successful owner completion lacks independent result",
        )
        if h.faulted:
            # Independent physical/inline replay still validates the outcome.
            # Trust loss prevents it from authorizing a new source OKAY.
            self.counts["completed_after_fault"] += 1
            return
        if t.write:
            h.pin_write(tag, t.address.address, bytes(expected), (1 << 64) - 1, h.epoch)
        h.physical_done(tag)
        h.integrity_done(tag)
        if not t.write:
            actual = number(result["data"]).to_bytes(64, "little")
            require(actual == b"".join(v.to_bytes(16, "little") for v in t.read_data), "returned original/source bytes")


class Run:
    def __init__(self, p):
        self.p = p
        self.source = Source()
        self.physical = [None, None]
        self.inline = [InlineDrain(), InlineDrain()]
        self.tags = [None, None]
        self.q = [None, None]
        self.last = [None, None]
        self.cancelled = []
        self.case = None
        self.cases = []
        self.low = self.fresh = False
        self.reset_low = False
        self.reset_high = False
        self.boot = 0
        self.profile_image = None
        self.closure_edges = defaultdict(int)

    def marker(self, e):
        name = e["name"]
        if name == "begin":
            require(self.case is None, "overlapping wrapper cases")
            self.case = dict(id=e["case"], failed=e["failed"], begin=e["ps"], entries=0, reset=False, fresh=False)
        elif name == "request":
            c, h = self.case, self.source.host
            require(
                c is not None and self.inline[0].dirty and self.inline[0].pending is not None,
                "request outside independently dirty data/check interval",
            )
            require(self.inline[0].live["stage"] == 3, "request before final check physical stage")
            require(
                (1, "b") in self.source.held and (1, "r") in self.source.held,
                "request without exposed backpressured B/R",
            )
            owed = sum(len(t.ranges) - len(t.w) for t in h.transactions.values() if t.write and not t.done)
            require(owed > 0 if c["failed"] else owed == 0, "incorrect trailing-W success/failure split")
            require(not self.source.settled(), "pre-quiesced unsolicited stimulus")
            c.update(request=e["ps"], owed=owed)
            c["schedule"] = remaining_budget(self.p)
            c["closure_probes"] = {0: 0, 1: 0}
            self.closure_edges.clear()
        elif name == "error":
            require(self.case["failed"] and self.inline[0].dirty, "error outside dirty failure")
            self.case["error"] = e["ps"]
            self.source.host.fail()
        elif name == "stale_confirmation_rejected":
            require(
                self.source.settled() and self.last[0]["reset_request"] and self.last[0]["boot"] == self.boot,
                "stale recovery accepted",
            )
            self.case["stale_rejected"] = True
        elif name == "fresh_service":
            require(
                self.source.settled()
                and not self.source.host.faulted
                and self.last[0]["trusted"]
                and self.last[0]["trained"]
                and self.last[0]["config_valid"],
                "fresh service lacks settlement/trust",
            )
            self.case["fresh"] = True
        elif name == "end_case":
            c = self.case
            require(c["fresh"] and self.source.settled(), "missing fresh service/complete W/R/B tail")
            require(c["entries"] == (0 if c["failed"] else 1), "missing or premature ownership outcome")
            require(not c["failed"] or c["reset"] and c.get("stale_rejected"), "missing confirmed recovery")
            require(c["failed"] or "ack_ps" in c and c.get("handback"), "missing ACK/withdrawal/handback")
            require(all(c.get("closed", {}).get(p, 0) > 0 for p in range(2)), "missing source closure observations")
            require(all(c["closure_probes"][p] > 0 for p in range(2)), "missing active AW/AR closure probes")
            self.cases.append(c)
            self.case = None

    def sample(self, e):
        channel = e["channel"]
        old = self.last[channel]
        self.last[channel] = e
        if self.profile_image is None:
            self.profile_image = e["profile_image"]
        require(e["profile_image"] == self.profile_image, "unbound profile mutation")
        c = self.case
        if channel == 0:
            if not e["confirmed"]:
                self.reset_low = True
            if self.reset_low and e["confirmed"]:
                self.reset_high = True
            # While admission is open the remote empty reduction may still
            # describe the edge before a new source acceptance crosses CDC.
            # It cannot authorize management entry: entry always checks the
            # independent ledger, and closed admission must settle for real.
            if e["settled"] and not e["enabled"]:
                require(self.source.settled(), "internal settlement before actual W/R/B ledger")
            if e["reset_request"]:
                require(self.source.settled(), "destructive recovery before source settlement")
            if e["destructive"] and not (old and old["destructive"]):
                require(
                    self.source.settled() and self.reset_low and self.reset_high,
                    "unconfirmed/stale destructive recovery",
                )
                self.source.retired_epochs.append(
                    [
                        dict(
                            tag=tag,
                            port=t.address.port,
                            id=t.address.id,
                            address=t.address.address,
                            write=t.write,
                            accepted_w=len(t.w),
                            consumed_r=t.response_index,
                            done=t.done,
                            final_response=t.offered,
                            physical=t.physical,
                            integrity=t.integrity,
                        )
                        for tag, t in self.source.host.transactions.items()
                    ]
                )
                self.source.host.reset(destructive=True)
                self.source.tags.clear()
                self.reset_low = self.reset_high = False
                require(c and c["failed"], "unexpected destructive recovery")
                c["reset"] = True
            if old and e["boot"] != old["boot"]:
                require(c and c["reset"] and e["boot"] == self.boot + 1, "stale boot recovery")
                self.boot = e["boot"]
                require(
                    not e["config_valid"] and not e["trained"] and not e["enabled"],
                    "stale recovery configuration/training",
                )
            if e["trusted"]:
                require(e["trained"] and e["config_valid"] and e["training_epoch"] == e["boot"], "stale trusted epoch")
        reset = not e["reset_n"] or e["memory_reset"] or e["destructive"]
        if reset:
            if self.physical[channel] is not None:
                require(e["destructive"], "physical work cancelled without destructive reset")
                self.cancelled.append(
                    dict(
                        channel=channel,
                        ps=e["ps"],
                        physical=self.physical[channel].summary(cancelled=True),
                        inline_live=int(self.inline[channel].live is not None),
                    )
                )
                self.physical[channel] = None
                self.inline[channel].reset()
                self.tags[channel] = self.q[channel] = None
            return
        if self.physical[channel] is None:
            self.physical[channel] = PhysicalStream(copy.deepcopy(self.p))
        physical = self.physical[channel]
        physical.sample(e | {"time_ps": e["ps"]})
        if channel != 0 or c is None or "request" not in c or c.get("reset"):
            return
        active = e["phy_update"] or e["master"] or e["owner"] == 1
        was_active = old and (old["phy_update"] or old["master"] or old["owner"] == 1)
        if (e["phy_update"] or e["master"]) and not e["fault"]:
            selected = "phyupd_req" if c["id"] < 2 else "phymstr_req"
            ack = "phy_update" if c["id"] < 2 else "master"
            require(not c["failed"] and e[ack] and (e[selected] or old and old[ack]), "unsolicited/wrong ACK")
            c.setdefault("ack_ps", e["ps"])
        if old and (old["phy_update"] or old["master"]) and not (e["phy_update"] or e["master"]) and not e["fault"]:
            require(
                not old["phyupd_req"] and not old["phymstr_req"] and not e["phyupd_req"] and not e["phymstr_req"],
                "ACK release before request withdrawal",
            )
            c["handback"] = True
        if active and not e["fault"] and not was_active:
            require(not c["failed"] and self.source.settled() and e["settled"], "ownership before source settlement")
            require(
                physical.drained() and self.inline[0].live is None, "ownership before independent physical/row/WCK tail"
            )
            require(
                e["ps"] - c["request"]
                <= min(remaining_budget(self.p)["response_limits_ck"].values()) * self.p["ck_ps"],
                "management response deadline",
            )
            require(
                e["ps"] - c["request"] <= c.get("schedule", remaining_budget(self.p))["total_ck"] * self.p["ck_ps"],
                "selected event schedule exceeded",
            )
            c["entry_elapsed_ck"] = (e["ps"] - c["request"]) / self.p["ck_ps"]
            c["entries"] += 1
            c["entry_ps"] = e["ps"]
        if e["phyupd_req"] or e["phymstr_req"]:
            key = tuple(
                e[k]
                for k in (
                    "phyupd_req",
                    "phymstr_req",
                    "phyupd_type",
                    "phymstr_type",
                    "phymstr_cs_state",
                    "phymstr_state_sel",
                )
            )
            require("request_fields" not in c or c["request_fields"] == key, "unstable management request")
            c["request_fields"] = key
        if e["master"]:
            if not e["rank_valid"]:
                self.low = True
            if self.low and e["rank_valid"] == 1 and not e["rank_sr"]:
                self.fresh = True
            require(not e["refresh_done"] or old and old["refresh_request"], "unsolicited refresh completion")
        if old and old["master"] and not e["master"] and not e["fault"]:
            require(
                self.fresh and not e["phymstr_req"] and not e["refresh_request"],
                "master handback lacks fresh rank report/refresh settlement",
            )
            self.low = self.fresh = False
        if active and not e["fault"]:
            require(
                not e["cs"] and not number(e["write"]) >> 24 & 255 and not number(e["read"]) >> 16,
                "controller traffic during PHY ownership",
            )

    def event(self, e):
        kind = e["kind"]
        if kind in ("axi", "aw", "ar", "w", "b", "r"):
            c = self.case
            if c and "request" in c and not c.get("fresh") and kind == "axi" and e["ps"] >= c["request"]:
                self.closure_edges[e["port"]] += 1
                # Two enable synchronizers plus one edge for pre-NBA sampling.
                if self.closure_edges[e["port"]] >= 4 and not self.last[0]["enabled"]:
                    require(not e["flags"] & ((1 << 8) | (1 << 6)), "source admission after closure latency")
                    closed = c.setdefault("closed", {})
                    closed[e["port"]] = closed.get(e["port"], 0) + 1
                    if e["flags"] & (1 << 9) and e["flags"] & (1 << 7):
                        probes = c.setdefault("closure_probes", {0: 0, 1: 0})
                        probes[e["port"]] += 1
            if c and "request" in c and kind in ("aw", "ar") and not self.last[0]["enabled"]:
                require(self.closure_edges[e["port"]] < 4, "new source admission after closure")
            self.source.event(e)
        elif kind == "sample":
            self.sample(e)
        elif kind == "marker":
            self.marker(e)
        elif kind.startswith("inline_"):
            channel = e["channel"]
            require(self.physical[channel] is not None, "internal transfer during reset")
            last = self.last[channel]
            require(
                e["tick"] == last["tick"] and e["ps"] == last["ps"] + self.p["ck_ps"] // 2,
                "internal event outside qualified handshake phase",
            )
            require(
                e["boot"] == last["boot"] and e["config"] == last["config"] and e.get("rank", 0) < self.p["ranks"],
                "stale internal profile/rank/epoch",
            )
            if kind == "inline_admit":
                self.tags[channel] = self.source.owner(e, channel)
                self.q[channel] = e
            elif kind == "inline_physical":
                self.physical[channel].expect(e)
            elif kind == "inline_result":
                self.physical[channel].result(e)
            if kind == "inline_complete" and not e["failed"]:
                self.source.completed(self.tags[channel], self.q[channel], e, self.inline[channel].live["expected"])
            self.inline[channel].event(e)

    def finish(self):
        require(self.case is None and [c["id"] for c in self.cases] == list(range(4)), "missing four-case wrapper tail")
        self.source.host.finish()
        require(all(c["entries"] == (not c["failed"]) for c in self.cases), "missing management entries")
        return dict(
            cases=self.cases,
            source=dict(self.source.counts),
            retired_epochs=self.source.retired_epochs,
            cancelled=self.cancelled,
            physical=[p.summary() for p in self.physical],
            profile_sha256=digest(self.p),
            schedule=remaining_budget(self.p),
            limitations=[
                "No trailing-W-at-request success or deadline-exhaustion hardware claim.",
                "Short master window has no refresh request due; active PHY refresh service remains separate evidence.",
                "No wrapper FSP/power or forwarding/merging qualification.",
            ],
        )


def check(wrapper_path, source_path):
    records = [json.loads(line) for line in Path(wrapper_path).read_text().splitlines()]
    source = [json.loads(line) for line in Path(source_path).read_text().splitlines()]
    header, p = records[0], profile_binding()
    require(
        header["kind"] == "header" and header["version"] == 2 and header["profile_sha256"] == digest(p),
        "wrapper profile binding",
    )
    require(records[-1]["kind"] == "end" and source[-1]["kind"] == "end", "missing wrapper/source terminal tail")
    phases = qualify_phases(records[1:-1], header)
    # CK model samples before the later edge. All actual internal events carry
    # edge timestamps; source admission precedes owner acceptance at a tie.
    order = {
        "axi": 0,
        "aw": 1,
        "ar": 2,
        "w": 3,
        "inline_admit": 4,
        "inline_physical": 5,
        "inline_result": 6,
        "inline_complete": 7,
        "b": 8,
        "r": 9,
    }
    events = [e for e in records[1:-1] if e["kind"] != "edge"]
    events.extend(e for e in source[1:-1] if e["kind"] != "phy")
    events.sort(key=lambda e: (e["ps"], order.get(e["kind"], -1), e.get("port", e.get("channel", 0))))
    run = Run(p)
    for e in events:
        run.event(e)
    return run.finish() | {
        "phase_samples": phases,
        "raw_sha256": hashlib.sha256(Path(wrapper_path).read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(Path(source_path).read_bytes()).hexdigest(),
    }
