"""Write-only finite-window stability, with mandatory raw source/clock evidence.

This supplements the benchmark's unchanged data/pin/protocol checks. It does
not establish payload, scheduler fairness, or an unrestricted latency promise.
All times below are source-edge picoseconds, including the admitted REF bound.
"""

from bisect import bisect_left, bisect_right
from collections import Counter, deque

from system.dram import decode
from system.host import require


def _channel_stability(transactions, refs, *, start, measure_start, measure_end, ref_limit):
    """Assess admitted identities; the public collector first validates evidence."""
    require(
        0 <= start <= measure_start < measure_end and type(ref_limit) is int and ref_limit > 0,
        "write stability window/timing evidence",
    )
    require(transactions and refs and refs == sorted(set(refs)), "write stability REF/transaction inputs")
    require(
        len({(t["port"], t["id"]) for t in transactions}) == len(transactions), "write stability duplicate identity"
    )
    require(
        all(t.get("wlast") is not None and t.get("b") is not None for t in transactions),
        "write stability unsettled W/B tail",
    )
    require(all(t["aw"] <= t["wlast"] < t["b"] for t in transactions), "write stability stage order")
    before = bisect_right(refs, measure_start) - 1
    require(before >= 1 and refs[before - 1] >= start, "write stability missing adjacent warm-up calibration")
    require(measure_start - refs[before] <= ref_limit, "write stability missing preceding REF")
    last = bisect_right(refs, measure_end) - 1
    require(measure_end - refs[last] <= ref_limit, "write stability overdue trailing REF")
    require(
        all(b - a <= ref_limit for a, b in zip(refs[before - 1 : last], refs[before : last + 1], strict=True)),
        "write stability REF gap exceeds admitted maximum",
    )
    times = {key: sorted(t[key] for t in transactions) for key in ("aw", "wlast", "b")}

    def state(at, *, inclusive=False):
        cut = bisect_right if inclusive else bisect_left
        n = {key: cut(values, at) for key, values in times.items()}
        return {
            "aw_no_b": n["aw"] - n["b"],
            "incomplete_w": n["aw"] - n["wlast"],
            "complete_w_no_b": n["wlast"] - n["b"],
        }

    def interval(a, b):
        points = sorted({a, b - 1} | {at for v in times.values() for at in v[bisect_left(v, a) : bisect_left(v, b)]})
        return [(at, state(at, inclusive=True)) for at in points]

    def conservation(a, b):
        counts = {k: bisect_left(v, b) - bisect_left(v, a) for k, v in times.items()}
        left, right = state(a), state(b)
        delta = {k: right[k] - left[k] for k in left}
        require(
            delta
            == {
                "aw_no_b": counts["aw"] - counts["b"],
                "incomplete_w": counts["aw"] - counts["wlast"],
                "complete_w_no_b": counts["wlast"] - counts["b"],
            },
            "write stability conservation",
        )
        return {"events": counts, "before": left, "after": right, "delta": delta}

    warm_a, warm_b = refs[before - 1 : before + 1]
    warm = interval(warm_a, warm_b)
    floor = min(s["complete_w_no_b"] for _, s in warm)
    previous = next(at for at, s in reversed(warm) if s["complete_w_no_b"] == floor)
    calibration = {"start_ps": warm_a, "end_ps": warm_b, "cut_ps": previous, "floor": floor}
    rows, failures = [], set()
    for a, b in zip(refs, refs[1:], strict=False):
        if not measure_start <= a < b <= measure_end:
            continue
        states = interval(a, b)
        cut = next((at for at, s in states if s["complete_w_no_b"] <= floor), None)
        old = (
            []
            if cut is None
            else [[t["port"], t["id"]] for t in transactions if t["aw"] <= previous < t["b"] and t["b"] > cut]
        )
        if cut is None:
            failures.add("service_ready_floor_did_not_recur")
        else:
            if old:
                failures.add("old_aw_survived_recurrence")
            previous = cut
        rows.append(
            {
                "start_ps": a,
                "end_ps": b,
                "cut_ps": cut,
                "unretired_previous_aw": old,
                "minimum_complete_w_no_b": min(s["complete_w_no_b"] for _, s in states),
                "maximum_complete_w_no_b": max(s["complete_w_no_b"] for _, s in states),
                "conservation": conservation(a, b),
            }
        )
    require(len(rows) >= 2, "write stability insufficient complete measured REF intervals")
    cohort = [t for t in transactions if t["aw"] < measure_end and t["b"] >= measure_start]
    slow = [[t["port"], t["id"]] for t in cohort if t["b"] - t["aw"] > 3 * ref_limit]
    if slow:
        failures.add("aw_to_b_age_limit")
    return {
        "passed": not failures,
        "failures": sorted(failures),
        "warmup_calibration": calibration,
        "complete_measured_intervals": len(rows),
        "intervals": rows,
        "slow_identities": slow,
        "maximum_live_aw_to_b_ps": max((t["b"] - t["aw"] for t in cohort), default=0),
        "window_conservation": conservation(measure_start, measure_end),
        "coverage": {
            "preceding_ref_ps": refs[before],
            "last_ref_ps": refs[last],
            "trailing_age_ps": measure_end - refs[last],
            "maximum_ref_interval_ps": ref_limit,
        },
    }


