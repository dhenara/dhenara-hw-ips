"""Version-two channel stream admission and composed dirty-management checks.

Raw timestamps remain simulator picoseconds. Normalized time is tick*ck_ps:
extra #1 delays in this procedural bench are not a physical CK clock. Events
stay in their recorded order; each pin sample precedes the later clock-edge
handshakes. Component settlement is an input attestation, never an AXI ledger.
"""

import copy
import hashlib
import json
from pathlib import Path

from continuous import InlineDrain, identity
from physical_stream import PhysicalStream, number
from system.host import require

IP = Path(__file__).resolve().parents[2]
PHASE = "memory_sample_after_prepare_before_sample_before_clk"
HANDSHAKE_PHASE = "posedge_clk_after_memory_sample"
KINDS = ("update", "fsp", "power", "master")
FLAGS = dict(
    original_offer="offer",
    original_admit="request",
    inline_admit="admit",
    inline_physical="physical",
    inline_result="result",
    inline_complete="complete",
    original_complete="original",
)
GRANULE_FIELDS = "tag generation boot config rank row bank column write bytes mask data payload".split()
COMPLETION_FIELDS = "tag generation boot config write physical integrity failed response data payload".split()
TICK_FIELDS = """tick case_id cycles time_ps profile reset_n memory_reset destructive confirmed settled enabled
host_fault fault trusted owner boot config config_valid trained training_epoch drained pin_drained ecc_drained
inline_idle data_committed command_valid command_ready command command_rejected
update phy_update fsp fsp_busy fsp_ready
power master refresh_request refresh_done rank_valid rank_sr thermal ratio cs ca wck write read return mr4 mr43
management_in management_out lp_ctrl_ack lp_data_ack ctrlupd_ack phyupd_req phymstr_req
init_complete error train_ack train_token prepared
prepare_error prepared_epoch committed_epoch mailbox config_prepare config_commit request_valid request_ready
completion_valid completion_ready inline_admit inline_physical inline_result inline_complete profile_image""".split()


def bindings():
    profiles = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"]
    result = []
    for ratio in (4, 2):
        matches = [
            p
            for p in profiles
            if p["ratio"] == ratio
            and p["always_on"]
            and p["write_dbi"]
            and not (p["read_dbi"] or p["read_link"] or p["write_link"])
        ]
        require(len(matches) == 1, "ambiguous channel profile binding")
        p = copy.deepcopy(matches[0])
        p["ranks"] = 1
        result.append(p)
    return result


