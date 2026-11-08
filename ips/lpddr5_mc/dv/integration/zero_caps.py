"""Check disabled-direction queueing and explicit fault settlement from real traces."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from system.host import Violation, require
from system.measurement import Caps


def check(path: Path, physical: dict) -> dict:
    events = [json.loads(line) for line in path.read_text().splitlines()]
    require(bool(events) and events[0]["kind"] == "header", "missing zero-cap header")
    header = events[0]
    zero = header["zero_direction"]
    limits = (header["read_cap"], header["write_cap"])
    require(
        header["version"] == 3
        and header["channels"] == 2
        and header["mode"] in (1, 2)
        and zero in (0, 1)
        and limits[zero] == 0
        and limits[1 - zero] == 64
        and header["granule_bytes"] == 64
        and header["window"] == 512,
        "zero-cap configuration",
    )
    models = [Caps(512, limits, header["mode"] == 2) for _ in range(2)]
    live, seen, admitted, grants, windows = {}, set(), Counter(), Counter(), Counter()
    last_ck, hold_ck, fault_ck, fault_ps, ended = -1, None, None, None, False
    for event in events[1:]:
        ck, kind = event["ck"], event["kind"]
        require(not ended and ck >= last_ck, "zero-cap trace order/tail")
        last_ck = ck
        if kind == "hold":
            require(hold_ck is None and len(seen) == 16, "zero-cap enabled tail not complete")
            require(len(live) == 16 and all(k[3] == zero for k in live), "zero-cap held ledger")
            hold_ck = ck
        elif kind == "fault":
            require(
                hold_ck is not None and fault_ck is None and ck - hold_ck >= 2048,
                "zero-cap hold shorter than four windows",
            )
            require(event["ps"] == physical["fault_ps"], "zero-cap fault witness disagreement")
            fault_ck, fault_ps = ck, event["ps"]
        elif kind == "end":
            require(fault_ck is not None and ck > fault_ck and len(live) == 16, "zero-cap missing fault/settled tail")
            ended = True
        else:
            require(kind in ("admit", "issue") and fault_ck is None, "unexpected zero-cap record after fault")
            channel, direction = event["channel"], event["write"]
            require(
                channel in (0, 1) and direction in (0, 1) and event["bytes"] == 64,
                "zero-cap descriptor",
            )
            key = channel, event["tag"], event["generation"], direction
            group = channel, direction, event["port"], event["qos"]
            if kind == "admit":
                require(hold_ck is None and key not in live and key not in seen, "zero-cap duplicate/late admission")
                live[key] = event
                admitted[group] += 1
            else:
                require(key in live and key not in seen, "zero-cap duplicate/unmatched issue")
                require(all(live[key][k] == event[k] for k in ("bytes", "port", "qos")), "zero-cap issue identity")
                models[channel].issue(ck, direction, 64, False)
                seen.add(key)
                del live[key]
                grants[group] += 1
                windows[channel, ck // 512] += 64
    require(ended, "missing zero-cap final boundary")
    expected_groups = {(c, d, p, q) for c in range(2) for d in range(2) for p in range(2) for q in range(4)}
    require(set(admitted) == expected_groups and set(admitted.values()) == {1}, "zero-cap source/class admissions")
    require(
        set(grants) == {g for g in expected_groups if g[1] != zero} and set(grants.values()) == {1},
        "zero-cap source/class progress",
    )
    payload, failed = [0, 0], [0, 0]
    payload[1 - zero], failed[zero] = 1024, 16
    require(physical["failed_requests_by_direction"] == failed, "zero-cap incomplete fault settlement")
    require(physical["physical_payload_bytes_by_direction"] == payload, "zero-cap physical side effect")
    require([physical["read_bytes"], physical["write_bytes"]] == payload, "zero-cap response payload")
    return {
        "mode": header["mode"],
        "zero_direction": zero,
        "held_requests": len(live),
        "hold_start_ck": hold_ck,
        "fault_ck": fault_ck,
        "fault_ps": fault_ps,
        "observed_until_ck": last_ck,
        "issues": len(seen),
        "failed_requests_by_direction": failed,
        "physical_payload_bytes_by_direction": payload,
        "issues_by_channel_direction_port_class": {"/".join(map(str, k)): v for k, v in sorted(grants.items())},
        "issued_bytes_by_channel_window": {"/".join(map(str, k)): v for k, v in sorted(windows.items())},
        "limits": [
            "Two-port/two-channel plain 64-byte datapath traffic, real CDC and physical/host replay.",
            "An explicit external fault settles disabled traffic after at least 2048 blocked CK; "
            "no reset or preservation claim.",
            "Mixed borrowing eligibility and combined fairness require separate evidence.",
        ],
    }


def qualify(path: Path, temporary: Path, physical: dict) -> dict:
    report = check(path, physical)
    events = [json.loads(line) for line in path.read_text().splitlines()]
    hold = next(i for i, e in enumerate(events) if e["kind"] == "hold")
    fault = next(i for i, e in enumerate(events) if e["kind"] == "fault")
    disabled = next(e for e in events if e["kind"] == "admit" and e["write"] == report["zero_direction"])
    last_issue = max(i for i, e in enumerate(events) if e["kind"] == "issue")
    variants = (
        (
            "disabled_issue",
            events[:hold] + [{**disabled, "kind": "issue", "ck": events[hold]["ck"]}] + events[hold:],
            "zero disables direction",
        ),
        (
            "short_hold",
            events[:fault] + [{**events[fault], "ck": events[hold]["ck"] + 1}] + events[fault + 1 :],
            "hold shorter than four windows",
        ),
        ("missing_fault", events[:fault] + events[fault + 1 :], "missing fault/settled tail"),
        ("unserved_enabled_tail", events[:last_issue] + events[last_issue + 1 :], "enabled tail not complete"),
        ("missing_final_boundary", events[:-1], "missing zero-cap final boundary"),
    )
    report["trace_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report["negative_cases"] = {}
    for name, altered, marker in variants:
        temporary.write_text("".join(json.dumps(e) + "\n" for e in altered))
        try:
            check(temporary, physical)
        except Violation as error:
            require(marker in str(error), "zero-cap mutation failed at unrelated boundary: " + str(error))
            report["negative_cases"][name] = {
                "reason": str(error),
                "mutated_sha256": hashlib.sha256(temporary.read_bytes()).hexdigest(),
            }
        else:
            raise RuntimeError("Zero-cap checker accepted mutation: " + name)
        finally:
            temporary.unlink()
    return report
