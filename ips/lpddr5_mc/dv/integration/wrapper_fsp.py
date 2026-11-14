"""Actual-wrapper constant-CK all-channel FSP publication and data replay.

Keeps the existing source, host seam, inline ECC and physical owners. Separate
sampled APB/CK ACKs bind a frozen image to the twenty actual MRW pairs and
commit. A profile changes on the persistent physical owner at actual COMMIT.
"""

import copy
import hashlib
import json
import math
import re
import zlib
from pathlib import Path

from contracts.dvfs import Sample, check_dvfs
from system.host import require
from system.trace import number
from wrapper_fsp_profiles import ProfilePublication, decoded_program, expected_program
from wrapper_management import IP, Run, digest, profile_binding, qualify_address_coverage, qualify_phases
from wrapper_master import PhysicalCommands

REGISTERS = json.loads((IP / "config/contracts/registers.json").read_text())["registers"]
INDEX = {r["name"]: i for i, r in enumerate(REGISTERS)}
OFFSETS = {r["name"]: r["offset"] for r in REGISTERS}
PCLK_PS = 7000
FSP_FIELDS = (
    "start complete ratio frequency fsp clock_done wck_disabled ready declined fault prepared prepared_epoch "
    "epoch boot active_image prepared_image memory_image mb_prepare mb_commit mb_abort mb_epoch mb_crc "
    "mb_dest config_crc config_image target_fsp"
).split()


