"""Derive opposite-request timing eligibility from decoded external CA history.

The queue ledger supplies identities and geometry only. No DUT ready timer,
eligibility bit, selected entry or byte counter is trusted. Probes use a copy
of the independent DRAM oracle and do not change the actual command replay.
"""

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

from contracts.commands import access, activate, maintenance
from system.dram import DramModel, decode
from system.host import Violation, require


def geometry(event: dict) -> tuple[int, int, int, int]:
    values = tuple(event[k] for k in ("rank", "bank", "row", "column"))
    rank, bank, row, column = values
    require(
        all(type(v) is int for v in values)
        and rank == 0
        and 0 <= bank < 16
        and 0 <= row < 32768
        and 0 <= column < 64
        and (event["bytes"] == 32 or column % 2 == 0),
        "cap physical geometry",
    )
    return rank, bank, row, column


def footprint(event: dict) -> set[tuple[int, int, int, int, int]]:
    rank, bank, row, column = geometry(event)
    return {(event["channel"], rank, bank, row, column + n) for n in range(event["bytes"] // 32)}


def probe(model: DramModel, at: int, request: dict) -> tuple[bool, str]:
    """Check the request's next ACT pair, PRE or ACCESS at the DRAM edge.

    Shared command-pair and WCK reservations belong to arbitration, not the
    per-request timing predicate. Remove those only in this hypothetical copy;
    the actual replay still enforces every pair and synchronization command.
    Caps, weights and QoS do not turn ready opposite work into absent work.
    """
    rank, bank, row, column = geometry(request)
    trial = copy.deepcopy(model)
    trial.pending = trial.sync = None
    opened = trial.banks[rank][bank].row
    if opened is None:
        commands = activate(row, bank)
    elif opened != row:
        commands = (maintenance("PRE", bank, False),)
    else:
        commands = (access(bool(request["write"]), request["bytes"] // 2, bank, column),)
    try:
        for offset, ca in enumerate(commands):
            trial.command(at + offset, 1 << rank, ca)
    except Violation as error:
        return False, str(error)
    return True, decode(commands[0]).kind


class Eligibility:
    def __init__(self, raw: Path, profile: dict):
        require(profile["ranks"] == 1, "cap eligibility requires one rank")
        self.models = [DramModel(profile) for _ in range(2)]
        self.delay = profile["management_ck"]["tctrl_delay"]
        self.samples = [[], []]
        self.positions = [0, 0]
        self.reasons = Counter()
        self.concurrent_issues = [0, 0]
        self.eligible_issues = [0, 0]
        self.borrowed_with_opposite = [0, 0]
        self.bound_issues = []
        self.raw_sha256 = hashlib.sha256(raw.read_bytes()).hexdigest()
        ended = False
        with raw.open() as stream:
            header = json.loads(next(stream))
            require(header["kind"] == "header" and header["channels"] == 2, "cap raw observation header")
            for line in stream:
                event = json.loads(line)
                require(not ended, "cap raw event after end")
                if event["kind"] == "end":
                    ended = True
                elif event["kind"] == "phy":
                    channel = event["channel"]
                    require(channel in (0, 1), "cap raw channel")
                    require(event["ck"] == len(self.samples[channel]), "cap raw missing/repeated CK")
                    self.samples[channel].append(event)
        require(ended and all(self.samples), "cap raw missing final boundary")

    def advance(self, channel: int, ck: int) -> DramModel:
        samples, model = self.samples[channel], self.models[channel]
        position = self.positions[channel]
        while position < len(samples) and samples[position]["ck"] < ck:
            e = samples[position]
            model.command(e["ck"] + self.delay, e["cs"], e["ca"])
            position += 1
        self.positions[channel] = position
        return model

    def issue(self, event: dict, live: dict, borrowed: bool) -> bool:
        channel, ck = event["channel"], event["ck"]
        model = self.advance(channel, ck)
        require(ck < len(self.samples[channel]), "cap issue outside raw trace")
        raw = self.samples[channel][ck]
        command = decode(raw["ca"]) if raw["cs"] else None
        rank, bank, row, column = geometry(event)
        require(
            command is not None
            and command.kind == ("WR" if event["write"] else "RD")
            and raw["cs"] == 1 << rank
            and (command.bank, model.banks[rank][bank].row, command.operand, command.beats)
            == (bank, row, column, event["bytes"] // 2),
            "cap issue disagrees with decoded physical command",
        )
        # An admission sampled at this edge enters the queue after arbitration.
        pending = [r for r in live.values() if r["channel"] == channel and r["ck"] < ck]
        opposite = [r for r in pending if r["write"] != event["write"]]
        eligible = False
        for request in opposite:
            ready, reason = probe(model, ck + self.delay, request)
            eligible |= ready
            self.reasons[("ready: " if ready else "blocked: ") + reason] += 1
        if opposite:
            self.concurrent_issues[channel] += 1
            self.eligible_issues[channel] += int(eligible)
            self.borrowed_with_opposite[channel] += int(borrowed)
        self.bound_issues.append((channel, ck))
        return eligible

    def finish(self, require_mixed_borrowing: bool) -> dict:
        for channel in range(2):
            self.advance(channel, len(self.samples[channel]))
        actual = {
            (channel, event["ck"])
            for channel in range(2)
            for event in self.samples[channel]
            if event["cs"] and decode(event["ca"]).kind in ("RD", "WR", "MWR")
        }
        require(
            len(self.bound_issues) == len(set(self.bound_issues)) and set(self.bound_issues) == actual,
            "cap ACCESS/physical command bijection",
        )
        if require_mixed_borrowing:
            require(all(self.borrowed_with_opposite), "missing borrowing with live ineligible opposite requests")
            require(all(self.eligible_issues), "missing eligible opposite requests")
        return {
            "raw_trace_sha256": self.raw_sha256,
            "issues_with_opposite_by_channel": self.concurrent_issues,
            "issues_with_eligible_opposite_by_channel": self.eligible_issues,
            "borrowed_issues_with_ineligible_opposite_by_channel": self.borrowed_with_opposite,
            "independent_next_command_probes": dict(sorted(self.reasons.items())),
            "limits": [
                "One-rank plain disjoint-address traffic; eligibility uses independent next-command timing, "
                "including ACT pairs, PRE recovery, turnarounds and physical DQ occupancy.",
                "Shared WCK/pair arbitration, caps, weights and QoS do not mask ready opposite requests. "
                "Actual command replay retains all pair and synchronization checks.",
            ],
        }
