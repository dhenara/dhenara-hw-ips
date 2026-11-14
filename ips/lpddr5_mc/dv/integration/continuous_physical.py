"""Version-two C1 replay with continuous independent physical state custody.

Each procedural memory preparation is paired with its receiving rising edge.
The prepared procedural return is authoritative only for that preparation;
strict equality with the separately observed receiving bus checks propagation.
Inline requests are expectations, never physical evidence. Trust loss keeps
memory, rows, RAA, WCK and return obligations alive until a consumed destructive
reset. Cold startup is the only reset that needs no previous epoch.
"""

import copy
import hashlib
import json
import sys
from pathlib import Path

from continuous import check as check_structure
from system.host import require

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "channel"))
from physical_stream import PhysicalStream, number  # noqa: E402

PHASE = "negedge_memory_after_prepare_before_sample"
RECEIVING_PHASE = "posedge_before_nba"
PINS = ("cs", "ca", "wck", "write", "read", "return")


def binding_digest(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Replay:
    """One channel; reset is the only operation that replaces its physical owner."""

    def __init__(self, profile):
        self.profile = copy.deepcopy(profile)
        self.physical = self.prepared = self.last = None
        self.intervals = []
        self.flips = []
        self.invalidating = None
        self.lost_trust = False
        self.ever_trusted = False
        self.cold_ended = False
        self.samples = 0
        self.start = None
        self.flip_ids = set()

    def prepare(self, e):
        missing = sorted(set(("ck", "ps", "reset_applied", "reset_n", "destructive", "mr4", "mr43", *PINS)) - e.keys())
        require(not missing, f"continuous memory preparation missing fields: {missing}")
        require(0 <= e["mr4"] < 32 and 0 <= e["mr43"] < 256, "continuous fixture MR4/MR43 width")
        require(self.prepared is None, "duplicate continuous memory preparation")
        require(self.last is not None and e["ck"] == self.last["ck"] + 1, "preparation outside next receiving CK")
        require(e["ps"] - self.last["ps"] == self.profile["ck_ps"] // 2, "memory preparation phase")
        self.prepared = e

    def close(self, ck, *, cancelled):
        if self.physical is not None:
            self.intervals.append(dict(start_ck=self.start, end_ck=ck, **self.physical.summary(cancelled=cancelled)))
            self.physical = None

    def sample(self, e):
        old = self.last
        cold_release = False
        require(e["ck"] == (old["ck"] + 1 if old else 0), "missing/repeated physical receiving CK")
        if old is None:
            require(
                not e["reset_n"] and not any(number(e[k]) for k in PINS), "first receiving edge must be cold idle reset"
            )
        else:
            q = self.prepared
            require(q is not None and q["ck"] == e["ck"], "missing continuous memory preparation")
            require(e["ps"] - q["ps"] == self.profile["ck_ps"] // 2, "memory receiving phase")
            require(all(number(q[k]) == number(e[k]) for k in PINS), "prepared/receiving physical bus mismatch")
            cold_release = not q["reset_n"] and e["reset_n"] and not self.cold_ended
            require(q["destructive"] == e["destructive"], "destructive pulse changed across preparation")
            require(q["reset_n"] == e["reset_n"] or cold_release, "reset changed across physical preparation")
            if cold_release:
                require(
                    not e["trusted"] and not e["config_valid"] and not any(number(e[k]) for k in PINS),
                    "cold reset release must remain idle and unconfigured",
                )
            if q["reset_applied"]:
                require(not q["reset_n"] or e["destructive"], "live fixture reset without destructive consumption")
            require(
                bool(q["reset_applied"]) == bool(not q["reset_n"] or e["destructive"]),
                "missing physical reset application",
            )
        if self.invalidating is not None:
            require(
                e["boot"] == self.invalidating
                and not any(e[k] for k in ("trusted", "config_valid", "trained", "enabled")),
                "destructive reset missing next-edge epoch invalidation",
            )
            self.invalidating = None
        reset = not e["reset_n"] or bool(e["destructive"]) or cold_release
        if e["destructive"]:
            require(
                e["confirmed"] and e["settled"] and not e["enabled"],
                "physical destructive reset before settlement/confirmation",
            )
            require(old is not None and not old["destructive"], "repeated destructive reset pulse")
            require(e["boot"] == old["boot"] + 1, "destructive observation missing incremented boot epoch")
            self.invalidating = e["boot"]
        elif not e["reset_n"]:
            require(not self.cold_ended, "live asynchronous reset cannot erase physical obligations")
        if reset:
            self.close(e["ck"], cancelled=True)
            self.lost_trust = self.ever_trusted = False
        else:
            self.cold_ended = True
            if self.physical is None:
                self.physical = PhysicalStream(self.profile)
                self.start = e["ck"]
            if old and old["trusted"] and not e["trusted"]:
                self.lost_trust = True
            if e["trusted"]:
                require(not self.lost_trust, "trust restored without destructive physical reset")
                self.ever_trusted = True
                require(
                    e["training_epoch"] == e["boot"] and e["trained"] and e["config_valid"],
                    "stale physical service epoch",
                )
            # This bounded C1 binding has one numeric profile in each epoch.
            # Do not reinterpret an unsupported FSP/PHY-owner interval.
            if not e["config_valid"]:
                require(
                    not e["trusted"] and not any(number(e[k]) for k in PINS),
                    "unconfigured physical startup must remain idle",
                )
                require(e["ratio"] in (0, self.profile["ratio"]), "unconfigured physical ratio")
            else:
                require(e["ratio"] == self.profile["ratio"], "C1 physical numeric ratio binding")
            require(not (e["fsp"] or e["master"]), "C1 physical profile/PHY-owner interval needs explicit binding")
            q = self.prepared
            self.physical.sample(dict(e, mr4=q["mr4"], mr43=q["mr43"], time_ps=e["ps"]))
        self.prepared = None
        self.last = e
        self.samples += 1

    def event(self, e):
        require(self.last is not None and e["ck"] == self.last["ck"], "physical event outside receiving CK")
        require(self.physical is not None, "physical event during reset")
        if e["kind"] == "inline_physical":
            require(
                self.last["trusted"] and not (self.last["fault"] or self.last["host_fault"]),
                "physical admission after trust loss",
            )
            require(
                (e["boot"], e["config"]) == (self.last["boot"], self.last["config"]), "stale physical request epoch"
            )
            self.physical.expect(e)
        elif e["kind"] == "inline_result":
            self.physical.result(e)

    def flip(self, e):
        # A fixture writes this immediately at the actual external byte XOR.
        # It is not inferred from ECC output or a DUT memory snapshot.
        require(self.physical is not None and self.last is not None, "memory flip before physical epoch")
        require(
            e["ck"] == self.last["ck"] + 1 and e["ps"] == self.last["ps"] + self.profile["ck_ps"] // 2,
            "memory flip phase",
        )
        require(e["id"] not in self.flip_ids, "duplicate external memory flip")
        require(
            not self.physical.requests
            and not self.physical.bursts
            and not any(self.physical.completion.pending.values()),
            "external memory flip before independent physical data drain",
        )
        address, rank, mask = e["address"], e["rank"], e["mask"]
        require(
            0 <= address < 1 << 30 and 0 <= rank < self.profile["ranks"] and 0 < mask < 256,
            "external memory flip address/mask",
        )
        key = (rank, address >> 11 & 15, address >> 15, address & 2047)
        memory = self.physical.phy.dram.memory
        memory[key] = memory.get(key, 0) ^ mask
        self.flip_ids.add(e["id"])
        self.flips.append(dict(e))

    def finish(self, ck):
        require(self.last is not None and self.last["ck"] + 1 == ck, "physical missing final CK")
        require(self.prepared is None and self.invalidating is None, "unfinished physical preparation/reset tail")
        self.close(ck, cancelled=False)
        return dict(samples=self.samples, intervals=self.intervals, external_memory_flips=self.flips)


def check(events, profile):
    header = events[0]
    require(
        header["kind"] == "header"
        and header["version"] == 2
        and header["scope"] == "inline_lifecycle"
        and header["phase"] == PHASE
        and header["receiving_phase"] == RECEIVING_PHASE,
        "C1 physical observation header/phase",
    )
    p = copy.deepcopy(profile)
    p["ranks"] = header["ranks"]
    require(
        header["profile_sha256"] == binding_digest(p) and header["profile_number"] == 0,
        "C1 physical numeric profile binding",
    )
    require(p["ck_ps"] == 1250 and p["ratio"] == 4, "C1 physical fixture clock/profile binding")
    require(1 <= header["channels"] <= 2 and 1 <= header["ranks"] <= 2, "C1 physical channel/rank binding")
    channels = [Replay(p) for _ in range(header["channels"])]
    structural = [dict(header, version=1)]
    ended = False
    for e in events[1:]:
        require(not ended, "physical event after end")
        kind = e["kind"]
        if kind == "end":
            require(e["physical_closed"] == 1, "continuous physical cutoff was not closed")
            result = [c.finish(e["ck"]) for c in channels]
            structural.append(e)
            ended = True
            continue
        require(type(e["channel"]) is int and 0 <= e["channel"] < len(channels), "physical event channel")
        c = channels[e["channel"]]
        if kind == "prepared":
            c.prepare(e)
        elif kind == "memory_flip":
            c.flip(e)
        elif kind == "transition":
            c.sample(e)
            structural.append(e)
        elif kind in ("inline_admit", "inline_physical", "inline_result", "inline_complete"):
            c.event(e)
            structural.append(e)
        else:
            raise ValueError("unknown continuous physical event")
    require(ended, "continuous physical trace missing end")
    require(
        header["external_flips"] == 1 and sum(len(c.flips) for c in channels) == 1,
        "missing/duplicate C1 external memory flip",
    )
    # Structural and inline semantics remain the existing owner's responsibility.
    structure = check_structure(structural, p)
    return dict(
        structure=structure["channels"],
        physical_channels=result,
        scope="continuous C1 decoded CA, rows, data-CS, pin bytes and returned slices",
        profile_sha256=binding_digest(p),
        limitations=[
            "Retention deadlines suspend after trust loss; row/data/RAA/return/WCK custody continues "
            "until witnessed destructive reset.",
            "This C1 binding excludes FSP and external PHY-master ownership; "
            "neither is inferred from absent observations.",
            "Synthetic qualification does not establish simulator phase behavior or a connected RTL pass.",
        ],
    )