def target_profile():
    p = copy.deepcopy(json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"][24])
    p["ranks"], p["device"]["mr57"] = 1, 3
    return p


def profile_bits(p):
    """Pack the actual typed profile layout from independently admitted fields.

    Read only the public type declaration, never generated HDL vectors or the
    producer's claimed profile digest. An unrecognized field fails closed.
    """
    source = (IP / "rtl/channel/lpddr5_mc_channel_pkg.sv").read_text()
    layout = source.split("typedef struct packed {", 1)[1].split("} lpddr5_mc_profile_t;", 1)[0]
    value = 0
    for declaration in layout.split(";"):
        declaration = declaration.strip()
        if not declaration:
            continue
        match = re.fullmatch(r"logic\s*((?:\[\d+:0\]\s*)*)([\w, ]+)", declaration)
        require(match is not None, "unknown profile layout declaration")
        dimensions, names = match.groups()
        width = math.prod(int(n) + 1 for n in re.findall(r"\[(\d+):0\]", dimensions))
        for name in (n.strip() for n in names.split(",")):
            if name == "mr":
                field = sum(v << (8 * int(k)) for k, v in p["mr"].items())
            elif name in ("mr8", "mr27", "mr57", "raw_parity"):
                field = p["device"][name]
            elif name.startswith("device_"):
                field = p["device"][name.removeprefix("device_")]
            else:
                groups = [
                    g
                    for g in ("wck", "phy_half_wck", "dfi_wck", "management_ck", "dram_ck")
                    if name.startswith(g + "_")
                ]
                field = p[groups[0]][name.removeprefix(groups[0] + "_")] if groups else p[name]
            require(0 <= int(field) < 1 << width, "profile field width")
            value = (value << width) | int(field)
    return value


def image_crc(raw):
    raw = number(raw)
    require(0 <= raw < 1 << (32 * len(REGISTERS)), "APB image width")
    return zlib.crc32(raw.to_bytes(4 * len(REGISTERS), "little"))


def target_slot(raw):
    return number(raw) >> (32 * INDEX["DVFS"]) & 3


def qualify_fsp(records, binding):
    require(
        binding.get("phase") == "posedge_before_NBA"
        and binding.get("apb_phase") == "pclk_posedge_before_NBA"
        and binding.get("register_count") == len(REGISTERS),
        "unqualified FSP producer phases/layout",
    )
    reference = {(e["channel"], e["tick"]): e for e in records if e["kind"] == "management_edge"}
    witnesses = [e for e in records if e["kind"] == "fsp_edge"]
    require(len(witnesses) == len(reference) and witnesses, "missing/duplicate FSP edge")
    for channel in (0, 1):
        selected = [e for e in witnesses if e["channel"] == channel]
        require([e["tick"] for e in selected] == list(range(len(selected))), "reordered FSP edge")
    for e in witnesses:
        key = e["channel"], e["tick"]
        require(key in reference and e["ps"] == reference[key]["ps"], "FSP witness phase")
        require(all(k in e for k in FSP_FIELDS), "incomplete FSP edge")
        require(
            e["boot"] == reference[key]["boot"] and e["epoch"] == reference[key]["config"], "FSP epoch phase binding"
        )
    apb = [e for e in records if e["kind"] == "fsp_apb"]
    require(
        apb and all(b["ps"] - a["ps"] == PCLK_PS for a, b in zip(apb, apb[1:], strict=False)),
        "missing/duplicate PCLK witness",
    )
    for e in records:
        if e["kind"] in ("fsp_transfer", "fsp_ack"):
            key = e["channel"], e["tick"]
            require(key in reference and e["ps"] == reference[key]["ps"], "FSP transfer phase")
        elif e["kind"] in ("fsp_freeze", "fsp_apb_ack"):
            require(e["ps"] in {a["ps"] for a in apb}, "FSP APB transfer phase")
    return len(witnesses)


class PublicationWitness:
    """Frozen image/CRC and exactly-once ACKs across both real clock domains."""

    def __init__(self, epoch, *, failed_case=False):
        self.epoch = epoch
        self.failed_case = failed_case
        self.failure = None
        self.recover_requested = False
        self.active = None
        self.history = []
        self.rejected = 0
        self.apb = None
        self.rejection_tail = None

    def observe_apb(self, e, source_settled):
        if self.rejection_tail is not None:
            require(
                tuple(e[k] for k in ("sequence", "epoch", "valid", "busy")) == self.rejection_tail,
                "rejected APB request mutated sequence/image/state on next edge",
            )
            self.rejection_tail = None
        self.apb = e
        if (
            self.failed_case
            and e["psel"]
            and e["penable"]
            and e["pready"]
            and e["write"]
            and e["address"] == OFFSETS["CONTROL"]
            and e["data"] & 8
        ):
            self.require_failed()
            require(
                not self.recover_requested and not e["error"] and source_settled and not e["busy"] and not e["valid"],
                "RECOVER before failed publication settled idle/invalid",
            )
            self.recover_requested = True
        publication = (
            e["psel"]
            and e["penable"]
            and e["pready"]
            and e["write"]
            and (
                e["address"] == OFFSETS["CONFIG_COMMIT"]
                and e["data"] & 1
                or e["address"] == OFFSETS["DVFS"]
                and e["data"] & 256
            )
        )
        if publication:
            if e["error"]:
                require(not source_settled and not e["settled"], "dirty rejection without independent source work")
                require(not e["busy"] and e["epoch"] == self.epoch, "rejected request changed publication")
                self.rejected += 1
                self.rejection_tail = tuple(e[k] for k in ("sequence", "epoch", "valid", "busy"))
            else:
                require(
                    source_settled and e["settled"] and (not e["enabled"] or e["quiesced"]),
                    "FSP publication before actual source/crossed QUIESCE",
                )
                require(self.active is None, "overlapping publication")
                require(self.failure is None, "new publication before destructive recovery")
                self.active = dict(
                    sequence=e["sequence"] + 1,
                    boot=e["boot"],
                    frozen={},
                    transfers=set(),
                    ck_acks={},
                    apb_acks={},
                    commits=set(),
                    faults=set(),
                    aborts=set(),
                    target=None,
                )
            return
        if self.failed_case and (self.active is not None or self.failure is not None):
            require(not e["valid"] and e["epoch"] == self.epoch, "failed FSP changed the published image/epoch")
            if self.active is not None:
                t = self.active
                require(e["sequence"] == t["sequence"], "failed FSP changed its publication sequence")
                if not e["busy"]:
                    expected = {(c, phase): int(phase == 2) for c in (0, 1) for phase in (0, 2)}
                    require(
                        t["faults"] == t["aborts"] == {0, 1}
                        and t["transfers"] == set(expected)
                        and t["ck_acks"] == t["apb_acks"] == expected
                        and not t["commits"],
                        "failed FSP lacks both negative PREPARE ACKs and consumed/crossed ABORT ACKs",
                    )
                    self.failure = t | {"outcome": "failed"}
                    self.history.append(self.failure)
                    self.active = None
            return
        if e["valid"] and self.active and e["epoch"] == self.active["sequence"]:
            t = self.active
            require(
                t["apb_acks"].keys() == {(c, phase) for c in (0, 1) for phase in (0, 1)},
                "APB image before both commit ACKs",
            )
            require(all(t["apb_acks"].values()) and t["commits"] == {0, 1}, "APB image after declined/partial commit")
            self.epoch = e["epoch"]
            self.history.append(t | {"outcome": "published"})
            self.active = None
        elif e["valid"] and self.active and not e["busy"]:
            t = self.active
            require(
                e["epoch"] == self.epoch and t["apb_acks"] == {(0, 3): 0, (1, 3): 0} and not t["commits"],
                "non-unanimous decline or changed retained epoch",
            )
            self.history.append(t | {"outcome": "declined"})
            self.active = None

    def identity(self, e):
        t = self.active
        require(
            t is not None and e["sequence"] == t["sequence"] and e["boot"] == t["boot"] and e["channel"] in (0, 1),
            "stale/outside publication identity",
        )
        return t

    def freeze(self, e):
        t = self.identity(e)
        require(
            self.apb and e["ps"] == self.apb["ps"] and e["channel"] not in t["frozen"], "unbound/duplicate image freeze"
        )
        slot = target_slot(e["image"])
        require(slot in (1, 2) and (t["target"] is None or slot == t["target"]), "wrong/mixed target slot")
        t["target"] = slot
        t["frozen"][e["channel"]] = number(e["image"])

    def transfer(self, e):
        t = self.identity(e)
        c = e["channel"]
        require(
            e["prepare"] + e["commit"] + e["abort"] == 1 and e["dest"] == 3,
            "FSP payload phase/destinations",
        )
        phase = 2 if e["abort"] else int(bool(e["commit"]))
        require(
            c in t["frozen"] and number(e["image"]) == t["frozen"][c] and e["crc"] == image_crc(e["image"]),
            "changed frozen payload/CRC",
        )
        require((c, phase) not in t["transfers"], "duplicate configuration transfer")
        if phase == 2:
            require(
                self.failed_case and any(t["apb_acks"].get((channel, 0)) == 0 for channel in (0, 1)),
                "ABORT without a crossed selected negative PREPARE ACK",
            )
        elif phase == 1:
            require(not self.failed_case and not t["faults"], "COMMIT in selected failed FSP publication")
            require(
                all(t["apb_acks"].get((channel, 0)) == 1 for channel in (0, 1)),
                "commit payload before all prepare ACKs",
            )
        t["transfers"].add((c, phase))

    def timed_out(self, e):
        """Bind the independently checked DFI/pin timeout to its frozen prepare."""
        t = self.identity(e | {"sequence": e["prepared_epoch"]})
        c = e["channel"]
        require(
            self.failed_case
            and e["fault"]
            and e["prepared"]
            and not e["ready"]
            and (c, 0) in t["transfers"]
            and not t["commits"]
            and c not in t["faults"],
            "unbound/duplicate selected FSP timeout",
        )
        t["faults"].add(c)

    def abort(self, e):
        t = self.identity(e | {"sequence": e["mb_epoch"]})
        c = e["channel"]
        require(
            (c, 2) in t["transfers"]
            and c not in t["aborts"]
            and e["mb_dest"] == 1 << c
            and e["mb_crc"] == e["config_crc"],
            "unbound/duplicate mailbox ABORT or changed identity/CRC/destination",
        )
        t["aborts"].add(c)

    def require_failed(self):
        require(
            self.failed_case and self.failure is not None and self.active is None, "incomplete failed FSP publication"
        )

    def ack(self, e, *, apb=False):
        t = self.identity(e)
        c, phase = e["channel"], e["phase"]
        key = c, phase
        rejected_prepare = phase == 0 and e["accepted"] == 0
        require(
            phase in (0, 1, 2, 3) and e["accepted"] in (0, 1) and (rejected_prepare or e["accepted"] == (phase != 3)),
            "invalid FSP ACK outcome",
        )
        if rejected_prepare:
            require(self.failed_case and c in t["faults"], "negative PREPARE ACK without sampled selected timeout")
        elif phase == 2:
            require(self.failed_case and c in t["aborts"], "ABORT ACK before actual mailbox consumption")
        else:
            require(not self.failed_case, "successful/declined ACK in selected stuck-return publication")
        require((c, 0 if phase == 3 else phase) in t["transfers"], "ACK without configuration transfer")
        target = t["apb_acks"] if apb else t["ck_acks"]
        require(key not in target, "duplicate FSP ACK")
        if apb:
            require(t["ck_acks"].get(key) == e["accepted"], "APB ACK differs from consumed CK ACK")
        target[key] = e["accepted"]

    def commit(self, e):
        t = self.active
        c = e["channel"]
        require(
            not self.failed_case and t is not None and all(t["apb_acks"].get((channel, 0)) == 1 for channel in (0, 1)),
            "mailbox commit before all prepare ACKs",
        )
        require((c, 1) in t["transfers"] and c not in t["commits"], "duplicate/unreceived mailbox commit")
        require(
            e["mb_epoch"] == t["sequence"]
            and e["boot"] == t["boot"]
            and e["mb_dest"] == 1 << c
            and e["mb_crc"] == e["config_crc"],
            "mailbox commit identity/CRC/destination",
        )
        t["commits"].add(c)


class FspRun(Run):
    def __init__(self, p, q, binding, records):
        super().__init__(p, selected_cases=[], records=records)
        self.pins = PhysicalCommands(records, p)
        self.profiles = [p, q, p]
        self.images = [profile_bits(p), profile_bits(q), profile_bits(p)]
        require(
            [number(binding[k]) for k in ("initial_image", "target_image", "return_image")] == self.images,
            "producer descriptors differ from admitted profile fields",
        )
        self.selection = binding["case"]
        self.active = False
        self.publication = None
        self.owner = None
        self.origins = [None, None]
        self.controls = [[], []]
        self.pin_samples = {(e["channel"], e["tick"]): e for e in records if e["kind"] == "sample"}
        self.fsp_edges = [None, None]
        self.committed = [0, 0]
        self.markers = []
        self.faults = set()
        self.failure_seen = False
        self.failure_pending = None
        self.decline_pending = None
        self.decline_ack_ps = {}
        self.baseline = False
        self.finished = False
        self.host_edge = None

    def validate_profile(self, e):
        c = e["channel"]
        slot = self.committed[c]
        require(number(e["profile_image"]) == self.images[slot], "unbound active FSP memory profile")

    def recovery_allowed(self):
        return (
            self.selection == 2
            and self.failure_seen
            and self.failure_pending is None
            and self.publication.failure is not None
            and self.publication.recover_requested
        )

    def record_recovery(self, e):
        require(self.recovery_allowed(), "FSP reset without actual selected fault")
        self.publication.require_failed()
        require(
            self.owner is not None and self.owner.transaction is not None, "failed physical owner lost before reset"
        )
        self.owner = None
        self.committed = [0, 0]
        self.origins = [None, None]

    def sample(self, e):
        c = e["channel"]
        previous = self.physical[c]
        super().sample(e)
        if previous is None and self.physical[c] is not None:
            self.origins[c] = e["tick"]
        edge = self.fsp_edges[c]
        if edge and e["config_valid"] and not e["destructive"]:
            require(
                number(edge["active_image"]) == self.images[self.committed[c]] or edge["mb_commit"],
                "active image outside observed commit",
            )

    def trace(self, c):
        trace, pins = [], []
        for e in self.controls[c]:
            require(e["ratio"] in (1, 2), "unsupported DFI ratio wire encoding")
            trace.append(
                Sample(
                    bool(e["start"]),
                    bool(e["complete"]),
                    e["frequency"],
                    {1: 2, 2: 4}[e["ratio"]],
                    e["fsp"],
                    bool(e["clock_done"]),
                    bool(e["wck_disabled"]),
                )
            )
            pins.append(self.pin_samples[c, e["tick"]] | {"tick": e["tick"] - self.origins[c]})
        return trace, pins

    def return_ready(self, c, e):
        require(
            self.owner is not None and self.publication.active is not None,
            "FSP ACK without persistent publication owner",
        )
        edge = self.fsp_edges[c]
        if e["phase"] == 0:
            require(
                edge["ready"]
                and edge["prepared"]
                and edge["prepared_epoch"] == e["sequence"]
                and number(edge["prepared_image"]) == self.images[self.publication.active["target"]],
                "accepted ACK before actual target readiness/frozen prepared identity",
            )
        else:
            require(
                not edge["prepared"] and any(item["declined"] for item in self.controls[c]),
                "decline ACK without actual decline/prepared discard",
            )
        trace, pins = self.trace(c)
        self.owner.returned(c, trace, pins, sequence=e["sequence"], boot=e["boot"])

    def edge(self, e):
        c = e["channel"]
        self.fsp_edges[c] = e
        if not self.active or self.baseline:
            return
        if e["start"] or self.controls[c]:
            self.controls[c].append(e)
        if e["mb_prepare"] and self.publication.active:
            slot = self.publication.active["target"]
            require(
                number(e["config_image"]) == self.images[slot], "prepared decoder image differs from immutable target"
            )
        if e["mb_commit"]:
            self.publication.commit(e)
            t = self.publication.active
            require(number(e["config_image"]) == self.images[t["target"]], "commit changed frozen target")
            self.owner.commit(c, sequence=t["sequence"], boot=t["boot"])
            self.committed[c] = t["target"]
        if e["mb_abort"]:
            self.publication.abort(e)
        if e["fault"] and c not in self.faults:
            require(self.selection == 2 and self.owner is not None, "unexpected FSP engine fault")
            trace, pins = self.trace(c)
            p, q = self.owner.profiles[c], self.owner.transaction["targets"][c]
            result = check_dvfs(
                p["management_ck"]["tinit_start"], p["management_ck"]["tinit_complete"], trace, drained=True
            )
            require(result.disposition == "completion_timeout", "fault lacks sampled stuck-return timeout")
            release = next(i for i, item in enumerate(trace) if not item.start)
            require(
                len(trace) - 1 - release == p["management_ck"]["tinit_complete"] + 1,
                "FSP timeout is not the first overdue sampled return edge",
            )
            require(
                all(
                    (item.frequency, item.ratio, item.fsp)
                    == (0 if q["ratio"] == 2 else 1, q["ratio"], self.owner.transaction["target_fsp"])
                    for item in trace
                ),
                "failed FSP requested different frozen target DFI settings",
            )
            require(
                all(item.wck_disabled for item in trace)
                and all(not item.start and not item.complete and item.clock_change_done for item in trace[release:]),
                "selected stuck-return trace changed released controls or enabled WCK",
            )
            require(
                decoded_program(pins)
                == expected_program(p, q, self.owner.active_fsp, self.owner.transaction["target_fsp"]),
                "failed FSP incomplete/wrong actual MR program",
            )
            self.faults.add(c)
            self.publication.timed_out(e)

    def retained_decline(self, boot, epoch):
        """Keep the real channel/source fences intact while APB settles."""
        require(
            self.source.settled()
            and all(
                s is not None
                and s["config"] == epoch
                and s["boot"] == boot
                and s["config_valid"]
                and s["trusted"]
                and not any(s[k] for k in ("prepared_valid", "fault", "host_fault"))
                for s in self.last
            )
            and all(
                s is not None
                and s["epoch"] == epoch
                and s["boot"] == boot
                and not any(s[k] for k in ("prepared", "fault", "mb_commit", "mb_abort"))
                for s in self.fsp_edges
            ),
            "FSP decline lacks retained valid channels/source settlement",
        )

    def decline_marker(self, e):
        w, owner = self.publication, self.owner
        if w.active is None and owner.transaction is None:
            return
        t, physical = w.active, owner.transaction
        require(
            self.selection == 0
            and t is not None
            and physical is not None
            and t["ck_acks"] == t["apb_acks"] == {(0, 3): 0, (1, 3): 0}
            and t["transfers"] == {(0, 0), (1, 0)}
            and set(t["frozen"]) == {0, 1}
            and not any(t[k] for k in ("commits", "faults", "aborts"))
            and physical["sequence"] == t["sequence"]
            and physical["ready"] == {0: "declined", 1: "declined"}
            and not physical["committed"]
            and e["boot"] == t["boot"] == owner.boot
            and w.epoch == owner.epoch,
            "early FSP decline marker lacks complete unanimous retained publication",
        )
        a = w.apb
        require(
            a is not None
            and (a["sequence"], a["boot"], a["epoch"]) == (t["sequence"], t["boot"], w.epoch)
            and a["busy"]
            and not a["valid"]
            and a["settled"]
            and (not a["enabled"] or a["quiesced"])
            and self.decline_ack_ps == {0: a["ps"], 1: a["ps"]}
            and a["ps"] <= e["ps"] < a["ps"] + PCLK_PS,
            "early FSP decline marker outside final-ACK PCLK interval",
        )
        self.retained_decline(t["boot"], w.epoch)
        # The marker polls after NBA; the APB observer samples before NBA.
        # Keep both owners live until the next real PCLK sample attests the
        # final idle/valid state. Neither ACKs nor the marker finalize them.
        self.decline_pending = dict(ps=a["ps"] + PCLK_PS, sequence=t["sequence"], boot=t["boot"], epoch=w.epoch)

    def event(self, e):
        kind = e["kind"]
        pending = getattr(self, "decline_pending", None)
        if pending is not None:
            require(e["ps"] <= pending["ps"], "missing next-PCLK FSP decline publication witness")
            require(kind not in ("aw", "ar"), "source service before final FSP decline publication witness")
            if kind == "fsp_apb":
                require(
                    all(e[k] == pending[k] for k in ("ps", "sequence", "boot", "epoch"))
                    and e["valid"]
                    and not e["busy"]
                    and e["settled"]
                    and (not e["enabled"] or e["quiesced"]),
                    "FSP decline not finalized on the next qualified PCLK edge",
                )
                self.retained_decline(pending["boot"], pending["epoch"])
        if self.failure_pending is not None:
            require(e["ps"] <= self.failure_pending[1], "missing next-edge FSP host fatal witness")
            if kind == "host_settlement":
                require(
                    (e["tick"], e["ps"]) == self.failure_pending and e["fatal"],
                    "FSP fault fence not consumed at next qualified host edge",
                )
                self.failure_pending = None
        if kind == "fsp_binding":
            return
        if kind == "fsp_edge":
            self.edge(e)
            return
        if kind in ("fsp_apb", "fsp_freeze", "fsp_transfer", "fsp_ack", "fsp_apb_ack"):
            if not self.active or self.baseline:
                return
            w = self.publication
            if kind == "fsp_apb":
                old = len(w.history)
                w.observe_apb(e, self.source.settled())
                if len(w.history) > old:
                    t = w.history[-1]
                    if t["outcome"] == "declined":
                        self.owner.declined(
                            published_epoch=w.epoch,
                            channel_epochs=[s["config"] for s in self.last],
                            prepared_valid=[s["prepared_valid"] for s in self.last],
                        )
                if pending is not None:
                    require(
                        len(w.history) == old + 1
                        and w.history[-1]["outcome"] == "declined"
                        and w.active is None
                        and self.owner.transaction is None,
                        "next-PCLK FSP decline did not close both actual owners",
                    )
                    self.decline_pending = None
            elif kind == "fsp_freeze":
                w.freeze(e)
            elif kind == "fsp_transfer":
                w.transfer(e)
                if e["prepare"] and self.owner.transaction is None:
                    t = w.active
                    slot = t["target"]
                    self.controls = [[], []]
                    self.decline_ack_ps = {}
                    self.owner.prepare(
                        sequence=t["sequence"],
                        boot=t["boot"],
                        target_fsp=slot,
                        targets=[self.profiles[slot]] * 2,
                        source_settled=self.source.settled(),
                    )
            elif kind == "fsp_ack":
                if e["phase"] == 3 or e["phase"] == 0 and e["accepted"]:
                    self.return_ready(e["channel"], e)
                elif e["phase"] == 2:
                    require(
                        not self.fsp_edges[e["channel"]]["prepared"] and not self.last[e["channel"]]["config_valid"],
                        "ABORT ACK before actual prepared/active invalidation",
                    )
                w.ack(e)
            else:
                w.ack(e, apb=True)
                if e["phase"] == 3:
                    self.decline_ack_ps[e["channel"]] = e["ps"]
            return
        if kind == "host_edge":
            self.host_edge = e
            if (
                self.active
                and not self.baseline
                and self.owner
                and self.owner.transaction is not None
                and e["config_valid"]
            ):
                t = self.owner.transaction
                if e["config_epoch"] == t["sequence"]:
                    require(
                        self.publication.active is None and self.publication.epoch == t["sequence"],
                        "host image before actual final APB publication",
                    )
                    self.owner.published(
                        sequence=t["sequence"],
                        boot=self.owner.boot,
                        host_epoch=e["config_epoch"],
                        channel_epochs=[s["config"] for s in self.last],
                    )
        if kind == "host_settlement" and self.active and e["fatal"] and not self.failure_seen:
            require(self.selection == 2 and self.faults, "host fatal without selected sampled FSP timeout")
            self.failure_seen = True
            self.source.host.fail()
        if kind in ("aw", "ar") and self.active and "fsp_quiesced" in self.markers:
            require(
                self.last[0]["enabled"] and self.host_edge and self.host_edge["enable"],
                "actual source admission before renewed wrapper/host enable",
            )
            for channel, edge in enumerate(self.fsp_edges):
                slot = self.committed[channel]
                ratio = self.profiles[slot]["ratio"]
                require(
                    edge is not None
                    and edge["fsp"] == slot
                    and edge["ratio"] == {2: 1, 4: 2}[ratio]
                    and edge["frequency"] == (0 if ratio == 2 else 1),
                    "source service before actual common DFI settings",
                )
        if kind in ("aw", "ar") and self.active and "fsp_publish_offer" in self.markers and not self.baseline:
            require(
                self.owner is not None and self.host_edge is not None, "source service lacks current installed image"
            )
            self.owner.service(
                host_epoch=self.host_edge["config_epoch"], channel_epochs=[s["config"] for s in self.last]
            )
        super().event(e)

    def marker(self, e):
        name = e["name"]
        require(getattr(self, "decline_pending", None) is None, "FSP marker before pending final decline PCLK witness")
        require(name.startswith("fsp_"), "unexpected FSP marker")
        expected = [
            "fsp_begin",
            "fsp_initial_preserved",
            "fsp_dirty",
            "fsp_dirty_rejected",
            "fsp_quiesced",
            "fsp_publish_offer",
        ]
        expected += {
            0: ["fsp_declined"],
            1: ["fsp_published"],
            2: [
                "fsp_failed",
                "fsp_recover_requested",
                "fsp_stale_confirmation_rejected",
                "fsp_recovery_baseline",
                "fsp_retrained",
            ],
        }[self.selection]
        expected += ["fsp_recovered" if self.selection == 2 else "fsp_preserved"]
        if self.selection == 1:
            expected += ["fsp_publish_offer", "fsp_published", "fsp_roundtrip_preserved"]
        expected += ["fsp_end"]
        require(
            len(self.markers) < len(expected) and name == expected[len(self.markers)],
            "missing/duplicate/reordered FSP marker",
        )
        self.markers.append(name)
        if name == "fsp_begin":
            require(not self.active, "duplicate FSP begin")
            self.active = True
            require(self.host_edge is not None and all(self.physical), "FSP missing real physical/host history")
            epoch = self.host_edge["config_epoch"]
            self.publication = PublicationWitness(epoch, failed_case=self.selection == 2)
            self.owner = ProfilePublication([self.p, self.p], self.physical, boot=e["boot"], epoch=epoch)
            self.host_completions.begin(e["case"], self.selection == 2)
            self.recovery.begin()
        elif name == "fsp_initial_preserved":
            require(self.source.settled(), "initial FSP images lack source settlement")
            for ident in (173, 174):
                require(
                    any(
                        t.done and not t.write and t.address.id == ident for t in self.source.host.transactions.values()
                    ),
                    "missing both initial protected image reads",
                )
        elif name == "fsp_declined":
            self.decline_marker(e)
        elif name == "fsp_published":
            require(
                self.publication.active is None and self.owner.transaction is None,
                "FSP outcome before actual final publication/host installation",
            )
        elif name == "fsp_dirty":
            require(
                not self.source.settled()
                and self.inline[0].dirty
                and (1, "b") in self.source.held
                and (1, "r") in self.source.held,
                "FSP rejected publication lacks actual dirty inline work and exposed held B/R",
            )
        elif name == "fsp_dirty_rejected":
            require(
                self.publication.rejected == 1 and self.publication.active is None,
                "missing actual side-effect-free dirty rejection",
            )
        elif name in ("fsp_quiesced", "fsp_end"):
            require(
                self.source.settled()
                and all(p and p.drained() for p in self.physical)
                and all(i.live is None for i in self.inline),
                "FSP marker before independent source/pin/ECC/WCK drain",
            )
            if name == "fsp_end":
                self.host_completions.checkpoint(e)
                self.finished = True
        elif name == "fsp_failed":
            require(self.faults == {0, 1}, "missing both actual FSP timeouts")
            # The fixture marks the wrapper fault fence at falling CK+1ps.
            # Its fatal input is consumed/recorded on the following rising
            # edge. Bind both sampled outputs and require that exact next
            # receiving witness before allowing recovery or finishing.
            require(
                all(
                    s
                    and s["channel"] == c
                    and s["host_fault"]
                    and not s["enabled"]
                    and s["ps"] <= e["ps"] < s["ps"] + self.p["ck_ps"] // 2
                    for c, s in enumerate(self.last)
                ),
                "FSP failed marker without actual sampled host fault/admission fence",
            )
            require(
                (self.last[0]["tick"], self.last[0]["ps"]) == (self.last[1]["tick"], self.last[1]["ps"]),
                "FSP failed marker without paired channel samples",
            )
            self.failure_pending = self.last[0]["tick"], self.last[0]["ps"] + self.p["ck_ps"] // 2
        elif name == "fsp_recover_requested":
            require(self.recovery_allowed(), "RECOVER marker without settled ABORT and actual accepted request")
        elif name == "fsp_recovery_baseline":
            self.recovery.require_complete()
            self.host_completions.baseline(e, self.last, self.source.settled(), self.recovery_channels)
            self.baseline = True
        elif name == "fsp_retrained":
            require(
                self.selection == 2 and self.baseline and all(s["trained"] and s["boot"] == 1 for s in self.last),
                "missing fresh FSP failure reconfiguration/training",
            )
        elif name in ("fsp_preserved", "fsp_recovered", "fsp_roundtrip_preserved"):
            require(self.source.settled(), "FSP byte checks before real response settlement")
            ids = (195, 196) if name == "fsp_roundtrip_preserved" else (190, 191, 192)
            require(
                all(
                    any(
                        t.done and not t.write and t.address.id == ident for t in self.source.host.transactions.values()
                    )
                    for ident in ids
                ),
                "missing actual selected byte/check reads",
            )

    def finish(self):
        require(getattr(self, "decline_pending", None) is None, "unfinished next-PCLK FSP decline publication witness")
        require(self.finished and self.markers.count("fsp_end") == 1, "missing FSP terminal tail")
        self.source.host.finish()
        require(not self.addresses.held and not self.addresses.offers, "FSP pending source address tail")
        expected = {0: ["declined"], 1: ["published", "published"], 2: ["failed"]}[self.selection]
        require(
            [t["outcome"] for t in self.publication.history] == expected, "missing selected all-channel FSP outcomes"
        )
        if self.selection == 2:
            self.publication.require_failed()
            require(
                self.failure_seen
                and self.failure_pending is None
                and self.baseline
                and self.recovery_channels == {0, 1}
                and "fsp_retrained" in self.markers,
                "incomplete FSP fatal recovery",
            )
        else:
            require(self.publication.active is None and self.owner.transaction is None, "unfinished FSP transaction")
        return dict(
            source=dict(self.source.counts),
            publications=[
                {"sequence": t["sequence"], "target": t["target"], "outcome": t["outcome"]}
                for t in self.publication.history
            ],
            host_completions=self.host_completions.finish(),
            physical=[p.summary() for p in self.physical],
            cancelled=self.cancelled,
            retired_epochs=self.source.retired_epochs,
            recovery=self.recovery.finish(),
            markers=self.markers,
            limitations=[
                "P2/C2/R1 AXI128 depth16 inline ECC CK2, constant CK1250ps, selected canonical profiles0/24 only.",
                "No variable-CK or two-rank FSP qualification.",
                "FSP return uses init_complete freshness; PHY-master rank-report freshness is separate.",
            ],
        )


def check(wrapper_path, source_path, selection):
    records = [json.loads(line) for line in Path(wrapper_path).read_text().splitlines()]
    source = [json.loads(line) for line in Path(source_path).read_text().splitlines()]
    header, p, q = records[0], profile_binding(), target_profile()
    require(header["kind"] == "header" and header["profile_sha256"] == digest(p), "FSP initial profile binding")
    bindings = [e for e in records if e["kind"] == "fsp_binding"]
    require(len(bindings) == 1 and bindings[0]["case"] == selection and selection in (0, 1, 2), "unbound FSP selection")
    binding = bindings[0]
    require(
        binding["target_sha256"] == digest(q)
        and [binding[k] for k in ("initial_number", "target_number", "return_number")] == [0, 24, 0],
        "FSP immutable profile digest/identity",
    )
    require(records[-1]["kind"] == source[-1]["kind"] == "end", "missing FSP/source terminal tail")
    phases = qualify_phases(records[1:-1], header)
    addresses = qualify_address_coverage(records, source)
    edges = qualify_fsp(records[1:-1], binding)
    order = {
        k: i
        for i, k in enumerate(
            (
                "sample",
                "window_sample",
                "host_reset",
                "window_edge",
                "management_edge",
                "fsp_edge",
                "fsp_transfer",
                "fsp_ack",
                "fsp_apb",
                "fsp_freeze",
                "fsp_apb_ack",
                "host_settlement",
                "host_edge",
                "marker",
                "axi",
                "address",
                "aw",
                "ar",
                "w",
                "inline_admit",
                "inline_physical",
                "inline_result",
                "inline_complete",
                "b",
                "r",
            )
        )
    }
    events = [e for e in records[1:-1] if e["kind"] != "edge"] + [e for e in source[1:-1] if e["kind"] != "phy"]
    events.sort(key=lambda e: (e["ps"], order.get(e["kind"], 13), e.get("port", e.get("channel", 0))))
    run = FspRun(p, q, binding, records)
    for e in events:
        run.event(e)
    return run.finish() | {
        "phase_samples": phases,
        "address_samples": addresses,
        "fsp_edges": edges,
        "raw_sha256": hashlib.sha256(Path(wrapper_path).read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(Path(source_path).read_bytes()).hexdigest(),
    }
