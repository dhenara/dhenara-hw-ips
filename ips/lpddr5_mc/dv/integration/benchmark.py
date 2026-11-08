"""Actual-controller source/pin replay and integral-window payload accounting.

Caps, all-class fairness and full qualification remain separately required.
A two-period smoke run checks this binding without producing a rate verdict.
"""

import gzip
import json
import re
from collections import Counter, deque
from pathlib import Path

from benchmark_pins import PinChannel
from system.host import require
from system.trace import source_order


def address(port, sequence):
    return (
        (sequence // 512 << 16)
        | (sequence // 2 % 16 << 12)
        | (port << 11)
        | (sequence // 32 % 16 << 7)
        | (sequence % 2 << 6)
    )


def pattern(at):
    return bytes((i ^ (i >> 8) ^ 90) & 255 for i in range(at, at + 64))


def number(value):
    return int(value, 0) if isinstance(value, str) else value


def check(trace: Path, log: Path, direction: str, service: int, *, smoke: bool = False) -> dict:
    match = re.search(r"BENCHMARK_START ps=(\d+) direction=(\d+) service=(\d+) periods=(\d+)", log.read_text())
    require(match is not None, "missing benchmark start")
    start, selected_direction, selected_service, periods = map(int, match.groups())
    require(
        selected_direction == int(direction == "write")
        and selected_service == service
        and periods == (2 if smoke else 100),
        "benchmark settings",
    )
    live, wqueue = [{}, {}], [deque(), deque()]
    accepted, completed, wbytes = [0, 0], [0, 0], [0, 0]
    counts = [[0] * periods for _ in range(2)]
    profiles = json.loads((Path(__file__).resolve().parents[2] / "config/contracts/profiles.json").read_text())[
        "profiles"
    ]
    owners = {}
    pins = [PinChannel(profiles[4], c, owners) for c in range(2)]
    clocks, source_stalls, engine_stalls = [None, None], [Counter(), Counter()], [Counter(), Counter()]
    engine_edges = [-1, -1]
    channel_live, channel_maximum, channel_backlog = [0, 0], [0, 0], []
    duration = periods * 3_750_000
    backlog, maximum = [], [0, 0]
    next_boundary, ended, last_ps = 0, False, -1
    with gzip.open(trace, "rt") if trace.suffix == ".gz" else trace.open() as stream:
        events = iter(source_order(stream))
        header = next(events)
        require(header["ports"] == header["channels"] == 2 and header["bus_bytes"] == 16, "benchmark ports/width")
        for e in events:
            require(not ended and e["ps"] >= last_ps, "benchmark event order/end")
            last_ps = e["ps"]
            relative = e["ps"] - start
            while next_boundary <= periods and relative >= next_boundary * 3_750_000:
                backlog.append([len(port) for port in live])
                channel_backlog.append(list(channel_live))
                next_boundary += 1
            kind = e["kind"]
            if kind == "phy":
                pins[e["channel"]].sample(e)
                continue
            if kind == "axi":
                p, flags = e["port"], e["flags"]
                require(clocks[p] is None or e["ps"] == clocks[p] + 2500, "actual AXI400 clock cadence")
                clocks[p] = e["ps"]
                if relative >= 0:
                    require(flags & 5 == 5, "benchmark responses must remain ready")
                if 0 <= relative < duration:
                    for name, valid_bit, ready_bit in (("aw", 9, 8), ("ar", 7, 6), ("w", 5, 4)):
                        source_stalls[p][name + "_stall"] += bool(flags >> valid_bit & 1 and not flags >> ready_bit & 1)
                    source_stalls[p]["edges"] += 1
                continue
            if kind == "engine":
                c, ck, flags = e["channel"], e["ck"], e["flags"]
                require(ck == engine_edges[c] + 1, "missing engine diagnostic CK")
                engine_edges[c] = ck
                if (0 if smoke else 37_500_000) <= relative < duration:
                    queue = number(e["queue"]).bit_count()
                    if flags >> 3 & 1:
                        reason = "payload_issue"
                    elif flags >> 9 & 1:
                        reason = "refresh"
                    elif flags >> 8 & 1 or flags >> 10 & 1:
                        reason = "zq" if flags >> 14 & 1 else "mr4"
                    elif flags >> 11 & 1:
                        reason = "row_close"
                    elif not queue:
                        reason = "queue_empty_cdc_or_source"
                    elif not flags >> 2 & 1:
                        reason = "data_or_response_credit"
                    elif not flags & 1:
                        reason = "selection"
                    elif flags >> 6 & 1:
                        reason = "reserved_pair"
                    elif flags >> 4 & 1:
                        reason = "row_command" if e["operation"] != 2 else "selection_feedback"
                    else:
                        reason = "timing_or_policy"
                    engine_stalls[c][reason] += 1
                continue
            if kind == "end":
                ended = True
                continue
            require(relative >= 0, "source event before benchmark start")
            p = e["port"]
            if kind in ("aw", "ar"):
                identity = e["id"]
                require(
                    kind == ("aw" if direction == "write" else "ar") and identity == accepted[p],
                    "benchmark source sequence",
                )
                require(
                    number(e["address"]) == address(p, identity)
                    and e["length"] == 3
                    and e["size"] == 4
                    and e["burst"] == 1
                    and e["lock"] == 0,
                    "benchmark descriptor",
                )
                at = address(p, identity)
                require(at not in owners, "duplicate benchmark address")
                entry = {
                    "w": 0,
                    "r": 0,
                    "write": direction == "write",
                    "payload": pattern(at),
                    "written": bytearray(),
                    "issued": False,
                    "raw_words": 0,
                    "raw_slices": 0,
                    "pin_end_ps": float("inf"),
                }
                live[p][identity] = entry
                owners[at] = entry
                channel_live[identity % 2] += 1
                channel_maximum[identity % 2] = max(channel_maximum[identity % 2], channel_live[identity % 2])
                require(channel_live[identity % 2] <= 64, "benchmark per-channel credit overflow")
                if direction == "write":
                    wqueue[p].append(identity)
                accepted[p] += 1
                maximum[p] = max(maximum[p], len(live[p]))
            elif kind == "w":
                require(direction == "write" and bool(wqueue[p]), "benchmark W without AW")
                identity = wqueue[p][0]
                at = live[p][identity]["w"]
                require(
                    number(e["strobe"]) == 65535
                    and number(e["data"]).to_bytes(16, "little") == pattern(address(p, identity))[at : at + 16]
                    and bool(e["last"]) == (at == 48),
                    "benchmark W data/count/last",
                )
                live[p][identity]["written"].extend(number(e["data"]).to_bytes(16, "little"))
                live[p][identity]["w"] += 16
                wbytes[p] += 16
                if e["last"]:
                    wqueue[p].popleft()
            elif kind in ("b", "r"):
                identity = e["id"]
                require(
                    kind == ("b" if direction == "write" else "r")
                    and identity in live[p]
                    and e["response"] == 0
                    and e["ready"] == 1,
                    "benchmark response identity/status/ready",
                )
                require(identity == completed[p], "benchmark response sequence")
                entry = live[p][identity]
                require(
                    entry["issued"]
                    and e["ps"] >= entry["pin_end_ps"]
                    and entry["raw_words" if direction == "write" else "raw_slices"]
                    == (16 if direction == "write" else 32),
                    "AXI response before complete physical payload",
                )
                done = kind == "b"
                if done:
                    require(live[p][identity]["w"] == 64, "B before all W")
                else:
                    at = live[p][identity]["r"]
                    require(
                        number(e["data"]).to_bytes(16, "little") == pattern(address(p, identity))[at : at + 16]
                        and bool(e["last"]) == (at == 48),
                        "benchmark R data/count/last",
                    )
                    live[p][identity]["r"] += 16
                    done = bool(e["last"])
                if done:
                    del live[p][identity]
                    del owners[address(p, identity)]
                    channel_live[identity % 2] -= 1
                    completed[p] += 1
                    if relative < duration:
                        counts[identity % 2][relative // 3_750_000] += 64
            else:
                raise ValueError("unknown benchmark event")
    require(
        ended and not any(live) and not any(wqueue) and accepted == completed and next_boundary == periods + 1,
        "benchmark truncation/unsettled tail",
    )
    pin_results = [channel.finish() for channel in pins]
    require(
        all(clock is not None for clock in clocks) and engine_edges == [pins[0].last_ck, pins[1].last_ck],
        "missing diagnostic tail",
    )
    require(all(stalls["edges"] == periods * 1500 for stalls in source_stalls), "missing source clock window")
    require(
        all(sum(stalls.values()) == (periods if smoke else 90) * 3000 for stalls in engine_stalls),
        "missing engine stall accounting",
    )
    measured = sum(sum(channel[0 if smoke else 10 :]) for channel in counts)
    reference = 2 * 90 * 64 * (339 if service == 4 else 375)
    return {
        "direction": direction,
        "service_div": service,
        "completed_bytes": measured,
        "reference_bytes": reference,
        "missing_bytes": None if smoke else max(0, reference - measured),
        "threshold_passed": None if smoke else measured * 100 >= reference * 99,
        "smoke_only": smoke,
        "gb_per_second": None if smoke else measured / (270_000 * 1.25),
        "completed_bytes_per_channel_period": counts,
        "backlog_by_port": backlog,
        "backlog_by_channel": channel_backlog,
        "maximum_outstanding_by_channel": channel_maximum,
        "steady_backlog_endpoint_passed": None
        if smoke
        else all(channel_backlog[-1][c] <= channel_backlog[10][c] for c in range(2)),
        "physical_observations": pin_results,
        "source_cycle_observations": [dict(c) for c in source_stalls],
        "engine_cycle_observations": [dict(c) for c in engine_stalls],
        "maximum_outstanding_by_port": maximum,
        "accepted_by_port": accepted,
        "completed_by_port": completed,
        "write_bytes_by_port": wbytes,
        "controller_qualified": False,
        "limits": [
            "The engine reason counts partition observed cycles; overlapping pipeline costs "
            "do not convert one-for-one into missing bytes. "
            "Backlog growth interpretation, caps and all-class fairness closure remain pending."
        ],
    }
