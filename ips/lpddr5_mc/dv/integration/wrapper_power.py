"""Fixed-profile real-wrapper automatic power replay.

The source, inline, host-completion and physical owners are the existing
independent wrapper owners. This adapter adds sampled low-power controls and
power entry/exit checks; it never infers settlement from DUT drain flags.
FSP/ratio transitions require the separate explicit multi-profile binding.
"""

import hashlib
import json
from pathlib import Path

from system.host import require
from wrapper_management import (
    Run,
    digest,
    profile_binding,
    qualify_address_coverage,
    qualify_phases,
)
from wrapper_master import PhysicalCommands


class PowerWitness:
    """Consumed controls paired to independently decoded physical state."""

    def __init__(self, policy, idle_ck=64):
        require(policy in (1, 2) and idle_ck > 0, "unsupported automatic power fixture")
        self.policy, self.idle_ck = policy, idle_ck
        self.edge = None
        self.requested = False
        self.entered = self.exited = self.updates = self.deferred = 0
        self.sleeping = False
        self.entry_updates = 0

    def consume(self, e, *, source_empty, physical_ready, inline_empty):
        require(
            e["policy"] == self.policy and e["idle_ck"] == self.idle_ck,
            "unbound power policy",
        )
        if self.edge is not None:
            require(
                e["tick"] == self.edge["tick"] + 1 and e["ps"] == self.edge["ps"] + 1250,
                "power edge gap",
            )
        request = bool(e["ctrl_req"] or e["data_req"])
        require(e["ctrl_req"] == e["data_req"], "split low-power request")
        if request and not self.requested:
            require(
                source_empty and physical_ready and inline_empty,
                "power before independent source/pin/ECC drain",
            )
        if not source_empty:
            require(
                not request,
                "automatic power while accepted source responses remain live",
            )
            self.deferred += 1
        if e["ctrlupd_req"]:
            self.updates += 1
        self.edge, self.requested = e, request

    def physical(self, *, sleeping, source_empty, was_drained, inline_empty):
        if sleeping and not self.sleeping:
            require(self.edge is not None, "power pins without consumed control witness")
            require(
                all(self.edge[k] for k in ("ctrl_req", "data_req", "ctrl_ack", "data_ack")),
                "power pins before sampled acceptance",
            )
            require(
                source_empty and was_drained and inline_empty,
                "power pins before independent drain",
            )
            self.entered += 1
            self.entry_updates = self.updates
        elif self.sleeping and not sleeping:
            require(
                self.edge is not None
                and not any(self.edge[k] for k in ("ctrl_req", "data_req", "ctrl_ack", "data_ack")),
                "power exit before sampled ACK withdrawal",
            )
            require(
                self.policy == 1 or self.updates > self.entry_updates,
                "SRX without actual update request",
            )
            self.exited += 1
        self.sleeping = sleeping

    def finish(self):
        require(
            self.entered >= 1 and self.exited == self.entered and not self.sleeping,
            "missing automatic power entry/wake tail",
        )
        require(self.deferred >= 128, "missing held-source power deferral interval")
        return {
            "policy": self.policy,
            "entries": self.entered,
            "exits": self.exited,
            "deferred_edges": self.deferred,
            "update_edges": self.updates,
        }


def qualify_power_edges(records):
    reference = {(e["channel"], e["tick"]): e for e in records if e["kind"] == "management_edge"}
    witnesses = [e for e in records if e["kind"] == "power_edge"]
    # Same-timestamp coroutines across channels may run in either order. Per
    # channel sequence is strict; duplicates/missing records still fail.
    require(len(witnesses) == len(reference), "missing/duplicate power witness")
    for c in (0, 1):
        selected = [e for e in witnesses if e["channel"] == c]
        require(
            [e["tick"] for e in selected] == list(range(len(selected))),
            "reordered power witness",
        )
    for e in witnesses:
        key = e["channel"], e["tick"]
        require(key in reference and e["ps"] == reference[key]["ps"], "power witness phase")
        require(
            all(
                k in e
                for k in (
                    "ctrl_req",
                    "data_req",
                    "ctrl_ack",
                    "data_ack",
                    "ctrlupd_req",
                    "wake",
                    "policy",
                    "idle_ck",
                )
            ),
            "incomplete power witness",
        )
    return len(witnesses)


