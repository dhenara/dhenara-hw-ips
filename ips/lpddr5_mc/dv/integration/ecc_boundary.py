"""Independent boundary timing and host settlement over continuous physical replay.

This binding owns profile8 inline plus write-link, conservative response policy,
two real AXI ports/channels and one destructive recovery. It does not infer a
physical endpoint from an internal result. No early-posted behavior is assumed.
"""

import argparse
import copy
import json
from pathlib import Path

from continuous import check as structure
from continuous_physical import PHASE, RECEIVING_PHASE, Replay, binding_digest
from system.dram import decode
from system.host import require
from system.trace import number
from wrapper_management import Source

CK_FS = 1_250_000


def boundary(header, marks, edges, *, pin_fs=None, mr43_ready_fs=None):
    """Bind a scheduled instant to independent pins or a witnessed epoch edge."""
    require(header["version"] == 1 and header["boundary"] in (0, 1), "boundary header")
    require(header["delta_ck"] in (-1, 0, 1) and header["posted"] in (0, 1), "boundary selection")
    require(header["ck_fs"] == CK_FS, "boundary clock")
    require(not header["posted_early"] and not header["epoch_posted"], "invented early posting")
    inject = marks["inject"]
    if header["boundary"] == 0:
        require(pin_fs is not None, "missing independent WR32 pin endpoint")
        anchor, preedge = pin_fs, 0
    else:
        results = [e for e in edges if e["channel"] == 0 and e["armed"] and e["epoch_result"]]
        require(
            results and results[0]["epoch_failed"] and results[0]["epoch_value"] == 1,
            "missing failed MR43 epoch result",
        )
        anchor, preedge = results[0]["fs"], 1
        require(
            mr43_ready_fs is not None and mr43_ready_fs <= anchor,
            "MR43 result before independent return/recovery/WCK tail",
        )
    require(inject["anchor_fs"] == anchor, "schedule differs from observed boundary")
    require(inject["fs"] == anchor + header["delta_ck"] * CK_FS - preedge, "wrong before/at/after injection phase")
    active = [e for e in edges if marks["held"]["fs"] < e["fs"] < marks["settled"]["fs"]]
    require(
        active and all(e["posted"] == e["host_posted"] == header["posted"] for e in active),
        "posted configuration not installed in both owners",
    )
    require(all(not e["posted_early"] and not e["epoch_posted"] for e in edges), "unsupported early-posted owner")
    require(any(e["fatal"] for e in active) and any(e["late_error"] for e in active), "missing actual fatal/late error")
    driven = [e for e in active if e["fs"] >= inject["fs"] and e["channel"] == 0]
    field = "error" if header["boundary"] == 0 else "external_fault"
    require(
        all(not e[field] for e in edges if e["channel"] == 0 and e["fs"] < inject["fs"]),
        "selected error input asserted before injection",
    )
    require(driven and any(e[field] for e in driven), "missing actual error input")
    return dict(anchor_fs=anchor, injection_fs=inject["fs"], delta_ck=header["delta_ck"], posted=header["posted"])


