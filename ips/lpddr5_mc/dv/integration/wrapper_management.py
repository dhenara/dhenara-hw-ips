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
from host_completions import HOST_FIELDS, HostCompletions
from maintenance_window import WINDOW_FIELDS, MaintenanceWindow, window_budget
from system.host import Address, HostScoreboard, require
from system.trace import number
from wrapper_master import PhysicalCommands, RfmTimeout, active_refresh, settled_abort
from wrapper_recovery import RECOVERY_FIELDS, RECOVERY_PHASE, RECOVERY_REGISTERED, RecoveryConsumption

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


def final_tail_budget(p, page_idle=256):
    """Conservative continuous non-drain interval for this idle two-channel fixture.

    After fresh_service all W/R/B and inline completions have retired. Keep
    a full BL32 physical/recovery allowance, every bank's idle PRE, WCK
    disable, and a carry-in plus newly arriving REF, one RFM, MR4 and ZQ per
    channel. No new ACT debt is generated while idle. Summing both channels
    bounds staggered tails, even though their owners run concurrently.

    At thermal code9, REFab interval is3124CK and MR4/ZQ period3000CK.
    Prove the sum shorter than the early poll lead window: a second newly
    arriving group cannot extend this continuous busy interval. This is a
    fixture bound, not a new profile or management response limit.
    """
    require(p["ranks"] == 1 and not p["always_on"] and page_idle > 0, "final tail fixture shape/page closure")
    require(p["device"]["mr27"] == 69 and p["device"]["mr57"] == 3, "final tail RFM decrement/capability binding")
    # MR27 permits at most4*IMT debt; MR57=3 removes4*IMT in one RFM.
    r, t, d = p["ratio"], p["dram_ck"], p["dfi_wck"]

    def ceil(n, unit=r):
        return (n + unit - 1) // unit

    disable = ceil(p["wck"]["twck_dis"])
    physical = ceil(
        p["phy_half_wck"]["command"]
        + max(p["rl_ck"], p["wl_ck"]) * 2 * r
        + max(p["phy_half_wck"]["dq_input"], p["phy_half_wck"]["dq_output"])
        + 48
        + 2 * p["wck"]["twck_toggle_post"],
        2 * r,
    )
    # All16 banks, including a conservative full tRC/write recovery, close
    # one at a time; no row/physical/ECC flag is substituted for quiescent.
    residual = (
        physical + max(t["trc"], t["twr"], t["tras_min"]) + page_idle + 16 * t["tppd"] + t["trp_ab"] + disable + 8
    )
    returned = ceil(d["trddata_en"] + 7 + d["tphy_rdlat"])
    mrr_pin = ceil(
        p["phy_half_wck"]["command"]
        + p["rl_ck"] * 2 * r
        + p["phy_half_wck"]["dq_output"]
        + 16
        + 2 * p["wck"]["twck_toggle_post"],
        2 * r,
    )
    mr4 = max(returned, mrr_pin, t["tmrr"] + t["tmrw"]) + disable + 16
    # Full recoveries even for carry-in work. Eight edges per command cover
    # request selection, registered ownership/result and sample reductions.
    maintenance = 2 * (t["trfc_ab"] + 8) + t["trfm_ab"] + 8 + mr4 + t["tzqlat"] + 8
    total = 2 * (residual + maintenance) + 2
    admission = max(
        p["wl_ck"] + t["twr"] + 8 + t["trp_ab"] + t["trfc_ab"] + disable + 8,
        t["tras_min"] + t["trp_ab"] + t["trfc_ab"] + 8,
    )
    poll_lead = 2 * admission + mr4  # includes one RFM before coalesced REF
    renewal = min(t["trefi"] - admission, 3000 - poll_lead)
    require(total < renewal, "final tail bound cannot exclude recurring maintenance")
    require(total * p["ck_ps"] < 200_000_000, "final tail exceeds unchanged fixture deadline")
    return dict(
        page_idle_ck=page_idle,
        residual_ck=residual,
        mr4_ck=mr4,
        maintenance_ck=maintenance,
        total_ck=total,
        renewal_floor_ck=renewal,
    )


