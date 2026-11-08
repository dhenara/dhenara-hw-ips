"""Charge observed combined-controller ACCESS bytes, including the final tail."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from cap_eligibility import Eligibility, footprint, geometry, probe
from system.host import Violation, require
from system.measurement import Caps


def check(path: Path, physical: dict, *, raw: Path | None = None, profile: dict | None = None) -> dict:
    events = [json.loads(line) for line in path.read_text().splitlines()]
    require(bool(events) and events[0]["kind"] == "header", "missing cap header")
    header = events[0]
    granule = header.get("granule_bytes", 64)
    mixed = header.get("mixed", 0)
    require(
        header["channels"] == 2
        and header["mode"] in (1, 2)
        and (header["window"], header["read_cap"], header["write_cap"]) == (512, 64, 64),
        "cap workload configuration",
    )
    timed = header.get("version") == 4
    require(
        granule in (32, 64)
        and mixed in (0, 1, 2)
        and timed == (mixed == 2)
        and (not mixed or header["mode"] == 1 or (mixed == 2 and timed))
        and (not timed or (raw is not None and profile is not None)),
        "cap traffic shape/eligibility inputs",
    )
    eligibility = Eligibility(raw, profile) if timed else None
    if timed:
        require(
            (header["read_weight"], header["write_weight"], header["write_batch_min"]) == (1, 4, 2),
            "concurrent cap batching configuration",
        )
    models = [Caps(512, (64, 64), header["mode"] == 2) for _ in range(2)]
    live, seen, totals, groups, windows = {}, set(), Counter(), Counter(), Counter()
    borrowed, last_ck, ended = [0, 0], -1, False
    concurrent = [0, 0]
    addressed = set()
    for event in events[1:]:
        require(not ended and event["ck"] >= last_ck, "cap trace order/tail")
        last_ck = event["ck"]
        kind = event["kind"]
        if kind == "end":
            require(not live, "unserved cap tail")
            ended = True
            continue
        require(kind in ("admit", "issue"), "unknown cap record")
        channel, direction = event["channel"], event["write"]
        require(channel in (0, 1) and direction in (0, 1) and event["bytes"] == granule, "cap descriptor")
        key = channel, event["tag"], event["generation"], direction
        if kind == "admit":
            require(key not in live and key not in seen, "duplicate cap admission")
            if eligibility is not None:
                locations = footprint(event)
                require(
                    not addressed.intersection(locations), "timed cap workload requires disjoint physical addresses"
                )
                addressed.update(locations)
            live[key] = event
            if any(k[0] == channel and k[3] != direction for k in live):
                concurrent[channel] += 1
            continue
        require(key in live and key not in seen, "unmatched/duplicate cap issue")
        request = live[key]
        require(all(request[k] == event[k] for k in ("bytes", "port", "qos")), "cap descriptor changed before issue")
        if eligibility is not None:
            require(geometry(request) == geometry(event), "cap geometry changed before issue")
        opposite = any(k[0] == channel and k[3] != direction for k in live)
        model = models[channel]
        previous = model.used[direction] if model.current == event["ck"] // 512 else 0
        if eligibility is not None:
            opposite = eligibility.issue(event, live, previous + event["bytes"] > 64)
        model.issue(event["ck"], direction, event["bytes"], opposite)
        if previous + event["bytes"] > 64:
            borrowed[channel] += event["bytes"]
        del live[key]
        seen.add(key)
        totals[direction] += event["bytes"]
        groups[channel, direction, event["port"], event["qos"]] += 1
        windows[channel, event["ck"] // 512, direction] += event["bytes"]
    require(ended, "missing cap final boundary")
    require(
        len(seen) == 64 and totals[0] == physical["read_bytes"] == totals[1] == physical["write_bytes"] == 32 * granule,
        "cap issue/physical payload disagreement",
    )
    expected_groups, expected_count = (32, 2) if header.get("version") in (2, 4) else (16, 4)
    require(len(groups) == expected_groups and min(groups.values()) == expected_count, "cap source/class coverage")
    require(all(concurrent) if mixed else not any(concurrent), "cap direction concurrency")
    if header["mode"] == 2:
        require(min(borrowed) > 0, "missing work-conserving borrowing")
    else:
        require(not any(borrowed), "strict cap borrowed")
    result = {
        "mode": header["mode"],
        "granule_bytes": granule,
        "mixed": bool(mixed),
        "opposite_live_admissions_by_channel": concurrent,
        "batching": ({k: header[k] for k in ("read_weight", "write_weight", "write_batch_min")} if timed else None),
        "issues": len(seen),
        "read_bytes": totals[0],
        "write_bytes": totals[1],
        "borrowed_bytes_by_channel": borrowed,
        "issued_bytes_by_channel_window_direction": {"/".join(map(str, k)): v for k, v in sorted(windows.items())},
        "issues_by_channel_direction_port_class": {"/".join(map(str, k)): v for k, v in sorted(groups.items())},
        "observed_until_ck": last_ck,
        "limits": [
            "Actual host/CDC/channel traffic and ACCESS observations with independent physical/payload replay.",
            "Legacy phase borrowing uses absence of opposite admissions. Version-four concurrent borrowing "
            "requires independent physical-history eligibility. Fairness is checked separately.",
        ],
    }
    if eligibility is not None:
        result["opposite_eligibility"] = eligibility.finish(mixed == 2 and header["mode"] == 2)
    return result


def qualify(path: Path, temporary: Path, physical: dict, **inputs) -> dict:
    report = check(path, physical, **inputs)
    events = [json.loads(line) for line in path.read_text().splitlines()]
    first = next(i for i, e in enumerate(events) if e["kind"] == "issue")
    last = max(i for i, e in enumerate(events) if e["kind"] == "issue")
    inserted = []
    # Fill the remainder of the first issue's budget, then exceed it. Use the
    # observed byte amount so BL16 charging cannot escape a BL32-sized mutant.
    for index in range(64 * events[0]["mode"] // events[first]["bytes"]):
        issue = {**events[first], "tag": (1 << 63) + index}
        inserted.extend([{**issue, "kind": "admit"}, issue])
    variants = [
        ("duplicate_issue", events[: first + 1] + [events[first]] + events[first + 1 :], "duplicate cap issue"),
        ("unserved_tail", events[:last] + events[last + 1 :], "unserved cap tail"),
        ("missing_final_boundary", events[:-1], "missing cap final boundary"),
    ]
    if events[0].get("version") != 4:
        variants.append(
            (
                "over_budget_issue",
                events[: first + 1] + inserted + events[first + 1 :],
                "byte cap" if events[0]["mode"] == 2 else "exceeds byte cap",
            )
        )
    else:
        variants.extend(
            [
                (
                    "changed_physical_geometry",
                    events[:first] + [{**events[first], "row": events[first]["row"] ^ 1}] + events[first + 1 :],
                    "geometry changed before issue",
                ),
                (
                    "overlapping_admission",
                    events[: first + 1] + [inserted[0]] + events[first + 1 :],
                    "requires disjoint physical addresses",
                ),
            ]
        )
    if events[0].get("version") == 4 and events[0]["mode"] == 2:
        # Add a disjoint opposite admission whose next command the independent
        # physical prefix permits. The original borrowed command must now fail
        # at the cap rule, before any synthetic tail can affect the verdict.
        oracle = Eligibility(inputs["raw"], inputs["profile"])
        used, addressed, mutant = Counter(), set(), None
        for event in events[1:]:
            if event["kind"] == "admit":
                addressed.update(footprint(event))
            if event["kind"] != "issue":
                continue
            c, ck, d = event["channel"], event["ck"], event["write"]
            used[c, ck // 512, d] += event["bytes"]
            if used[c, ck // 512, d] <= 64:
                continue
            model = oracle.advance(c, ck)
            for bank in range(16):
                request = {
                    **event,
                    "kind": "admit",
                    "ck": ck - 1,
                    "tag": 1 << 63,
                    "write": 1 - d,
                    "bank": bank,
                    "row": 32000,
                    "column": 0,
                }
                if not addressed.intersection(footprint(request)) and probe(model, ck + oracle.delay, request)[0]:
                    mutant = request
                    break
            if mutant is not None:
                break
        require(mutant is not None, "missing legal opposite-work mutation opportunity")
        altered = [events[0], *sorted([*events[1:], mutant], key=lambda e: (e["ck"], e["kind"] != "admit"))]
        variants.append(("borrow_with_ready_opposite", altered, "exceeds byte cap"))
    report["trace_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report["negative_cases"] = {}
    for name, altered, marker in variants:
        temporary.write_text("".join(json.dumps(e) + "\n" for e in altered))
        try:
            check(temporary, physical, **inputs)
        except Violation as error:
            require(marker in str(error), "cap mutation failed at unrelated boundary: " + str(error))
            report["negative_cases"][name] = {
                "reason": str(error),
                "mutated_sha256": hashlib.sha256(temporary.read_bytes()).hexdigest(),
            }
        else:
            raise RuntimeError("Cap checker accepted mutation: " + name)
        finally:
            temporary.unlink()
    return report
