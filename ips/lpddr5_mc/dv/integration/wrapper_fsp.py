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
        apb and all(b["ps"] - a["ps"] == 7000 for a, b in zip(apb, apb[1:], strict=False)),
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

    def __init__(self, epoch):
        self.epoch = epoch
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
                self.active = dict(
                    sequence=e["sequence"] + 1,
                    boot=e["boot"],
                    frozen={},
                    transfers=set(),
                    ck_acks={},
                    apb_acks={},
                    commits=set(),
                    target=None,
                )
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
            e["prepare"] + e["commit"] + e["abort"] == 1 and not e["abort"] and e["dest"] == 3,
            "FSP payload phase/destinations",
        )
        phase = int(bool(e["commit"]))
        require(
            c in t["frozen"] and number(e["image"]) == t["frozen"][c] and e["crc"] == image_crc(e["image"]),
            "changed frozen payload/CRC",
        )
        require((c, phase) not in t["transfers"], "duplicate configuration transfer")
        if phase:
            require(
                all(t["apb_acks"].get((channel, 0)) == 1 for channel in (0, 1)),
                "commit payload before all prepare ACKs",
            )
        t["transfers"].add((c, phase))

    def ack(self, e, *, apb=False):
        t = self.identity(e)
        c, phase = e["channel"], e["phase"]
        key = c, phase
        require(phase in (0, 1, 3) and e["accepted"] == (phase != 3), "invalid FSP ACK outcome")
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
            t is not None and all(t["apb_acks"].get((channel, 0)) == 1 for channel in (0, 1)),
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
        self.baseline = False
        self.finished = False
        self.host_edge = None

    def validate_profile(self, e):
        c = e["channel"]
        slot = self.committed[c]
        require(number(e["profile_image"]) == self.images[slot], "unbound active FSP memory profile")

    def recovery_allowed(self):
        return self.selection == 2 and self.failure_seen

    def record_recovery(self, e):
        require(self.recovery_allowed(), "FSP reset without actual selected fault")
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
        if e["fault"] and c not in self.faults:
            require(self.selection == 2 and self.owner is not None, "unexpected FSP engine fault")
            trace, pins = self.trace(c)
            p, q = self.owner.profiles[c], self.owner.transaction["targets"][c]
            result = check_dvfs(
                p["management_ck"]["tinit_start"], p["management_ck"]["tinit_complete"], trace, drained=True
            )
            require(result.disposition == "completion_timeout", "fault lacks sampled stuck-return timeout")
            require(
                decoded_program(pins)
                == expected_program(p, q, self.owner.active_fsp, self.owner.transaction["target_fsp"]),
                "failed FSP incomplete/wrong actual MR program",
            )
            self.faults.add(c)

    def event(self, e):
        kind = e["kind"]
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
            elif kind == "fsp_freeze":
                w.freeze(e)
            elif kind == "fsp_transfer":
                w.transfer(e)
                if e["prepare"] and self.owner.transaction is None:
                    t = w.active
                    slot = t["target"]
                    self.controls = [[], []]
                    self.owner.prepare(
                        sequence=t["sequence"],
                        boot=t["boot"],
                        target_fsp=slot,
                        targets=[self.profiles[slot]] * 2,
                        source_settled=self.source.settled(),
                    )
            elif kind == "fsp_ack":
                if e["phase"] in (0, 3):
                    self.return_ready(e["channel"], e)
                w.ack(e)
            else:
                w.ack(e, apb=True)
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
        expected += ["fsp_preserved"]
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
            self.publication = PublicationWitness(epoch)
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
        elif name in ("fsp_published", "fsp_declined"):
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
            require(self.failure_seen and self.faults == {0, 1}, "missing both actual FSP timeouts/host fault")
        elif name == "fsp_recovery_baseline":
            self.recovery.require_complete()
            self.host_completions.baseline(e, self.last, self.source.settled(), self.recovery_channels)
            self.baseline = True
        elif name == "fsp_retrained":
            require(
                self.selection == 2 and self.baseline and all(s["trained"] and s["boot"] == 1 for s in self.last),
                "missing fresh FSP failure reconfiguration/training",
            )
        elif name in ("fsp_preserved", "fsp_roundtrip_preserved"):
            require(self.source.settled(), "FSP preservation before real response settlement")
            ids = (190, 191, 192) if name == "fsp_preserved" else (195, 196)
            require(
                all(
                    any(
                        t.done and not t.write and t.address.id == ident for t in self.source.host.transactions.values()
                    )
                    for ident in ids
                ),
                "missing actual preserved byte/check reads",
            )

    def finish(self):
        require(self.finished and self.markers.count("fsp_end") == 1, "missing FSP terminal tail")
        self.source.host.finish()
        require(not self.addresses.held and not self.addresses.offers, "FSP pending source address tail")
        expected = {0: ["declined"], 1: ["published", "published"], 2: []}[self.selection]
        require(
            [t["outcome"] for t in self.publication.history] == expected, "missing selected all-channel FSP outcomes"
        )
        if self.selection == 2:
            require(
                self.failure_seen
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