def physical(events, profile, schedule_fs):
    """Reuse the existing persistent row/data/return/WCK owner through reset."""
    require(events and events[-1]["kind"] == "end", "missing final physical end or records after end")
    require(sum(e["kind"] == "end" for e in events) == 1, "duplicate physical end")
    h = events[0]
    require(h["version"] == 2 and h["scope"] == "ecc_boundary", "boundary physical scope")
    require(h["phase"] == PHASE and h["receiving_phase"] == RECEIVING_PHASE, "boundary simulator phases")
    p = copy.deepcopy(profile)
    p["ranks"] = h["ranks"]
    require(h["profile_sha256"] == binding_digest(p) and h["profile_number"] == 8, "boundary numeric profile binding")
    require(
        h["channels"] == 2
        and h["ranks"] == 1
        and p["ratio"] == 4
        and p["ck_ps"] * 1000 == CK_FS
        and p["write_link"]
        and not p["read_link"],
        "boundary physical configuration",
    )
    require(h["external_flips"] == 0, "unexpected external byte mutation")
    channels = [Replay(p), Replay(p)]
    structural = [dict(h, version=1)]
    prepared, pin_fs, mr43, ready_fs = {}, None, None, None
    times, result = {}, None
    for e in events[1:]:
        if e["kind"] == "end":
            require(e["physical_closed"] == 1, "unclosed boundary physical tail")
            result = [c.finish(e["ck"]) for c in channels]
            structural.append(e)
            break
        c = channels[e["channel"]]
        if e["kind"] == "prepared":
            c.prepare(e)
            prepared[e["channel"]] = e
        elif e["kind"] == "transition":
            q = prepared.get(e["channel"])
            local_ck = c.physical.ck if c.physical is not None else None
            c.sample(e)
            times[e["channel"], e["ck"]] = e["ps"]
            structural.append(e)
            if q is None or c.physical is None or e["channel"] != 0:
                continue
            op = decode(q["ca"]) if q["cs"] else None
            if q["ps"] * 1000 == schedule_fs:
                require(
                    op is not None and op.kind == "WR" and op.beats == 32 and local_ck is not None,
                    "pin schedule is not actual WR32 CA",
                )
                burst = c.physical.phy.dram.bursts[-1]
                # Burst times come from independent CA/row/latency decoding.
                origin = q["ps"] * 1000 - local_ck * CK_FS
                pin_fs = origin + (burst.times[-1] + 1) * CK_FS // (2 * p["ratio"])
            if op and op.kind == "MRR" and op.operand == 43 and q["mr43"] == 1:
                mr43 = dict(fs=q["ps"] * 1000, serial=c.physical.serial)
            if mr43 is not None and ready_fs is None:
                received = c.physical.completion.received.get(mr43["serial"], {})
                if len(received) == 16 and c.physical.wck.epoch is None:
                    if (
                        e["ps"] * 1000
                        >= mr43["fs"]
                        + (p["management_ck"]["tctrl_delay"] + p["dram_ck"]["tmrr"] + p["dram_ck"]["tmrw"]) * CK_FS
                    ):
                        ready_fs = e["ps"] * 1000
        elif e["kind"] in ("inline_admit", "inline_physical", "inline_result", "inline_complete"):
            c.event(e)
            structural.append(e)
        else:
            raise ValueError("unexpected boundary physical event")
    require(result is not None, "missing boundary physical end")
    checked = structure(structural, p)
    return dict(channels=result, structure=checked, pin_fs=pin_fs, mr43_ready_fs=ready_fs), times


def host(source_events, physical_events, controls, times):
    """Reuse real AXI-edge scoreboard, including VALID continuity and abort W."""
    require(
        source_events and source_events[0] == dict(kind="header", version=1, ports=2, channels=2, bus_bytes=16),
        "boundary source header",
    )
    require(
        source_events[-1]["kind"] == "end" and sum(e["kind"] == "end" for e in source_events) == 1,
        "missing final source end or records after end",
    )
    s, owners, reset_at, fatal_seen = Source(), {}, None, False
    injected = settled = fresh = False
    timeline = []
    for e in source_events:
        if e["kind"] not in ("header", "end", "phy"):
            timeline.append((e["ps"] * 1000, 2, e))
    for e in physical_events:
        if e["kind"] in ("transition", "inline_admit", "inline_complete"):
            timeline.append((times[e["channel"], e["ck"]] * 1000, 1, e))
    for e in controls:
        if e["kind"] == "mark" or e["kind"] == "edge" and e["channel"] == 0:
            timeline.append((e["fs"], 0, e))
    for fs, _, e in sorted(timeline, key=lambda x: (x[0], x[1])):
        kind = e["kind"]
        if kind == "edge":
            if e["fatal"] and not fatal_seen:
                s.host.fail()
                fatal_seen = True
        elif kind == "mark":
            if e["name"] == "inject":
                live = {t.address.address: t for t in s.host.transactions.values() if not t.done}
                require(
                    not injected and set(live) == {64, 512, 768},
                    "injection lacks three actual original source obligations",
                )
                require(
                    live[64].write and live[64].offered == (0, 0, True) and not live[64].done,
                    "injection lacks held offered successful B",
                )
                require(
                    all(live[a].write and live[a].offered is None for a in (512, 768)),
                    "failed originals already offered before injection",
                )
                require(
                    len(live[512].w) == 4 and len(live[768].w) == 1,
                    "injection lacks complete target and accepted trailing W obligation",
                )
                injected = True
            elif e["name"] == "settled":
                require(injected and s.settled() and not s.held, "settlement marker before real source settlement")
                settled = True
            elif e["name"] == "fresh":
                require(
                    settled and reset_at is not None and fs > reset_at,
                    "fresh publication before consumed destructive reset",
                )
                fresh = True
        elif kind == "transition":
            if e["destructive"] and e["channel"] == 0:
                require(s.settled() and not s.held, "destructive reset before real AXI settlement")
                s.host.reset(destructive=True)
                owners.clear()
                reset_at = fs
        elif kind == "inline_admit":
            require(e["channel"] not in owners, "duplicate inline source owner")
            owners[e["channel"]] = (s.owner(e, e["channel"]), e)
        elif kind == "inline_complete":
            tag, q = owners.pop(e["channel"])
            if not e["failed"]:
                data = number(q["data"]).to_bytes(64, "little")
                s.completed(tag, q, e, data)
            else:
                require(fatal_seen, "unexpected independent failure cause before injected fatal")
        else:
            if kind in ("aw", "ar") and reset_at is not None:
                require(fresh, "fresh source acceptance before publication marker")
            if kind in ("b", "r"):
                witness = s.flags.get(e["port"])
                require(
                    witness is not None
                    and witness["ps"] == e["ps"]
                    and witness["flags"] >> (3 if kind == "b" else 1) & 1,
                    "source response lacks its AXI edge VALID witness",
                )
            s.event(e)
    s.host.finish()
    require(
        fatal_seen and injected and settled and fresh and reset_at is not None and s.settled(),
        "missing injection/fault/reset/final host settlement",
    )
    require(
        s.counts["aw"] == 4 and s.counts["ar"] == 1 and s.counts["b"] == 4 and s.counts["r"] == 4,
        "boundary original response coverage",
    )
    require(s.host.late_errors, "held successful B missing independent late-error cause")
    return dict(counts=dict(s.counts), reset_fs=reset_at, late_errors=len(s.host.late_errors))


