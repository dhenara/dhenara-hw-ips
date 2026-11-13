"""Independent request/phase ledger for version-4 wrapper host observations.

Each record samples both channels and the core ledger before NBA. The next
record observes that edge's counter update. The DUT selection is cross-checked
against retained rows; accepted requests, not those rows or a match bit, grant
permission to complete. Cancellation never forgets an identity or its phases.
"""

from collections import Counter

from system.host import require
from system.trace import number

MAX_COUNTER = (1 << 32) - 1
IDENTITY = "tag generation boot config".split()
PARTICIPANT = "slot port source_slot source_generation axi_id merged follower first beats length".split()
HOST_FIELDS = (
    "reset_n fatal fault recover enable settled config_valid cfg_valid cfg_ready config_boot config_epoch "
    "cfg_boot cfg_epoch count external_abort posted_early ledger channels"
).split()


def identity(value, channel):
    fields = tuple(number(value[k]) for k in IDENTITY)
    require(
        all(0 <= n < 1 << bits for n, bits in zip(fields, (64, 16, 32, 32), strict=True)),
        "host identity width",
    )
    require(value["write"] in (0, 1), "host completion type")
    return (channel, value["write"], *fields)


class HostCompletions:
    def __init__(self):
        self.previous = None
        self.expected = 0
        self.owners = {}
        self.history = []
        self.counts = Counter()
        self.case = None
        self.failed_case = False
        self.needs_baseline = False
        self.guarded = True
        self.recovered = False
        self.baseline_count = 0
        self.baselines = []
        self.reset_pending = False

    def begin(self, case, failed):
        require(not self.needs_baseline, "previous abort lacks recovery baseline")
        self.case, self.failed_case = case, bool(failed)
        self.guarded = True
        self.recovered = False

    def reset(self, e):
        """An actual falling core-reset edge is observed before its reset NBA."""
        require(self.previous is not None and e["ps"] >= self.previous["ps"], "unbound host reset")
        require(not self.reset_pending, "duplicate host reset witness")
        require(e["count"] == self.expected and e["reset_n"] == 0, "unreconciled counter before reset")
        self._cancel(e, "reset")
        self.expected = 0
        self.reset_pending = True
        self.recovered = False
        self.history.append(dict(kind="reset", ps=e["ps"], before=e["count"]))

    def _cancel(self, e, reason):
        for key, owner in self.owners.items():
            if not owner["done"] and owner["cancelled"] is None:
                owner["cancelled"] = (e["tick"], self.case, reason)
                self.history.append(dict(kind="cancel", ps=e["ps"], identity=key, reason=reason))
        if self.case is not None:
            require(self.failed_case, "abort in normal handback")
            self.needs_baseline = True
            self.guarded = False

    @staticmethod
    def _rows(e):
        rows = e["ledger"]
        slots = [r["slot"] for r in rows]
        require(slots == sorted(set(slots)), "duplicate/reordered host ledger slots")
        require(all(r["valid"] == 1 and r["pending"] == 1 for r in rows), "host ledger row not pending")
        return rows

    @staticmethod
    def _matching(rows, key):
        return [r for r in rows if identity(r, r["channel"]) == key]

    def _accept(self, e, channel, request, rows):
        require(not self.needs_baseline, "renewed request before recovery baseline")
        require(e["reset_n"] and not e["fatal"] and not e["fault"] and e["config_valid"], "request in invalid core")
        require(request["channel"] == channel, "request channel wire mismatch")
        require(
            (request["boot"], request["config"]) == (e["config_boot"], e["config_epoch"]),
            "request sideband differs from actual core configuration",
        )
        key = identity(request, channel)
        require(key not in self.owners, "duplicate/reused accepted host identity")
        participants = self._matching(rows, key)
        require(participants, "accepted request absent from pre-edge ledger")
        require(sum(not r["follower"] for r in participants) == 1, "missing/duplicate request leader")
        leader = next(r for r in participants if not r["follower"])
        require(
            (leader["port"], leader["axi_id"], leader["source_generation"])
            == (request["port"], request["axi_id"], request["generation"]),
            "request/source leader identity mismatch",
        )
        if len(participants) > 1:
            require(request["write"] and all(r["merged"] for r in participants), "unsupported merged participants")
        else:
            require(not participants[0]["follower"], "orphan merged follower")
        require(
            all(r["beats"] > 0 and r["first"] + r["beats"] <= r["length"] + 1 for r in participants),
            "invalid host chunk extent",
        )
        require(all(not r["physical"] and not r["integrity"] for r in participants), "new request already completed")
        split = bool(request["write"] and request["write_link"] and request["posted"] and not request["inline"])
        self.owners[key] = dict(
            accepted=e["tick"],
            participants=[tuple(r[k] for k in PARTICIPANT) for r in participants],
            split=split,
            phases=set(),
            done=False,
            cancelled=None,
        )
        self.history.append(dict(kind="accept", ps=e["ps"], identity=key, participants=len(participants)))
        self.counts["requests"] += 1

    def _complete(self, e, channel, value, rows):
        key = identity(value, channel)
        owner = self.owners.get(key)
        require(owner is not None and owner["accepted"] < e["tick"], "completion lacks previously accepted request")
        require(not owner["done"], "duplicate/already completed host identity")
        matches = self._matching(rows, key)
        selected = matches[-1]["slot"] if matches else -1
        require(value["match_slot"] == selected, "DUT match selection differs from pre-edge ledger")
        cancelled = owner["cancelled"]
        if cancelled is None:
            require(matches, "outstanding accepted request lost from host ledger without abort")
        else:
            require(not matches, "canceled host ledger resurrected")
            require(
                not self.guarded and self.needs_baseline and cancelled[1] == self.case,
                "unmatched completion outside canceled recovery interval",
            )
            require(cancelled[0] < e["tick"], "abort cannot unmatch a pre-edge completion")
        physical, integrity = value["physical"], value["integrity"]
        failed, response = value["failed"], value["response"]
        require(all(v in (0, 1) for v in (physical, integrity, failed)), "non-bit completion flags")
        require(response in (0, 2) and bool(response) == bool(failed), "completion failure/response contract")
        if not integrity:
            require(owner["split"] and physical, "unsupported nonterminal completion phase")
            phase = "physical"
        else:
            require(physical or failed, "terminal completion lacks physical/error prerequisite")
            if owner["split"] and physical:
                require("physical" in owner["phases"], "posted write final before physical notice")
            phase = "final"
        require(phase not in owner["phases"], "duplicate completion phase")
        owner["phases"].add(phase)
        owner["done"] = phase == "final"
        self.history.append(dict(kind="drop" if cancelled else "matched", ps=e["ps"], identity=key, phase=phase))
        self.counts["unmatched" if cancelled else "matched"] += 1
        return not matches

    def edge(self, e):
        old = self.previous
        require(0 <= e["count"] <= MAX_COUNTER, "counter width/wrap")
        require(e["count"] == self.expected, "unexplained/missing stale counter update")
        if old is None:
            require(e["tick"] == 0 and not e["reset_n"], "host witness must start in actual reset")
        else:
            require(
                e["tick"] == old["tick"] + 1 and e["ps"] == old["ps"] + 1250,
                "missing/duplicate/reordered host edge",
            )
            require(not old["reset_n"] or e["reset_n"] or self.reset_pending, "missing actual core reset edge")
        require(e["external_abort"] == 1 and e["posted_early"] == 0, "unsupported host owner policy")
        require([c["channel"] for c in e["channels"]] == [0, 1], "missing/reordered host channel edge")
        rows = self._rows(e)
        for key, owner in self.owners.items():
            matches = self._matching(rows, key)
            if owner["done"] or owner["cancelled"] is not None or not e["reset_n"]:
                require(not matches, "retired/canceled host ledger resurrected")
            else:
                require(
                    [tuple(r[k] for k in PARTICIPANT) for r in matches] == owner["participants"],
                    "accepted request ledger participants changed/disappeared",
                )
                require(
                    all(bool(r["physical"]) == ("physical" in owner["phases"]) and not r["integrity"] for r in matches),
                    "host phase state differs from accepted completion history",
                )
        unmatched = 0
        if e["reset_n"]:
            # Completions use the old ledger. Apply the final abort clear only
            # after processing both channels, as the owning always_ff does.
            for c in e["channels"]:
                if c["completion_valid"] and c["completion_ready"]:
                    unmatched += self._complete(e, c["channel"], c["completion"], rows)
                require(c["completion_ready"] == 1, "host completion READY contract")
            for c in e["channels"]:
                if c["request_valid"] and c["request_ready"]:
                    self._accept(
                        e, c["channel"], c["request"] | {k: c[k] for k in ("inline", "write_link", "posted")}, rows
                    )
            if e["fatal"] or e["fault"]:
                self._cancel(e, "external_abort")
            if e["recover"] and e["settled"] and not e["enable"] and not e["fatal"]:
                require(self.needs_baseline, "host recover without observed abort")
                self.recovered = True
            # Each unmatched channel schedules <= old+1, not a reduction sum.
            self.expected = min(MAX_COUNTER, e["count"] + bool(unmatched))
            if unmatched:
                self.history.append(
                    dict(kind="counter", ps=e["ps"], events=unmatched, before=e["count"], after=self.expected)
                )
        else:
            require(not rows, "host reset did not clear ledger")
            self.expected = 0
        self.counts["edges"] += 1
        self.counts["unmatched_edges"] += bool(unmatched)
        self.previous = e
        self.reset_pending = False

    def baseline(self, e, samples, source_settled, recovered_channels):
        require(self.failed_case and self.needs_baseline and self.recovered, "baseline before observed host recovery")
        require(
            recovered_channels == {0, 1} and len(samples) == 2 and source_settled,
            "baseline before both destructive/source settlements",
        )
        require(self.previous is not None and self.previous["ps"] < e["ps"], "baseline before next observation")
        require(e["count"] == self.expected == self.previous["count"], "baseline hides unreconciled counter")
        require(
            self.previous["settled"]
            and not any(self.previous[k] for k in ("fatal", "fault", "enable", "config_valid")),
            "baseline before completed core recovery",
        )
        require(
            all(
                s is not None
                and s["boot"] == e["boot"]
                and s["reset_n"]
                and s["settled"]
                and s["confirmed"]
                and not s["reset_request"]
                and not any(
                    s[k]
                    for k in (
                        "destructive",
                        "memory_reset",
                        "enabled",
                        "host_fault",
                        "fault",
                        "config_valid",
                        "trained",
                    )
                )
                for s in samples
            ),
            "baseline before complete new-epoch recovery or after renewed admission",
        )
        require(
            e["boot"] > self.previous["config_boot"] and not self.previous["config_valid"],
            "baseline lacks fresh epoch/config fence",
        )
        require(
            not any(not o["done"] and o["cancelled"] is None for o in self.owners.values()),
            "baseline hides live request",
        )
        self.baseline_count = e["count"]
        self.baselines.append(dict(ps=e["ps"], boot=e["boot"], count=e["count"], history_entries=len(self.history)))
        self.needs_baseline = False
        self.guarded = True

    def checkpoint(self, e):
        require(not self.needs_baseline and self.guarded, "service before recovery baseline")
        require(e["count"] == self.expected == self.baseline_count, "post-baseline/normal counter changed")

    def seam(self, e):
        """Bind the separate inline witness to the actual host wires that edge."""
        edge = self.previous
        require(edge is not None and (edge["tick"], edge["ps"]) == (e["tick"], e["ps"]), "inline/host edge binding")
        channel = edge["channels"][e["channel"]]
        request = e["kind"] == "inline_admit"
        value = channel["request" if request else "completion"]
        require(channel["request_valid" if request else "completion_valid"] == 1, "inline/host VALID binding")
        require(channel["request_ready" if request else "completion_ready"] == 1, "inline/host READY binding")
        fields = (
            (*IDENTITY, "write", "bytes", "mask", "data")
            if request
            else (*IDENTITY, "write", "physical", "integrity", "failed", "response", "data")
        )
        require(all(number(e[k]) == number(value[k]) for k in fields), "inline/host payload or actual epoch binding")

    def finish(self):
        require(self.previous is not None and self.previous["count"] == self.expected, "missing next-edge counter tail")
        require(not self.needs_baseline, "unfinished completion recovery accounting")
        require(
            not any(not o["done"] and o["cancelled"] is None for o in self.owners.values()), "live host completion tail"
        )
        return dict(counts=dict(self.counts), baselines=self.baselines, history=self.history, final_count=self.expected)
