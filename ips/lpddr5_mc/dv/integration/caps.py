"""Charge observed combined-controller ACCESS bytes, including the final tail."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from system.host import Violation, require
from system.measurement import Caps


def check(path: Path, physical: dict) -> dict:
    events = [json.loads(line) for line in path.read_text().splitlines()]
    require(bool(events) and events[0]["kind"] == "header", "missing cap header")
    header = events[0]
    require(
        header["channels"] == 2
        and header["mode"] in (1, 2)
        and (header["window"], header["read_cap"], header["write_cap"]) == (512, 64, 64),
        "cap workload configuration",
    )
    models = [Caps(512, (64, 64), header["mode"] == 2) for _ in range(2)]
    live, seen, totals, groups, windows = {}, set(), Counter(), Counter(), Counter()
    borrowed, last_ck, ended = [0, 0], -1, False
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
        require(channel in (0, 1) and direction in (0, 1) and event["bytes"] == 64, "cap descriptor")
        key = channel, event["tag"], event["generation"], direction
        if kind == "admit":
            require(key not in live and key not in seen, "duplicate cap admission")
            live[key] = event
            continue
        require(key in live and key not in seen, "unmatched/duplicate cap issue")
        request = live[key]
        require(all(request[k] == event[k] for k in ("bytes", "port", "qos")), "cap descriptor changed before issue")
        opposite = any(k[0] == channel and k[3] != direction for k in live)
        # These phase-drained workloads avoid timing/overlap eligibility
        # assumptions: no opposite admission is live when borrowing occurs.
        model = models[channel]
        previous = model.used[direction] if model.current == event["ck"] // 512 else 0
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
        len(seen) == 64 and totals[0] == physical["read_bytes"] == totals[1] == physical["write_bytes"] == 2048,
        "cap issue/physical payload disagreement",
    )
    require(len(groups) == 16 and min(groups.values()) == 4, "cap source/class coverage")
    if header["mode"] == 2:
        require(min(borrowed) > 0, "missing work-conserving borrowing")
    else:
        require(not any(borrowed), "strict cap borrowed")
    return {
        "mode": header["mode"],
        "issues": len(seen),
        "read_bytes": totals[0],
        "write_bytes": totals[1],
        "borrowed_bytes_by_channel": borrowed,
        "issued_bytes_by_channel_window_direction": {"/".join(map(str, k)): v for k, v in sorted(windows.items())},
        "issues_by_channel_direction_port_class": {"/".join(map(str, k)): v for k, v in sorted(groups.items())},
        "observed_until_ck": last_ck,
        "limits": [
            "Actual host/CDC/channel traffic and ACCESS observations with independent physical/payload replay.",
            "Phase-drained full 64-byte read/write traffic; mixed opposite eligibility, zero caps, "
            "32-byte descriptors and combined fairness remain separate qualification work.",
        ],
    }


def qualify(path: Path, temporary: Path, physical: dict) -> dict:
    report = check(path, physical)
    events = [json.loads(line) for line in path.read_text().splitlines()]
    first = next(i for i, e in enumerate(events) if e["kind"] == "issue")
    last = max(i for i, e in enumerate(events) if e["kind"] == "issue")
    inserted = []
    for index in range(events[0]["mode"]):
        issue = {**events[first], "tag": (1 << 63) + index}
        inserted.extend([{**issue, "kind": "admit"}, issue])
    variants = (
        ("duplicate_issue", events[: first + 1] + [events[first]] + events[first + 1 :], "duplicate cap issue"),
        (
            "over_budget_issue",
            events[: first + 1] + inserted + events[first + 1 :],
            "combined byte cap" if events[0]["mode"] == 2 else "exceeds byte cap",
        ),
        ("unserved_tail", events[:last] + events[last + 1 :], "unserved cap tail"),
        ("missing_final_boundary", events[:-1], "missing cap final boundary"),
    )
    report["trace_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report["negative_cases"] = {}
    for name, altered, marker in variants:
        temporary.write_text("".join(json.dumps(e) + "\n" for e in altered))
        try:
            check(temporary, physical)
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