def check(continuous_path, source_path, boundary_path, profile):
    streams = [
        [json.loads(line) for line in Path(path).read_text().splitlines()]
        for path in (continuous_path, source_path, boundary_path)
    ]
    continuous, source_events, controls = streams
    header = controls[0]
    require(header["kind"] == "header" and controls[-1].get("name") == "end", "boundary control header/end")
    require([e["fs"] for e in controls[1:]] == sorted(e["fs"] for e in controls[1:]), "reordered boundary controls")
    for channel in (0, 1):
        edge_fs = [e["fs"] for e in controls if e["kind"] == "edge" and e["channel"] == channel]
        sample_fs = [e["ps"] * 1000 for e in continuous if e["kind"] == "transition" and e["channel"] == channel]
        require(edge_fs and edge_fs == sample_fs, "missing/duplicate/displaced boundary CK witness")
    marks = {e["name"]: e for e in controls if e["kind"] == "mark"}
    require(len(marks) == sum(e["kind"] == "mark" for e in controls), "duplicate boundary marker")
    require(
        all(n in marks for n in ("held", "inject", "settled", "reset_consumed", "fresh", "end")),
        "missing boundary lifecycle marker",
    )
    schedule = marks["pin_schedule" if header["boundary"] == 0 else "mr43_schedule"]["fs"]
    require(
        marks["held"]["fs"]
        < schedule
        < marks["inject"]["fs"]
        < marks["settled"]["fs"]
        < marks["reset_consumed"]["fs"]
        < marks["fresh"]["fs"]
        < marks["end"]["fs"],
        "reordered boundary lifecycle",
    )
    pins, times = physical(continuous, profile, schedule)
    b = boundary(
        header,
        marks,
        [e for e in controls if e["kind"] == "edge"],
        pin_fs=pins["pin_fs"],
        mr43_ready_fs=pins["mr43_ready_fs"],
    )
    return dict(
        boundary=b,
        physical=pins,
        host=host(source_events, continuous, controls, times),
        limitations=[
            "Checker pass validates supplied streams; timed HDL execution and exact-source provenance "
            "require separately retained evidence.",
            "Profile8, rank1, inline plus write-link, conservative MODE.posted behavior; "
            "early-posted MR43 failure is unreachable.",
            "Read-link/read-DBI, FSP and external PHY ownership intervals are excluded; "
            "no separate maintenance-entry command is required by this selection.",
        ],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--continuous", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--boundary", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    result = check(args.continuous, args.source, args.boundary, json.loads(args.profile.read_text()))
    args.report.write_text(json.dumps(result, indent=2) + "\n")