MANAGEMENT_FIELDS = (
    "reset_n memory_reset destructive fault owner state settled drained pin_drained ecc_drained "
    "phy_update master phyupd_req phymstr_req phyupd_type phymstr_type phymstr_cs_state "
    "phymstr_state_sel rank_valid rank_sr refresh_request refresh_done boot config training_epoch"
).split() + [k for k in RECOVERY_FIELDS if k not in ("boot", "config", "destructive")]
REGISTERED_FIELDS = "phy_update master owner state".split()


class ManagementWitness:
    """DFI5.0 Table17 and4.15: sample request low, then register ACK low.

    Falling samples observe the preceding NBA result. Rising observations
    independently retain the request consumed by that NBA. Fault/disconnect
    traces never establish a healthy return. Rank-report freshness is proved
    using consumed rising samples, not a transient between clock edges.
    """

    def __init__(self):
        self.edge = None
        self.last_sample = None
        self.low = self.fresh = False
        self.releases = 0
        self.request_fields = None
        self.previous_edge = None

    def sample(self, s):
        old = self.edge
        if self.last_sample is not None:
            require(
                old is not None and s["tick"] == old["tick"] + 1 and s["ps"] == old["ps"] + 625,
                "missing management request-consumption edge",
            )
        released = False
        healthy = old and all(
            x["reset_n"] and not any(x[k] for k in ("memory_reset", "destructive", "fault")) for x in (old, s)
        )
        if healthy:
            for ack, req in (("phy_update", "phyupd_req"), ("master", "phymstr_req")):
                if old[ack]:
                    require(s[ack] == old[req], "registered ACK release before withdrawal or missing release")
                    if not s[ack]:
                        if ack == "master":
                            require(
                                self.fresh
                                and old["rank_valid"] == 1
                                and old["rank_sr"] == 0
                                and old["refresh_request"] == 0,
                                "master handback lacks fresh sampled rank report/refresh settlement",
                            )
                        released = True
                        self.releases += 1
                elif s[ack]:
                    require(
                        old[req] and not old["phy_update"] and not old["master"], "ACK entry before sampled request"
                    )
                    require(
                        all(old[k] for k in ("settled", "drained", "pin_drained", "ecc_drained")),
                        "ACK entry before consumed drain",
                    )
        self.edge = None
        self.last_sample = s
        return released

    def consume(self, e):
        s = self.last_sample
        require(
            s is not None and self.edge is None and e["tick"] == s["tick"] and e["ps"] == s["ps"] + 625,
            "wrong/duplicate/reordered management edge",
        )
        require(all(e[k] == s[k] for k in REGISTERED_FIELDS), "management outputs changed before consuming edge")
        require(not (e["phy_update"] and e["master"]), "overlapping management ACKs")
        if e["phyupd_req"] or e["phymstr_req"]:
            fields = tuple(
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
            require(
                self.request_fields is None or fields == self.request_fields, "unstable consumed management request"
            )
            self.request_fields = fields
        elif not (e["phy_update"] or e["master"]):
            self.request_fields = None
        self.edge = e
        if e["master"] and not e["fault"]:
            require(
                not e["refresh_done"] or self.previous_edge and self.previous_edge["refresh_request"],
                "unsolicited sampled refresh completion",
            )
            if not e["rank_valid"]:
                self.low = True
            elif self.low and e["rank_valid"] == 1 and e["rank_sr"] == 0:
                self.fresh = True
        else:
            self.low = self.fresh = False
        self.previous_edge = e


def qualify_phases(records, header):
    require(
        header.get("sample_phase") == "negedge_after_prepare_before_sample"
        and header.get("return_source") == "memory_output_after_prepare"
        and header.get("handshake_phase") == "posedge_before_NBA"
        and header.get("management_phase") == "posedge_before_NBA"
        and header.get("version") in (6, 7)
        and header.get("recovery_phase") == RECOVERY_PHASE
        and header.get("maintenance_phase") == "falling_and_rising_before_NBA"
        and header.get("host_phase") == "posedge_before_NBA_next_edge_counter"
        and header.get("host_reset_phase") == "negedge_core_reset_before_NBA"
        and header["ck_ps"] == 1250,
        "unqualified wrapper observation phases",
    )
    samples, edges, management = {}, {}, {}
    sequence = defaultdict(list)
    for e in records:
        if e["kind"] in ("sample", "edge", "management_edge"):
            key = e["channel"], e["tick"]
            target = samples if e["kind"] == "sample" else edges if e["kind"] == "edge" else management
            sequence[e["channel"]].append((e["tick"], e["kind"]))
            require(key not in target, "duplicate phase sample")
            target[key] = e
    require(samples and samples.keys() == edges.keys(), "missing memory/edge phase tail")
    require(samples.keys() == management.keys(), "missing rising management witness; old trace unqualified")
    for key, s in samples.items():
        e, m = edges[key], management[key]
        require(m["ps"] == e["ps"], "management edge timestamp displacement")
        require(all(k in m for k in MANAGEMENT_FIELDS), "incomplete management witness")
        require(all(m[k] == s[k] for k in REGISTERED_FIELDS + RECOVERY_REGISTERED), "management edge output binding")
        require(e["ps"] - s["ps"] == header["ck_ps"] // 2, "memory/handshake phase displacement")
        require(all(number(e[k]) == number(s[k]) for k in BUSES), "raw buses changed across qualified phase")
        if key[1]:
            require(
                (key[0], key[1] - 1) in samples and s["ps"] - samples[key[0], key[1] - 1]["ps"] == header["ck_ps"],
                "missing CK sample",
            )
    for channel, seen in sequence.items():
        ticks = sum(k[0] == channel for k in samples)
        require(
            seen == [(t, kind) for t in range(ticks) for kind in ("sample", "edge", "management_edge")],
            "reordered phase/witness stream",
        )
    for kind, reference in (("window_sample", samples), ("window_edge", management)):
        witnesses = [e for e in records if e["kind"] == kind]
        require(len(witnesses) == len(reference), "missing/duplicate maintenance phase witness")
        require(
            [(e["channel"], e["tick"]) for e in witnesses] == list(reference), "reordered maintenance phase witness"
        )
        for w in witnesses:
            ref = reference[w["channel"], w["tick"]]
            require(
                w["ps"] == ref["ps"] and all(k in w for k in WINDOW_FIELDS),
                "incomplete/misphased maintenance phase witness",
            )
    window_samples = {(e["channel"], e["tick"]): e for e in records if e["kind"] == "window_sample"}
    for e in records:
        if e["kind"] == "window_edge":
            s = window_samples[e["channel"], e["tick"]]
            stable = (
                "life_fault_code life_first_fault master_pending master_age master_elapsed life_elapsed "
                "maint_ck running pending mr4_pending zq_pending thermal thermal_debt thermal_deadline "
                "last_refresh last_mr4 last_zq register_state register_owner register_io_state"
            ).split()
            require(all(e[k] == s[k] for k in stable), "maintenance registered phase binding")
    host = [e for e in records if e["kind"] == "host_edge"]
    ticks = sorted(t for channel, t in samples if channel == 0)
    require([e["tick"] for e in host] == ticks, "missing/duplicate/reordered host witness; old trace unqualified")
    for e in host:
        require(e["ps"] == edges[0, e["tick"]]["ps"], "host witness timestamp displacement")
        require(all(k in e for k in HOST_FIELDS), "incomplete host core witness")
        require([c["channel"] for c in e["channels"]] == [0, 1], "host witness lacks both channels")
    times = [e["ps"] for e in records]
    require(times == sorted(times), "reordered observation timestamps")
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
                if h.transactions[tag].done:
                    self.tags.pop((e["port"], e["id"], kind == "b"))
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


class Addresses:
    """Bind stalled packed descriptors to independent source-edge admissions."""

    def __init__(self):
        self.held = {}
        self.offers = {}
        self.seen = set()

    def sample(self, e, flags):
        key = e["port"], e["ps"]
        require(key not in self.seen and flags is not None and flags["ps"] == e["ps"], "address/source edge coverage")
        self.seen.add(key)
        for kind, vbit, rbit in (("aw", 9, 8), ("ar", 7, 6)):
            valid, ready = e[kind + "valid"], e[kind + "ready"]
            require(
                valid == (flags["flags"] >> vbit & 1) and ready == (flags["flags"] >> rbit & 1),
                "address/source VALID/READY mismatch",
            )
            identity = number(e[kind + "_bits"]), e[kind + "_id"], number(e[kind + "_address"])
            owner = e["port"], kind
            require(
                owner not in self.held or valid and self.held[owner] == identity, "pending address withdrawn/changed"
            )
            if valid and not ready:
                self.held[owner] = identity
            else:
                self.held.pop(owner, None)
            if valid and ready:
                self.offers[e["port"], kind, e["ps"]] = identity

    def accept(self, e):
        value = self.offers.pop((e["port"], e["kind"], e["ps"]), None)
        require(
            value is not None and value[1:] == (e["id"], number(e["address"])), "unbound/duplicate address handshake"
        )


class Run:
    def __init__(self, p, selected_cases=None, records=None):
        self.p = p
        self.addresses = Addresses()
        self.source = Source()
        self.physical = [None, None]
        self.inline = [InlineDrain(), InlineDrain()]
        self.tags = [None, None]
        self.q = [None, None]
        self.last = [None, None]
        self.cancelled = []
        self.case = None
        self.cases = []
        self.management = [ManagementWitness(), ManagementWitness()]
        self.boot = 0
        self.profile_image = None
        self.closure_edges = defaultdict(int)
        self.host_completions = HostCompletions()
        self.recovery_channels = set()
        self.recovery = RecoveryConsumption(ck_ps=p["ck_ps"])
        self.window = MaintenanceWindow(p)
        self.selected_cases = list(range(4)) if selected_cases is None else list(selected_cases)
        self.records = [] if records is None else records
        self.pins = (
            PhysicalCommands(self.records, p) if self.records and any(c >= 4 for c in self.selected_cases) else None
        )
        self.timeout = RfmTimeout(p["management_ck"]["tphymstr_resp"])
        self.window_edges = {(e["channel"], e["tick"]): e for e in self.records if e["kind"] == "window_edge"}
        self.window_samples = {(e["channel"], e["tick"]): e for e in self.records if e["kind"] == "window_sample"}
        self.host_settlement = None
        self.fatal_propagation = None

    def marker(self, e):
        name = e["name"]
        if name == "begin":
            require(self.case is None, "overlapping wrapper cases")
            self.case = dict(id=e["case"], failed=e["failed"], begin=e["ps"], entries=0, reset=False, fresh=False)
            self.host_completions.begin(e["case"], e["failed"])
            self.recovery_channels.clear()
            self.recovery.begin()
        elif name == "maintenance_ready":
            require(self.case is not None and "readiness" not in self.case, "duplicate/outside-case readiness")
            self.case["readiness"] = self.window.begin(e["ps"], self.physical, self.source.settled())
        elif name == "recovery_baseline":
            self.recovery.require_complete()
            self.host_completions.baseline(e, self.last, self.source.settled(), self.recovery_channels)
            self.case["baseline_ps"] = e["ps"]
        elif name == "extension_setup":
            require(self.case and self.case["id"] in (4, 5), "unexpected extended setup")
            # Keep every original reserved-window assertion through the dirty
            # setup, then end that reservation before natural ACT/REF work.
            self.window.request(e["ps"], self.physical)
        elif name in ("rfm_request", "active_request"):
            c = self.case
            require(c is not None and "request" not in c, "duplicate/outside-case master request")
            require(c["id"] == (4 if name == "rfm_request" else 5), "wrong extended master case")
            if c["id"] == 4:
                require(self.physical[0].phy.dram.maintenance.raa[0][0] >= 63, "RFM request without decoded ACT debt")
                w = self.window_samples[0, self.last[0]["tick"]]
                require(w["maintenance_valid"] and w["maintenance_rfm"], "RFM request outside actual pending entry")
                require(
                    any(
                        not t.done and not t.physical and not t.write and t.address.port == 0
                        for t in self.source.host.transactions.values()
                    ),
                    "RFM request without pending AXI read",
                )
                require(
                    (1, "b") in self.source.held and (1, "r") in self.source.held,
                    "RFM timeout lacks exposed B/R backpressure",
                )
                require(
                    any(t.write and len(t.w) < len(t.ranges) for t in self.source.host.transactions.values()),
                    "RFM timeout lacks real trailing W",
                )
                c["outstanding_at_request"] = [
                    dict(
                        tag=tag,
                        port=t.address.port,
                        id=t.address.id,
                        write=t.write,
                        accepted_w=len(t.w),
                        consumed_r=t.response_index,
                    )
                    for tag, t in self.source.host.transactions.items()
                    if not t.done
                ]
            else:
                require(
                    self.source.settled() and all(p.drained() for p in self.physical),
                    "active REF entry before real source/physical settlement",
                )
            c.update(request=e["ps"], closure_probes={0: 0, 1: 0})
            self.closure_edges.clear()
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
            self.window.request(e["ps"], self.physical)
            c.update(request=e["ps"], owed=owed)
            c["schedule"] = remaining_budget(self.p)
            c["closure_probes"] = {0: 0, 1: 0}
            self.closure_edges.clear()
        elif name == "error":
            require(
                self.case["failed"] and (self.inline[0].dirty or self.case["id"] == 4), "error outside selected failure"
            )
            if self.case["id"] == 4:
                require(self.timeout.fatal is not None, "RFM marker without actual channel timeout")
            self.case["error"] = e["ps"]
            # The natural channel timeout reaches the coordinator/host on
            # its observed registered path. Preserve responses exposed in
            # that intervening interval; the host witness applies fatal.
            if self.case["id"] != 4:
                self.source.host.fail()
        elif name == "stale_confirmation_rejected":
            require(
                self.source.settled() and all(s and s["reset_request"] and s["boot"] == self.boot for s in self.last),
                "stale recovery accepted",
            )
            self.case["stale_rejected"] = True
        elif name == "fresh_service":
            self.host_completions.checkpoint(e)
            require(
                self.source.settled()
                and not self.source.host.faulted
                and all(s and s["enabled"] and s["trusted"] and s["trained"] and s["config_valid"] for s in self.last),
                "fresh service lacks settlement/trust",
            )
            self.case["fresh"] = True
            self.case["fresh_ps"] = e["ps"]
        elif name == "preserved_service":
            require(self.case and self.case["id"] == 5 and self.case.get("handback"), "preservation before PHY return")
            # Both original images must have passed physical/inline and AXI
            # replay since return; a newly written replacement is insufficient.
            for port, address in ((0, 0), (1, 64)):
                require(
                    any(
                        t.done
                        and not t.write
                        and t.address.port == port
                        and t.address.address == address
                        and t.address.id == 20 + 20 * 5 + (9 if port == 0 else 8)
                        for t in self.source.host.transactions.values()
                    ),
                    "missing preserved AXI image",
                )
            self.case["preserved"] = True
        elif name == "probes_accepted":
            require(
                all(
                    self.case.get("probe_handshakes", {}).get(f"{p}{kind}") == 1
                    for p in range(2)
                    for kind in ("aw", "ar")
                ),
                "probe W before exactly one renewed-service address handshake",
            )
            self.case["probes_accepted"] = True
        elif name == "end_case":
            c = self.case
            require(c["fresh"] and self.source.settled(), "missing fresh service/complete W/R/B tail")
            require(
                0 <= e["ps"] - c["fresh_ps"] <= final_tail_budget(self.p)["total_ck"] * self.p["ck_ps"],
                "fresh service final tail timeout",
            )
            require(
                all(s and s["drained"] for s in self.last)
                and all(p is not None and p.drained() for p in self.physical)
                and all(i.live is None for i in self.inline),
                "fresh service lacks all-owner/independent final drain",
            )
            require(c["entries"] == (0 if c["failed"] else 1), "missing or premature ownership outcome")
            require(not c["failed"] or c["reset"] and c.get("stale_rejected"), "missing confirmed recovery")
            require(not c["failed"] or self.recovery_channels == {0, 1}, "missing both-channel recovery tail")
            require(c["failed"] or "ack_ps" in c and c.get("handback"), "missing ACK/withdrawal/handback")
            require(c["id"] != 5 or c.get("preserved"), "missing physical-memory preservation after PHY REF")
            require(all(c.get("closed", {}).get(p, 0) > 0 for p in range(2)), "missing source closure observations")
            require(all(c["closure_probes"][p] > 0 for p in range(2)), "missing active AW/AR closure probes")
            require(c.get("probes_accepted"), "missing actual probe admission before W")
            self.host_completions.checkpoint(e)
            self.cases.append(c)
            self.case = None

    def sample(self, e):
        channel = e["channel"]
        old = self.last[channel]
        self.last[channel] = e
        released = self.management[channel].sample(e)
        if released and channel == 0 and self.case is not None:
            self.case["handback"] = True
        if self.profile_image is None:
            self.profile_image = e["profile_image"]
        require(e["profile_image"] == self.profile_image, "unbound profile mutation")
        c = self.case
        if c and c["id"] == 4 and channel == 0 and "request" in c and not c.get("reset"):
            require(not e["error"] and not e["master"], "RFM containment/external error")
            if e["fault"] and self.timeout.fatal is None:
                self.timeout.failed(e, self.window_samples[0, e["tick"]])
        transition = self.recovery.sample(e, self.source.settled(), bool(c and c["failed"]))
        self.recovery_channels = self.recovery.completed
        if channel == 0:
            # While admission is open the remote empty reduction may still
            # describe the edge before a new source acceptance crosses CDC.
            # It cannot authorize management entry: entry always checks the
            # independent ledger, and closed admission must settle for real.
            if e["settled"] and not e["enabled"]:
                require(self.source.settled(), "internal settlement before actual W/R/B ledger")
            if e["reset_request"]:
                require(self.source.settled(), "destructive recovery before source settlement")
                if c and c["id"] == 4:
                    require(self.host_settlement is not None, "missing host/bridge settlement witness")
                    require(self.host_settlement["tick"] == e["tick"] - 1, "stale host/bridge settlement witness")
                    settled_abort(self.host_settlement, self.source.settled())
            if transition:
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
                require(c and c["failed"], "unexpected destructive recovery")
                c["reset"] = True
            if transition:
                self.boot = e["boot"]
        reset = not e["reset_n"] or e["memory_reset"] or e["destructive"]
        if reset:
            self.window.observe(e, None)
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
        was_drained = physical.drained()
        command, pin = self.pins.command(e) if self.pins else (None, None)
        before_raa = list(physical.phy.dram.maintenance.raa[0])
        physical.sample(e | {"time_ps": e["ps"]}, command=command)
        if pin:
            self.pins.applied_ref(pin, physical, before_raa)
        self.window.observe(e, physical)
        if channel != 0 or c is None or "request" not in c or c.get("reset"):
            return
        active = e["phy_update"] or e["master"] or e["owner"] == 1
        was_active = old and (old["phy_update"] or old["master"] or old["owner"] == 1)
        if (e["phy_update"] or e["master"]) and not e["fault"]:
            selected = "phyupd_req" if c["id"] < 2 else "phymstr_req"
            ack = "phy_update" if c["id"] < 2 else "master"
            require(not c["failed"] and e[ack] and (e[selected] or old and old[ack]), "unsolicited/wrong ACK")
            c.setdefault("ack_ps", e["ps"])
        if active and not e["fault"] and not was_active:
            require(not c["failed"] and self.source.settled() and e["settled"], "ownership before source settlement")
            require(
                (was_drained if c["id"] == 5 else physical.drained()) and self.inline[0].live is None,
                "ownership before independent physical/row/WCK tail",
            )
            require(
                e["ps"] - c["request"]
                <= min(remaining_budget(self.p)["response_limits_ck"].values()) * self.p["ck_ps"],
                "management response deadline",
            )
            require(
                e["ps"] - c["request"]
                <= (
                    self.p["management_ck"]["tphymstr_resp"]
                    if c["id"] == 5
                    else c.get("schedule", remaining_budget(self.p))["total_ck"]
                )
                * self.p["ck_ps"],
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
        if active and not e["fault"]:
            require(
                not e["cs"] and not number(e["write"]) >> 24 & 255 and not number(e["read"]) >> 16,
                "controller traffic during PHY ownership",
            )

    def event(self, e):
        kind = e["kind"]
        if kind == "address":
            self.addresses.sample(e, self.source.flags.get(e["port"]))
        elif kind in ("axi", "aw", "ar", "w", "b", "r"):
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
            if kind in ("aw", "ar"):
                self.addresses.accept(e)
                if c and "request" in c and e["id"] == 20 + 20 * c["id"] + 12 + 2 * e["port"] + (kind == "ar"):
                    require(
                        self.last[0]["enabled"] and (c.get("handback") or c.get("reset")),
                        "probe admission before renewed service",
                    )
                    require(
                        not c["failed"] or c.get("baseline_ps", e["ps"]) < e["ps"],
                        "probe admission before recovery baseline",
                    )
                    counts = c.setdefault("probe_handshakes", {})
                    key = f"{e['port']}{kind}"
                    counts[key] = counts.get(key, 0) + 1
                    require(counts[key] == 1, "duplicate probe admission")
            if c and kind == "w" and self.source.host.aw[e["port"]]:
                original = self.source.host.transactions[self.source.host.aw[e["port"]][0]].address
                if original.id == 20 + 20 * c["id"] + 12 + 2 * e["port"]:
                    require(c.get("probes_accepted"), "probe W before address admission witness")
            self.source.event(e)
        elif kind == "sample":
            self.sample(e)
        elif kind == "management_edge":
            if (
                self.case
                and self.case["id"] == 4
                and e["channel"] == 0
                and "request" in self.case
                and self.timeout.decision is None
            ):
                self.timeout.edge(e, self.window_edges[0, e["tick"]])
            self.recovery.consume(e, self.source.settled())
            self.management[e["channel"]].consume(e)
        elif kind == "window_edge":
            self.window.diagnostic(e)
        elif kind == "window_sample":
            pass  # Full phase/field coverage is checked before any replay.
        elif kind == "host_edge":
            self.host_completions.edge(e)
        elif kind == "host_reset":
            self.host_completions.reset(e)
        elif kind == "host_settlement":
            self.host_settlement = e
            if (
                self.case
                and self.case["id"] == 4
                and self.timeout.fatal is not None
                and e["fatal"]
                and self.fatal_propagation is None
            ):
                require(e["tick"] == self.timeout.fatal + 1, "channel-to-coordinator-to-host fatal propagation")
                self.fatal_propagation = e["tick"]
                self.source.host.fail()
        elif kind == "marker":
            self.marker(e)
        elif kind.startswith("inline_"):
            if kind in ("inline_admit", "inline_complete"):
                self.host_completions.seam(e)
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
                self.window.physical_transfer()
                self.physical[channel].expect(e)
            elif kind == "inline_result":
                self.physical[channel].result(e)
            if kind == "inline_complete" and not e["failed"]:
                self.source.completed(self.tags[channel], self.q[channel], e, self.inline[channel].live["expected"])
            self.inline[channel].event(e)

    def finish(self):
        require(
            self.case is None and [c["id"] for c in self.cases] == self.selected_cases, "missing selected wrapper tail"
        )
        self.source.host.finish()
        require(not self.addresses.held and not self.addresses.offers, "unconsumed pending address tail")
        require(all(c["entries"] == (not c["failed"]) for c in self.cases), "missing management entries")
        if 4 in self.selected_cases:
            require(self.fatal_propagation is not None, "missing actual host fatal propagation")
        return dict(
            recovery_consumptions=self.recovery.finish(),
            cases=self.cases,
            source=dict(self.source.counts),
            retired_epochs=self.source.retired_epochs,
            cancelled=self.cancelled,
            host_completions=self.host_completions.finish(),
            physical=[p.summary() for p in self.physical],
            profile_sha256=digest(self.p),
            schedule=remaining_budget(self.p),
            maintenance_window_budget=window_budget(self.p),
            final_tail_budget=final_tail_budget(self.p),
            rfm_timeout=self.timeout.finish() if 4 in self.selected_cases else None,
            fatal_propagation=self.fatal_propagation,
            active_refresh=active_refresh(self.records, self.p, self.pins.applied)
            if 5 in self.selected_cases
            else None,
            limitations=[
                "No trailing-W-at-request successful ownership-entry qualification.",
                *(
                    ["Historical short master window has no active PHY refresh service."]
                    if self.selected_cases == list(range(4))
                    else []
                ),
                "Selected configuration only; no two-rank/FSP/power or forwarding/merging qualification.",
            ],
        )


def qualify_address_coverage(records, source):
    """Every observed AXI cycle needs its actual address-bus witness, even idle."""
    address_edges = [(e["port"], e["ps"]) for e in records if e["kind"] == "address"]
    source_edges = [(e["port"], e["ps"]) for e in source if e["kind"] == "axi"]
    require(
        len(address_edges) == len(set(address_edges))
        and len(source_edges) == len(set(source_edges))
        and set(address_edges) == set(source_edges),
        "address/source edge coverage",
    )
    return len(address_edges)


def check(wrapper_path, source_path):
    records = [json.loads(line) for line in Path(wrapper_path).read_text().splitlines()]
    source = [json.loads(line) for line in Path(source_path).read_text().splitlines()]
    header, p = records[0], profile_binding()
    require(
        header["kind"] == "header" and header["version"] in (6, 7) and header["profile_sha256"] == digest(p),
        "wrapper profile binding",
    )
    require(records[-1]["kind"] == "end" and source[-1]["kind"] == "end", "missing wrapper/source terminal tail")
    phases = qualify_phases(records[1:-1], header)
    address_samples = qualify_address_coverage(records, source)
    # CK model samples before the later edge. All actual internal events carry
    # edge timestamps; source admission precedes owner acceptance at a tie.
    order = {
        "sample": -5,
        "window_sample": -4,
        "window_edge": -2,
        "host_edge": -3,
        "host_reset": -3,
        "management_edge": -2,
        "marker": -1,
        "axi": 0,
        "address": 1,
        "aw": 2,
        "ar": 3,
        "w": 4,
        "inline_admit": 5,
        "inline_physical": 6,
        "inline_result": 7,
        "inline_complete": 8,
        "b": 9,
        "r": 10,
    }
    events = [e for e in records[1:-1] if e["kind"] != "edge"]
    events.extend(e for e in source[1:-1] if e["kind"] != "phy")
    events.sort(key=lambda e: (e["ps"], order.get(e["kind"], -1), e.get("port", e.get("channel", 0))))
    selected = header.get("selected_case", -1)
    require(selected in (-1, 4, 5), "unqualified wrapper case selection")
    run = Run(p, selected_cases=None if selected == -1 else [selected], records=records)
    for e in events:
        run.event(e)
    return run.finish() | {
        "phase_samples": phases,
        "address_samples": address_samples,
        "raw_sha256": hashlib.sha256(Path(wrapper_path).read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(Path(source_path).read_bytes()).hexdigest(),
    }