def binding_digest(profiles):
    return hashlib.sha256(json.dumps(profiles, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Case:
    def __init__(self, start, profiles):
        self.start, self.profiles = start, profiles
        self.inline = InlineDrain()
        self.physical = None
        self.profile_id = None
        self.last = self.original = self.held = None
        self.reset_seen = self.fault_seen = self.dirty_seen = self.request_seen = False
        self.configured = self.training = False
        self.retired_boot = -1
        self.retired_config = 0
        self.reset_invalid_seen = False
        self.reset_intervals = []
        self.interval_start = None
        self.intervals = []
        self.ownership = []
        self.dirty_tick = self.request_tick = self.completion_tick = None
        self.held_ticks = self.completed = self.entries = self.aborted_originals = 0
        self.external_acks = 0
        self.init_low = self.init_high = self.power_ack = self.wake = False
        self.master_report_low = self.master_report_fresh = self.master_withdraw = False
        self.profile_images = {}
        self.configurations = set()

    def close_physical(self, tick, reason, *, cancelled):
        if self.physical is not None:
            self.intervals.append(
                dict(
                    start=self.interval_start,
                    end=tick,
                    reason=reason,
                    **self.physical.summary(cancelled=cancelled),
                )
            )
            self.physical = None

    def sample(self, raw):
        e = dict(raw)
        require(set(TICK_FIELDS) <= e.keys(), "missing raw tick fields")
        p = self.profiles[e["profile"]] if e["profile"] in (0, 1) else None
        reset = not e["reset_n"] or e["destructive"] or e["memory_reset"]
        require(
            p is not None and (reset or e["ratio"] == p["ratio"]),
            "missing/mismatched profile binding",
        )
        old = self.last
        if old:
            require(e["time_ps"] > old["time_ps"], "nonmonotonic simulator timestamp")
            require(
                e["cycles"] == (0 if not old["reset_n"] and e["reset_n"] else old["cycles"] + 1),
                "missing/repeated case-local cycle",
            )
        require(
            not e["memory_reset"] or not e["reset_n"] or e["destructive"] or old and old["destructive"],
            "memory reset without witnessed destructive/cold reset",
        )
        if e["destructive"]:
            require(
                e["settled"] and e["confirmed"] and not e["enabled"],
                "destructive reset before component settlement/confirmation",
            )
        if reset and (old is None or old["reset_n"] and not old["destructive"] and not old["memory_reset"]):
            self.close_physical(
                e["tick"],
                "destructive reset" if e["destructive"] else "cold reset",
                cancelled=True,
            )
            self.aborted_originals += self.original is not None
            self.original = self.held = None
            self.inline.reset()
            self.reset_seen = True
            self.configured = self.training = False
            self.configurations.clear()
            self.reset_invalid_seen = False
            self.retired_boot = old["boot"] if old else -1
            self.retired_config = old["config"] if old else 0
            self.reset_intervals.append(dict(tick=e["tick"], boot=e["boot"], destructive=bool(e["destructive"])))
        if old and e["boot"] != old["boot"]:
            require(
                self.reset_seen
                and e["boot"] == old["boot"] + 1
                and not e["trusted"]
                and (reset or not e["config_valid"])
                and not e["trained"],
                "stale/unwitnessed boot recovery",
            )
        if not reset:
            image = number(e["profile_image"])
            require(
                image > 0 and self.profile_images.get(e["profile"], image) == image,
                "profile image changed within explicit binding",
            )
            self.profile_images[e["profile"]] = image
            if not e["config_valid"] and not e["trusted"] and not e["trained"]:
                self.reset_invalid_seen = True
            if e["config_commit"]:
                require(
                    e["prepared"] and e["prepared_epoch"] > 0 and not e["prepare_error"],
                    "configuration commit without exact prepared epoch",
                )
                self.configurations.add((e["boot"], e["prepared_epoch"]))
                self.configured = True
            if e["config_valid"]:
                require(
                    (e["boot"], e["config"]) in self.configurations,
                    "stale/unpublished configuration epoch",
                )
            if e["train_ack"]:
                require(
                    e["train_token"] == e["boot"] and e["training_epoch"] == e["boot"],
                    "stale training ACK",
                )
                self.training = True
            if e["trusted"] and not (old and old["trusted"]):
                require(
                    self.reset_seen
                    and e["boot"] > self.retired_boot
                    and self.training
                    and self.configured
                    and self.reset_invalid_seen
                    and e["trained"]
                    and e["training_epoch"] == e["boot"]
                    and e["config_valid"]
                    and e["config"] > 0
                    and not e["fault"],
                    "trust without fresh reset/training/configuration",
                )
                self.reset_seen = False
            if e["enabled"]:
                require(
                    e["trusted"] and e["trained"] and e["config_valid"] and not e["fault"],
                    "channel service without fresh trust",
                )
            if self.physical is None:
                self.physical = PhysicalStream(p)
                self.interval_start = e["tick"]
                self.profile_id = e["profile"]
            elif e["profile"] != self.profile_id:
                require(
                    e["config_valid"]
                    and not self.inline.live
                    and old is not None
                    and e["config"] > old["config"]
                    and e["boot"] == old["boot"],
                    "profile change with live inline lock or stale configuration epoch",
                )
                self.physical.profile(p)
                self.profile_id = e["profile"]
            self.physical.sample(e)
        if e["fault"]:
            self.fault_seen = True
        if not reset and (e["fault"] or not e["trusted"]):
            require(not e["request_ready"] or reset, "inline admission after trust loss")
            if e["cs"]:
                from system.dram import decode

                require(
                    decode(e["ca"]).kind not in ("ACT1", "RD", "WR", "MWR"),
                    "new access after trust loss",
                )
        # data_committed_q is sticky through COMPLETE/IDLE. The independently
        # checked data/check results, not that status bit, define the interval.
        if self.inline.dirty and not reset:
            require(
                e["data_committed"],
                "missing data-commit status in independently dirty interval",
            )
            self.dirty_seen = True
            if self.dirty_tick is None:
                self.dirty_tick = e["tick"]
        requested = (
            (e["command_valid"] and e["command_ready"] and e["command"] == (3, 7, 4, -1)[self.start["management"]])
            or self.start["management"] == 3
            and e["phymstr_req"]
        )
        if requested and not self.request_seen:
            require(
                self.inline.live is not None
                and self.inline.dirty
                and e["data_committed"]
                and not e["inline_idle"]
                and not e["ecc_drained"]
                and not e["settled"],
                "management request did not hit actual dirty interval",
            )
            self.request_seen = True
            self.request_tick = e["tick"]
        if self.request_seen and not self.completion_tick and e["completion_valid"] and not e["completion_ready"]:
            self.held_ticks += 1
        entry = any(e[k] and not (old and old[k]) for k in ("update", "phy_update", "fsp", "power", "master"))
        # Boot init_start is not a dirty-management FSP entry.
        entry = entry and self.request_seen
        if entry:
            require(e[KINDS[self.start["management"]]], "wrong management entry kind")
            require(
                not self.start["failed"]
                and self.completed == 1
                and self.inline.live is None
                and not self.inline.dirty
                and self.original is None
                and e["settled"]
                and e["pin_drained"]
                and e["ecc_drained"]
                and e["inline_idle"]
                and e["drained"]
                and self.physical is not None
                and self.physical.drained()
                and number(e["wck"]) == 0,
                "management ownership before independent pin/data/check/original drain",
            )
            self.entries += 1
        if e["owner"] == 1 and e["trained"] and not reset:
            require(
                number(e["wck"]) == 0 and number(e["write"]) >> 24 & 255 == 0 and number(e["read"]) >> 16 == 0,
                "PHY ownership with controller data/WCK",
            )
        if (
            old is None
            or e["owner"] != old["owner"]
            or reset != bool(not old["reset_n"] or old["destructive"] or old["memory_reset"])
        ):
            self.ownership.append(dict(tick=e["tick"], owner=e["owner"], reset=bool(reset)))
        if self.entries:
            self.init_low |= not e["init_complete"]
            self.init_high |= self.init_low and bool(e["init_complete"])
            self.power_ack |= bool(e["lp_ctrl_ack"] and e["lp_data_ack"])
            self.wake |= bool(e["command_valid"] and e["command_ready"] and e["command"] == 6)
            self.master_report_low |= not e["rank_valid"]
            self.master_report_fresh |= bool(self.master_report_low and e["rank_valid"] == 1)
            self.master_withdraw |= bool(self.master_report_fresh and not e["phymstr_req"])
        if e["ctrlupd_ack"]:
            require(
                self.entries and e["update"],
                "ACK without dirty controller-update request",
            )
            self.external_acks += 1
        self.last = e

    def events(self, handshakes, events):
        e = self.last
        require(e is not None, "events without tick")
        for kind, flag in FLAGS.items():
            require(
                sum(q["kind"] == kind for q in events) == int(bool(handshakes[flag])),
                "missing/duplicate clock-edge handshake identity",
            )
        for q in events:
            kind = q["kind"]
            if kind in ("original_admit", "inline_admit"):
                require(
                    e["trusted"] and not e["fault"] and q["boot"] == e["boot"] and q["config"] == e["config"],
                    "stale original/inline admission",
                )
            if kind == "original_admit":
                require(self.original is None, "duplicate original admission")
                self.original = q
            elif kind == "original_offer":
                require(
                    self.original is not None and identity(q) == identity(self.original),
                    "stale original offer",
                )
                # Check eligibility even under completion backpressure; restore the
                # live lock because this is an offer, not a successful handshake.
                probe = copy.deepcopy(self.inline)
                probe.event(dict(q, kind="inline_complete"))
                require(
                    self.physical is not None and not self.physical.requests,
                    "original offer before independent physical results",
                )
                if self.held:
                    require(
                        all(q[k] == self.held[k] for k in COMPLETION_FIELDS),
                        "unstable held component completion",
                    )
                self.held = q
            elif kind == "original_complete":
                require(
                    self.original is not None
                    and identity(q) == identity(self.original)
                    and self.held is not None
                    and all(q[k] == self.held[k] for k in COMPLETION_FIELDS),
                    "original completion identity",
                )
                self.original = self.held = None
                self.completed += 1
                self.completion_tick = e["tick"]
            elif kind in (
                "inline_admit",
                "inline_physical",
                "inline_result",
                "inline_complete",
            ):
                require(self.physical is not None, "inline event while reset")
                if kind == "inline_admit":
                    require(
                        self.original is not None and all(q[k] == self.original[k] for k in GRANULE_FIELDS),
                        "inline admission differs from original payload",
                    )
                if kind == "inline_physical":
                    self.physical.expect(q)
                elif kind == "inline_result":
                    self.physical.result(q)
                self.inline.event(q)
            else:
                raise ValueError("unknown channel event kind")

    def finish(self, tick):
        require(
            self.last is not None and self.dirty_seen and self.request_seen,
            "missing dirty management coverage",
        )
        if self.start["failed"]:
            require(
                self.fault_seen
                and self.completed == 0
                and self.entries == 0
                and self.aborted_originals == 1
                and any(i["destructive"] for i in self.reset_intervals)
                and self.reset_invalid_seen
                and self.last["boot"] == 1
                and not self.last["config_valid"]
                and not self.last["trusted"],
                "failed dirty case missing original cancellation/reset epoch fencing",
            )
        else:
            require(
                self.completed == 1
                and self.entries >= 1
                and self.held_ticks >= 8
                and not self.fault_seen
                and self.last["trusted"]
                and self.last["config_valid"],
                "success dirty case missing held completion/entry/terminal accounting",
            )
        if not self.start["failed"]:
            kind = self.start["management"]
            require(
                (kind != 0 or self.external_acks > 0)
                and (
                    kind != 1
                    or self.init_low
                    and self.init_high
                    and self.last["config"] == 2
                    and self.last["profile"] == 1
                    and self.last["boot"] == 0
                )
                and (kind != 2 or self.power_ack and self.wake and not self.last["power"])
                and (kind != 3 or self.master_report_low and self.master_report_fresh and self.master_withdraw),
                "missing external management ACK/return/epoch semantics",
            )
        require(
            self.original is None and self.inline.live is None,
            "incomplete original/inline tail",
        )
        self.close_physical(tick, "case end", cancelled=False)
        return dict(
            case_id=self.start["case_id"],
            management=KINDS[self.start["management"]],
            failed=self.start["failed"],
            dirty_tick=self.dirty_tick,
            request_tick=self.request_tick,
            completion_tick=self.completion_tick,
            held_ticks=self.held_ticks,
            entries=self.entries,
            original_completions=self.completed,
            original_cancellations=self.aborted_originals,
            inline_completed=self.inline.completed,
            inline_cancelled=self.inline.aborted,
            physical_intervals=self.intervals,
            wck_total={
                k: sum(i["wck_total"][k] for i in self.intervals)
                for k in ("epochs", "accesses", "half_wck_samples", "disabled_epochs")
            },
            ownership=self.ownership,
            reset_intervals=self.reset_intervals,
            ctrlupd_ack_ticks=self.external_acks,
            settlement="component host_settled input attestation; no wrapper AXI settlement proof",
        )


def check(events, profiles=None):
    profiles = profiles or bindings()
    records = iter(events)
    h = next(records)
    require(
        h["kind"] == "header"
        and h["version"] == 2
        and h["phase"] == PHASE
        and h["handshake_phase"] == HANDSHAKE_PHASE
        and h["time_unit"] == "ps"
        and h["timing"] == "one_tick_one_profile_CK"
        and h["profile_binding"] == "dirty-management-v1"
        and h["profile_sha256"] == binding_digest(profiles)
        and h["cases"] == 8,
        "missing stream phase/profile binding",
    )
    current = frame = handshakes = None
    clock_events, results = [], []
    tick = 0
    time_ps = -1
    ended = False

    def flush():
        if frame is not None:
            require(handshakes is not None, "missing later clock-edge handshake sample")
            require(
                handshakes["time_ps"] > frame["time_ps"],
                "clock handshake timestamp precedes memory sample",
            )
            current.events(handshakes, clock_events)

    for e in records:
        require(not ended, "event after complete channel tail")
        kind = e["kind"]
        if kind in ("tick", "case_end"):
            if kind == "tick" and handshakes is not None:
                require(
                    e["time_ps"] > handshakes["time_ps"],
                    "clock timestamp crosses next memory sample",
                )
            flush()
            frame, handshakes, clock_events = None, None, []
        if kind == "case_begin":
            require(
                current is None
                and e["tick"] == tick
                and e["case_id"] == len(results)
                and e["case_id"] == e["management"] * 2 + e["failed"],
                "missing/duplicate/reordered management case",
            )
            current = Case(e, profiles)
        elif kind == "tick":
            require(
                current is not None and e["case_id"] == current.start["case_id"] and e["tick"] == tick,
                "missing/duplicate channel tick",
            )
            require(e["time_ps"] > time_ps, "missing/repeated actual timestamp")
            time_ps = e["time_ps"]
            tick += 1
            current.sample(e)
            frame = e
        elif kind == "handshakes":
            require(
                frame is not None
                and handshakes is None
                and e["tick"] == tick - 1
                and e["case_id"] == current.start["case_id"]
                and set(FLAGS.values()) <= e.keys(),
                "missing/duplicate clock phase binding",
            )
            handshakes = e
        elif kind in FLAGS:
            require(
                frame is not None
                and handshakes is not None
                and e["tick"] == tick - 1
                and e["case_id"] == current.start["case_id"],
                "event outside exact clock tick",
            )
            clock_events.append(e)
        elif kind == "case_end":
            require(
                current is not None and e["case_id"] == current.start["case_id"] and e["tick"] == tick,
                "incomplete/repeated case tail",
            )
            results.append(current.finish(tick))
            current = None
        elif kind == "end":
            require(
                current is None and len(results) == 8 and e["tick"] == tick and e["complete"] == 1,
                "incomplete channel tail/eight-case success marker",
            )
            ended = True
        else:
            raise ValueError("unknown channel record")
    require(ended, "truncated channel stream")
    return dict(
        ticks=tick,
        cases=results,
        profile_sha256=binding_digest(profiles),
        sampling_phase=PHASE,
        handshake_phase=HANDSHAKE_PHASE,
        limitations=[
            "Synthetic fixtures qualify this adapter only until actual case9 execution.",
            "Component host settlement attestation is not real wrapper AXI settlement.",
            "Faults retain row/data/return/WCK obligations until witnessed reset cancellation.",
            "Retention deadlines suspend after trust loss; reset cancellations do not prove physical completion.",
            "No actual-channel ECC forwarding/merging, vendor/electrical or final qualification closure.",
        ],
    )
