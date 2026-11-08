"""Measure actual combined scheduler service, named suspensions and the tail.

The fixture uses disjoint plain full granules. Row/timing/WCK delays remain
eligible CK; actual maintenance ownership and missing response capacity are
observed explicitly. Independent pin/host replay separately checks effects.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path

from system.host import Violation, require
from system.measurement import Fairness


def check(path: Path, physical: dict) -> dict:
    live = [{}, {}]
    seen = [set(), set()]
    models = [Fairness(), Fairness()]
    last = [None, None]
    maximum = [0, 0]
    eligible = [0, 0]
    worst_wait = [0, 0]
    suspended_live = [Counter(), Counter()]
    grants, urgent, escapes = Counter(), Counter(), Counter()
    admitted = Counter()
    ended = False
    header = None
    with path.open() as stream:
        for line in stream:
            event = json.loads(line)
            require(not ended, "fairness record after final boundary")
            kind = event["kind"]
            if header is None:
                require(
                    kind == "header"
                    and event["version"] in (2, 3)
                    and (event["version"] == 2 or event["rows"] in (0, 1))
                    and event["profile"] == 4
                    and event["mode"] in (0, 1, 2)
                    and event["divisor"] in (1, 2, 4)
                    and (event["depth"], event["age"], event["tick"], event["gap"]) == (64, 32, 8, 64),
                    "combined fairness configuration",
                )
                header = event
                continue
            if kind == "end":
                require(all(v is not None and event["ck"] == v + 1 for v in last), "fairness final CK boundary")
                require(not any(live), "unserved combined fairness tail")
                ended = True
                continue
            require(kind == "cycle" and event["channel"] in (0, 1), "combined fairness cycle")
            c, ck = event["channel"], event["ck"]
            require(ck == (0 if last[c] is None else last[c] + 1), "missing combined fairness CK")
            last[c] = ck
            request, issue = event["admit"], event["issue"]
            for item in (request, issue):
                if item is not None:
                    require(
                        item["write"] in (0, 1)
                        and item["port"] in (0, 1)
                        and item["qos"] in range(4)
                        and item["bytes"] == 64,
                        "combined fairness descriptor",
                    )
            if request is not None:
                key = request["tag"], request["generation"], request["write"]
                require(key not in live[c] and key not in seen[c], "duplicate combined fairness admission")
                live[c][key] = request, ck, eligible[c]
                admitted[c, request["write"], request["port"], request["qos"]] += 1
            maximum[c] = max(maximum[c], len(live[c]))
            require(len(live[c]) <= 64, "combined fairness D64 capacity")
            require(all(event[n] in (0, 1) for n in ("pair", "maintenance", "ownership", "response")), "pause flags")
            pause = (
                0
                if event["pair"]
                else 3
                if event["maintenance"]
                else 2
                if event["ownership"]
                else 1
                if event["response"]
                else 0
            )
            require(event["pause"] == pause, "fairness suspension disagrees with observed boundary")
            reason = {0: None, 1: "response", 2: "ownership", 3: "maintenance"}[pause]
            if live[c] and reason:
                suspended_live[c][reason] += 1
            # These operational workloads never deliberately yield ownership.
            # Boot/quiesce may suspend only an empty ledger.
            require(not (live[c] and reason == "ownership"), "unexpected live ownership suspension")
            key = None if issue is None else (issue["tag"], issue["generation"], issue["write"])
            if issue is not None:
                require(key in live[c] and key not in seen[c], "unmatched/duplicate combined fairness issue")
                require(issue == live[c][key][0], "combined fairness identity changed")
            models[c].step(set(live[c]), key, reason)
            if reason is None:
                eligible[c] += 1
            if issue is not None:
                _, admission_ck, admission_eligible = live[c][key]
                worst_wait[c] = max(worst_wait[c], eligible[c] - admission_eligible)
                group = c, issue["write"], issue["port"], issue["qos"]
                grants[group] += 1
                if ck - admission_ck >= 264:
                    urgent[group] += 1
                    if any(item[0]["qos"] > issue["qos"] for item in live[c].values()):
                        escapes[group] += 1
                del live[c][key]
                seen[c].add(key)
    require(ended, "missing combined fairness final boundary")
    directions = (0, 1) if header["mode"] == 2 else (header["mode"],)
    groups = {(c, d, p, q) for c in range(2) for d in directions for p in range(2) for q in range(4)}
    require(set(grants) == groups and set(admitted) == groups, "combined fairness source/class coverage")
    require(all(grants[g] == admitted[g] == 16 for g in groups), "combined fairness source/class totals")
    if header.get("rows", 0):
        require(set(urgent) == groups, "missing row-churn aged source/class grants")
    payload = [16384 if d in directions else 0 for d in range(2)]
    require([physical["read_bytes"], physical["write_bytes"]] == payload, "combined fairness physical payload")
    return {
        "mode": header["mode"],
        "divisor": header["divisor"],
        "profile": header["profile"],
        "row_churn": bool(header.get("rows", 0)),
        "conditional_bound_ck": models[0].bound,
        "maximum_issue_gap_ck": 64,
        "measured_issue_gap_ck": [m.worst_gap for m in models],
        "maximum_eligible_wait_ck": worst_wait,
        "maximum_live_by_channel": maximum,
        "observed_until_ck": last,
        "eligible_ck": eligible,
        "suspended_ck": [m.suspended for m in models],
        "suspended_with_live_ck": suspended_live,
        "grants_by_channel_direction_port_class": {"/".join(map(str, k)): v for k, v in sorted(grants.items())},
        "aged_grants_by_channel_direction_port_class": {"/".join(map(str, k)): v for k, v in sorted(urgent.items())},
        "aged_grants_with_higher_qos_pending": {"/".join(map(str, k)): v for k, v in sorted(escapes.items())},
        "limits": [
            "Actual combined AXI/CDC/channel grants with independent pin/host payload replay.",
            "Disjoint 64-byte plain traffic; no overlap, cap, missing-W or ECC exclusion after scheduler admission.",
            "Suspensions observe actual owner/maintenance/response-capacity inputs. Row/timing/WCK costs remain in G.",
            "Aged-grant observations describe only witnessed cases; absent groups do not establish urgent-age escape.",
        ],
    }


def qualify(path: Path, temporary: Path, physical: dict) -> dict:
    result = check(path, physical)
    events = [json.loads(line) for line in path.read_text().splitlines()]
    issues = [i for i, e in enumerate(events) if e.get("issue") is not None]
    first = issues[0]
    second = next(i for i in issues[1:] if events[i]["channel"] == events[first]["channel"])
    variants = (
        (
            "duplicate_issue",
            {second: {**events[second], "issue": events[first]["issue"]}},
            "unmatched/duplicate combined fairness issue",
        ),
        (
            "starved_eligible_tail",
            {i: {**events[i], "issue": None} for i in issues},
            "issue gap G including trace tail",
        ),
        ("false_suspension", {first: {**events[first], "pause": 3}}, "suspension disagrees"),
        ("missing_final_boundary", {len(events) - 1: None}, "missing combined fairness final boundary"),
    )
    result["trace_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    result["negative_cases"] = {}
    for name, replacements, marker in variants:
        temporary.write_text(
            "".join(
                json.dumps(replacements.get(i, event)) + "\n"
                for i, event in enumerate(events)
                if replacements.get(i, event) is not None
            )
        )
        try:
            check(temporary, physical)
        except Violation as error:
            require(marker in str(error), "combined fairness mutation failed at unrelated boundary: " + str(error))
            result["negative_cases"][name] = {
                "reason": str(error),
                "mutated_sha256": hashlib.sha256(temporary.read_bytes()).hexdigest(),
            }
        else:
            raise RuntimeError("Combined fairness checker accepted mutation: " + name)
        finally:
            temporary.unlink()
    return result
