"""Check actual scheduler admissions/grants, eligible CK and the final tail."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from system.host import Violation, require
from system.measurement import Fairness


def check(path: Path) -> dict:
    cases = []
    case = None
    with path.open() as stream:
        for line in stream:
            event = json.loads(line)
            kind = event["kind"]
            if kind == "start":
                require(case is None, "unfinished fairness case")
                case = dict(event)
                require(
                    case["divisor"] in (1, 2, 4) and case["mode"] in (0, 1, 2) and case["pause_mode"] in range(4),
                    "fairness case configuration",
                )
                live, seen, waits, groups = set(), set(), {}, Counter()
                model, ck, admitted, eligible_ck, worst_wait = Fairness(), 0, 0, 0, 0
            elif kind == "cycle":
                require(case is not None and event["ck"] == ck, "missing fairness CK")
                request, grant, pause = event["request"], event["grant"], event["pause"]
                if request != -1:
                    require(request == admitted and request < 256, "fairness admission order")
                    live.add(request)
                    waits[request] = eligible_ck
                    admitted += 1
                require(len(live) <= 64, "fairness queue depth")
                require(pause in range(4), "fairness suspension reason")
                reason = {0: None, 1: "response", 2: "ownership", 3: "maintenance"}[pause]
                model.step(live, None if grant == -1 else grant, reason)
                if reason is None:
                    eligible_ck += 1
                if grant != -1:
                    require(grant in live and grant not in seen, "fairness grant identity")
                    worst_wait = max(worst_wait, eligible_ck - waits.pop(grant))
                    mode = case["mode"]
                    direction = grant // 8 % 2 if mode == 2 else mode
                    groups[direction, grant % 2, grant // 2 % 4] += 1
                    live.remove(grant)
                    seen.add(grant)
                ck += 1
            elif kind == "end":
                require(case is not None and event["ck"] == ck, "fairness observed tail")
                require(admitted == len(seen) == 256 and not live, "unserved fairness tail")
                expected = 16 if case["mode"] == 2 else 8
                require(len(groups) == expected and min(groups.values()) > 0, "missing direction/port/QoS class")
                if case["pause_mode"]:
                    reason = {1: "response", 2: "ownership", 3: "maintenance"}[case["pause_mode"]]
                    require(
                        model.suspended.get(reason, 0) > (64 if reason == "ownership" else 0), "missing pause stimulus"
                    )
                case.update(
                    observed_ck=ck,
                    eligible_ck=eligible_ck,
                    measured_issue_gap_ck=model.worst_gap,
                    maximum_issue_gap_ck=model.issue_gap,
                    maximum_eligible_wait_ck=worst_wait,
                    conditional_bound_ck=model.bound,
                    suspended_ck=model.suspended,
                    grants_by_direction_port_class={
                        "/".join(map(str, key)): value for key, value in sorted(groups.items())
                    },
                )
                cases.append(case)
                case = None
            else:
                require(False, "unknown fairness record")
    require(case is None and len(cases) == 36, "missing fairness cases/tail")
    require(
        {(c["divisor"], c["mode"], c["pause_mode"]) for c in cases}
        == {(d, m, p) for d in (1, 2, 4) for m in range(3) for p in range(4)},
        "missing fairness workload",
    )
    return {
        "cases": cases,
        "limits": [
            "Actual scheduler grants with explicit physical-ready and ownership inputs; "
            "combined AXI/PHY fairness remains required.",
            "Fixed row, no older overlaps, ECC or caps; those eligibility exclusions need separate evidence.",
            "WCK is supplied disabled, so each access receives its own synchronization pair. "
            "This is not a pin/WCK qualification.",
        ],
    }


def qualify(path: Path, temporary: Path) -> dict:
    """Require a passing actual trace before checking exact active mutations."""
    report = check(path)
    events = [json.loads(line) for line in path.read_text().splitlines()]
    first_end = next(i for i, e in enumerate(events) if e["kind"] == "end")
    grants = [i for i, e in enumerate(events) if e["kind"] == "cycle" and e["grant"] != -1]
    last_admission = max(i for i, e in enumerate(events[:first_end]) if e["kind"] == "cycle" and e["request"] != -1)
    starved_tail = {i: {**events[i], "grant": -1} for i in grants if last_admission <= i < first_end}
    variants = (
        (
            "duplicate_grant",
            {grants[1]: {**events[grants[1]], "grant": events[grants[0]]["grant"]}},
            "ineligible grant",
        ),
        ("unserved_final_tail", {grants[-1]: {**events[grants[-1]], "grant": -1}}, "unserved fairness tail"),
        ("missing_final_boundary", {len(events) - 1: None}, "missing fairness cases/tail"),
        ("starved_eligible_tail", starved_tail, "issue gap G including trace tail"),
    )
    report["trace_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report["negative_cases"] = {}
    for name, replacements, marker in variants:
        temporary.write_text(
            "".join(
                json.dumps(replacements.get(i, event)) + "\n"
                for i, event in enumerate(events)
                if replacements.get(i, event) is not None
            )
        )
        try:
            check(temporary)
        except Violation as error:
            require(marker in str(error), "fairness mutation failed at an unrelated boundary: " + str(error))
            report["negative_cases"][name] = {
                "reason": str(error),
                "mutated_sha256": hashlib.sha256(temporary.read_bytes()).hexdigest(),
                "changes": [{"record": i, "before": events[i], "after": after} for i, after in replacements.items()],
            }
        else:
            raise RuntimeError("Fairness checker accepted mutation: " + name)
        finally:
            temporary.unlink()
    return report