class PowerRun(Run):
    def __init__(self, p, policy, records):
        super().__init__(p, selected_cases=[], records=records)
        self.pins = PhysicalCommands(records, p)
        self.policy = policy
        self.power = [PowerWitness(policy), PowerWitness(policy)]
        self.markers = {}
        self.active = False
        self.finished = False
        self.power_edges = {(e["channel"], e["tick"]): e for e in records if e["kind"] == "power_edge"}

    def sample(self, e):
        c = e["channel"]
        physical = self.physical[c]
        was_drained = physical is not None and physical.drained()
        super().sample(e)
        physical = self.physical[c]
        if self.active and physical is not None:
            require(
                not any(e[k] for k in ("fault", "host_fault", "destructive", "memory_reset")),
                "unexpected power fault/reset",
            )
            state = physical.phy.dram.power[0]
            require(
                state is None or state[0] == ("PDE" if self.policy == 1 else "SRE"),
                "wrong physical power mode",
            )
            self.power[c].physical(
                sleeping=physical.phy.dram.power[0] is not None,
                source_empty=self.source.settled(),
                was_drained=was_drained,
                inline_empty=self.inline[c].live is None,
            )
            if e["enabled"]:
                require(
                    physical.phy.dram.power[0] is None,
                    "host enabled during physical sleep",
                )
                require(
                    physical.phy.dram.last_ck >= physical.phy.dram.rank_ready[0],
                    "host enabled before physical wake recovery",
                )

    def event(self, e):
        if e["kind"] == "power_edge":
            c = e["channel"]
            if self.active:
                physical = self.physical[c]
                require(physical is not None, "power witness without physical owner")
                self.power[c].consume(
                    e,
                    source_empty=self.source.settled(),
                    physical_ready=physical.drained(),
                    inline_empty=self.inline[c].live is None,
                )
            return
        if e["kind"] in ("aw", "ar") and "power_sleeping" in self.markers:
            for p in self.physical:
                require(
                    p is not None and p.phy.dram.power[0] is None and p.phy.dram.last_ck >= p.phy.dram.rank_ready[0],
                    "source acceptance before physical wake recovery",
                )
        super().event(e)

    def marker(self, e):
        name = e["name"]
        require(
            name.startswith("power_") and name not in self.markers,
            "unexpected/duplicate power marker",
        )
        expected = [
            "power_begin",
            "power_dirty",
            "power_deferred",
            "power_source_settled",
            "power_sleeping",
            "power_wake_offer",
            "power_preserved",
            "power_end",
        ]
        require(name == expected[len(self.markers)], "missing/reordered power marker")
        self.markers[name] = e["ps"]
        if name == "power_begin":
            self.active = True
        elif name == "power_dirty":
            require(
                not self.source.settled() and self.inline[0].dirty,
                "power stimulus lacks independent dirty inline/source work",
            )
            require(
                (1, "b") in self.source.held and (1, "r") in self.source.held,
                "power stimulus lacks exposed held B/R",
            )
        elif name == "power_deferred":
            require(
                e["ps"] - self.markers["power_dirty"] >= 128 * self.p["ck_ps"],
                "power deferral shorter than configured hold",
            )
            require(
                not self.source.settled() and all(w.entered == 0 for w in self.power),
                "power did not defer real held work",
            )
        elif name == "power_source_settled":
            require(self.source.settled(), "power marker before real W/R/B settlement")
        elif name == "power_sleeping":
            require(
                all(w.sleeping for w in self.power),
                "power marker before both independent pin entries",
            )
        elif name == "power_wake_offer":
            minimum = 40 if self.policy == 1 else 3500
            require(
                e["ps"] - self.markers["power_sleeping"] >= minimum * self.p["ck_ps"],
                "power residence shorter than selected fixture",
            )
        elif name == "power_preserved":
            require(self.source.settled(), "preservation before real response settlement")
            for port, ident, address in ((0, 204, 0), (1, 205, 64)):
                require(
                    any(
                        t.done
                        and not t.write
                        and t.address.port == port
                        and t.address.id == ident
                        and t.address.address == address
                        for t in self.source.host.transactions.values()
                    ),
                    "missing preserved source read",
                )
            require(
                all(w.exited >= 1 for w in self.power),
                "preserved source service before power exit",
            )
        elif name == "power_end":
            require(
                self.source.settled()
                and all(p is not None and p.drained() for p in self.physical)
                and all(i.live is None for i in self.inline),
                "power final independent drain missing",
            )
            self.host_completions.checkpoint(e)
            self.finished = True

    def finish(self):
        require(self.finished, "missing power terminal marker")
        self.source.host.finish()
        require(
            not self.addresses.held and not self.addresses.offers,
            "unconsumed source address tail",
        )
        return {
            "power": [w.finish() for w in self.power],
            "source": dict(self.source.counts),
            "host_completions": self.host_completions.finish(),
            "physical": [p.summary() for p in self.physical],
            "physical_ca_samples": len(self.pins.pins),
            "markers": self.markers,
            "profile_sha256": digest(self.p),
            "limitations": [
                "Fixed profile0, P2/C2/R1, AXI128, depth16, inline ECC and CK2 selection only.",
                "No FSP/ratio transitions, power decline/fault, two-rank or forwarding/merging qualification.",
            ],
        }


def check(wrapper_path, source_path, policy):
    records = [json.loads(line) for line in Path(wrapper_path).read_text().splitlines()]
    source = [json.loads(line) for line in Path(source_path).read_text().splitlines()]
    header, p = records[0], profile_binding()
    require(
        header["kind"] == "header" and header["profile_sha256"] == digest(p),
        "power profile binding",
    )
    require(
        header.get("power_policy") == policy and header.get("power_phase") == "posedge_before_NBA",
        "unbound power producer",
    )
    require(
        records[-1]["kind"] == source[-1]["kind"] == "end",
        "missing power/source terminal tail",
    )
    phases = qualify_phases(records[1:-1], header)
    addresses = qualify_address_coverage(records, source)
    power_edges = qualify_power_edges(records[1:-1])
    order = {
        "sample": -5,
        "window_sample": -4,
        "host_edge": -3,
        "host_reset": -3,
        "window_edge": -2,
        "management_edge": -2,
        "power_edge": -1,
        "marker": 0,
        "axi": 1,
        "address": 2,
        "aw": 3,
        "ar": 4,
        "w": 5,
        "inline_admit": 6,
        "inline_physical": 7,
        "inline_result": 8,
        "inline_complete": 9,
        "b": 10,
        "r": 11,
    }
    events = [e for e in records[1:-1] if e["kind"] != "edge"]
    events.extend(e for e in source[1:-1] if e["kind"] != "phy")
    events.sort(
        key=lambda e: (
            e["ps"],
            order.get(e["kind"], 0),
            e.get("port", e.get("channel", 0)),
        )
    )
    run = PowerRun(p, policy, records)
    for e in events:
        run.event(e)
    return run.finish() | {
        "phase_samples": phases,
        "address_samples": addresses,
        "power_edges": power_edges,
        "raw_sha256": hashlib.sha256(Path(wrapper_path).read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(Path(source_path).read_bytes()).hexdigest(),
    }