class WriteStability:
    """Collect the fixed benchmark's actual AW/W/B, AXI and REF observations.

    Same-instant observations are admitted atomically so print-process ordering
    cannot turn a missing VALID/sample into evidence. No assumed source-offer
    boolean is accepted. Handshakes must match sampled VALID/READY exactly.
    """

    def __init__(self, profile, start, *, smoke=False):
        require(
            profile["ck_ps"] == 1250
            and profile["ratio"] == 4
            and profile["bank_mode"] == "BG"
            and profile["ranks"] == 1
            and profile["always_on"]
            and not any(profile[k] for k in ("read_link", "write_link", "read_dbi", "write_dbi"))
            and profile["dram_ck"]["trefi"] == 3124,
            "unsupported write stability benchmark profile",
        )
        require(type(start) is int and start >= 0, "write stability start/units")
        self.ck_ps = profile["ck_ps"]
        self.ref_limit = profile["dram_ck"]["trefi"] * self.ck_ps
        self.start, self.smoke = start, smoke
        self.measure_start = start + 10 * 3000 * self.ck_ps
        self.end = start + (2 if smoke else 100) * 3000 * self.ck_ps
        self.header, self.ended, self.last = False, False, -1
        self.batch = []
        self.axi_last, self.phy_last, self.phy_ck = [None, None], [None, None], [-1, -1]
        self.axi_counts, self.phy_counts = [0, 0], [0, 0]
        self.offers = [Counter(), Counter()]
        self.refs, self.pending = [[], []], [deque(), deque()]
        self.transactions, self.live = [{}, {}], [set(), set()]
        self.completed = [0, 0]

    def sample(self, event):
        kind = event["kind"]
        if not self.header:
            require(
                event == {"kind": "header", "version": 1, "ports": 2, "channels": 2, "bus_bytes": 16},
                "write stability header/scope",
            )
            self.header = True
            return
        at = event["ps"]
        require(not self.ended and type(at) is int and at >= 0 and at >= self.last, "write stability trace order/end")
        if at != self.last:
            self._flush()
            self.last = at
        if kind in ("axi", "aw", "w", "b"):
            require(event["port"] in (0, 1), "write stability port scope")
            self.batch.append(event)
        elif kind == "phy":
            c, ck = event["channel"], event["ck"]
            require(c in (0, 1) and event["cs"] in (0, 1), "write stability channel/rank scope")
            require(
                ck == self.phy_ck[c] + 1 and (self.phy_last[c] is None or at - self.phy_last[c] == self.ck_ps),
                "write stability missing PHY clock sample",
            )
            self.phy_ck[c], self.phy_last[c] = ck, at
            self.phy_counts[c] += self.start <= at < self.end
            if event["cs"]:
                command = decode(event["ca"])
                if command.kind == "REF":
                    require(command.all_banks, "write stability unsupported REFpb")
                    self.refs[c].append(at)
        elif kind == "end":
            self._flush()
            self.ended = True
        else:
            require(kind == "engine", "write stability unexpected source event")

    def _flush(self):
        for p in range(2):
            events = [e for e in self.batch if e["port"] == p]
            samples = [e for e in events if e["kind"] == "axi"]
            require(len(samples) <= 1 and (samples or not events), "write stability missing/duplicate AXI sample")
            if not samples:
                continue
            at, flags = samples[0]["ps"], samples[0]["flags"]
            require(type(flags) is int and 0 <= flags < 1024, "write stability sampled flags")
            require(
                self.axi_last[p] is None or at - self.axi_last[p] == 2 * self.ck_ps,
                "write stability missing AXI clock sample",
            )
            self.axi_last[p] = at
            self.axi_counts[p] += self.start <= at < self.end
            kinds = Counter(e["kind"] for e in events)
            require(
                kinds["aw"] == int(bool(flags & 512 and flags & 256))
                and kinds["w"] == int(bool(flags & 32 and flags & 16))
                and kinds["b"] == int(bool(flags & 8)),
                "write stability sampled handshake mismatch",
            )
            if at >= self.start:
                require(flags & 5 == 5, "write stability response ready")
            if not self.smoke and self.measure_start <= at < self.end:
                require(flags & 512 and (not self.pending[p] or flags & 32), "write stability source under-offer")
                self.offers[p]["samples"] += 1
                self.offers[p]["aw_valid"] += bool(flags & 512)
                self.offers[p]["w_valid"] += bool(flags & 32)
                self.offers[p]["w_stalls"] += bool(flags & 32 and not flags & 16)
            # Canonical AW before W allows a same-edge first data handshake.
            for e in sorted(
                (e for e in events if e["kind"] != "axi"), key=lambda e: {"aw": 0, "w": 1, "b": 2}[e["kind"]]
            ):
                require(at >= self.start, "write stability source before start")
                if e["kind"] == "aw":
                    identity = e["id"]
                    require(
                        identity == len(self.transactions[p])
                        and e["length"] == 3
                        and e["size"] == 4
                        and e["burst"] == 1
                        and e["lock"] == 0,
                        "write stability AW identity/shape",
                    )
                    address = int(e["address"], 0) if isinstance(e["address"], str) else e["address"]
                    require(address >= 0 and address % 64 == 0, "write stability AW alignment")
                    self.transactions[p][identity] = {
                        "port": p,
                        "id": identity,
                        "channel": (address >> 6) & 1,
                        "aw": at,
                        "beats": 0,
                    }
                    self.live[p].add(identity)
                    require(len(self.live[p]) <= 64, "write stability credit overflow")
                    self.pending[p].append(identity)
                elif e["kind"] == "w":
                    require(self.pending[p], "write stability W without AW")
                    t = self.transactions[p][self.pending[p][0]]
                    t["beats"] += 1
                    require(bool(e["last"]) == (t["beats"] == 4), "write stability WLAST identity/count")
                    if e["last"]:
                        t["wlast"] = at
                        self.pending[p].popleft()
                else:
                    identity = e["id"]
                    require(
                        identity in self.live[p]
                        and identity == self.completed[p]
                        and e["ready"] == 1
                        and e["response"] == 0,
                        "write stability B identity/status",
                    )
                    t = self.transactions[p][identity]
                    require("wlast" in t and t["wlast"] < at, "write stability B before complete W")
                    t["b"] = at
                    self.live[p].remove(identity)
                    self.completed[p] += 1
        self.batch.clear()

    def finish(self):
        self._flush()
        require(self.header and self.ended, "write stability truncated trace/window")
        require(not any(self.pending) and not any(self.live), "write stability unsettled W/B tail")
        require(self.last >= self.end, "write stability truncated trace/window")
        periods = 2 if self.smoke else 100
        require(
            self.axi_counts == [periods * 1500] * 2 and self.phy_counts == [periods * 3000] * 2,
            "write stability incomplete clock/window coverage",
        )
        require(
            all(t is not None and 0 <= self.last - t < 2 * self.ck_ps for t in self.axi_last)
            and all(t is not None and 0 <= self.last - t < self.ck_ps for t in self.phy_last),
            "write stability missing sampled tail",
        )
        if self.smoke:
            return None
        require(all(o["samples"] == 90 * 1500 for o in self.offers), "write stability absent source evidence")
        tx = [t for port in self.transactions for t in port.values()]
        channels = [
            _channel_stability(
                [t for t in tx if t["channel"] == c],
                self.refs[c],
                start=self.start,
                measure_start=self.measure_start,
                measure_end=self.end,
                ref_limit=self.ref_limit,
            )
            for c in range(2)
        ]
        return {
            "passed": all(c["passed"] for c in channels),
            "method": "write_ref_recurrence_full_aw_turnover",
            "channels": channels,
            "source_offers": [dict(o) for o in self.offers],
            "clock_samples": {"axi": self.axi_counts, "phy": self.phy_counts},
            "aw_to_b_guard_ps": 3 * self.ref_limit,
            "window_ps": [self.measure_start, self.end],
            "scope": "Fixed plain write benchmark; finite observation, not scheduler fairness or universal latency.",
        }
