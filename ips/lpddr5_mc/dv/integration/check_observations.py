"""Replay retained source/PHY observations and prove checker sensitivity.

Mutants are disposable copies of the trace; the original observation file and
its failed or passing simulation report remain unchanged.
"""

import argparse
import copy
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

from connected import check, number
from system.dram import decode
from system.host import Violation

IP = Path(__file__).resolve().parents[2]
REPO = IP.parents[1]


def sensitivity(events: list[dict], profile: dict) -> dict[str, list[dict]]:
    variants = {}

    def return_bit(e):
        if e["kind"] != "phy":
            return None
        for lane in range(4):
            for byte in range(2):
                if (number(e["return"]) >> (32 + lane * 2 + byte)) & 1:
                    return 40 + lane * 32 + byte * 8
        return None

    def mutate(name, predicate, change):
        result = copy.deepcopy(events)
        target = next(e for e in result if predicate(e))
        change(target)
        variants[name] = result

    # Write enable precedes data by tphy_wrdata. Mutating an enable-time data
    # lane would only alter idle bus bits, which a protocol checker must ignore.
    enabled = next(e for e in events if e["kind"] == "phy" and (number(e["write"]) >> 24) & 255)
    ratio = profile["ratio"]
    lane = next(i for i in range(ratio) if (number(enabled["write"]) >> (24 + i * 2)) & 3)
    data_at = enabled["ck"] * ratio + lane + profile["dfi_wck"]["tphy_wrdata"]
    data_lane = data_at % ratio
    target = next(
        e for e in events if e["kind"] == "phy" and e["channel"] == enabled["channel"] and e["ck"] == data_at // ratio
    )
    byte = next(i for i in range(4) if not (number(target["write"]) >> (32 + data_lane * 4 + i)) & 1)
    mutate(
        "physical_write_data",
        lambda e: e == target,
        lambda e: e.update(write=hex(number(e["write"]) ^ (1 << (48 + data_lane * 32 + byte * 8)))),
    )
    mutate(
        "returned_slice_data",
        lambda e: return_bit(e) is not None,
        lambda e: e.update({"return": hex(number(e["return"]) ^ (1 << return_bit(e)))}),
    )
    mutate(
        "unsupported_command_rank",
        lambda e: e["kind"] == "phy" and e["cs"] == 1,
        lambda e: e.update(cs=2),
    )
    mutate(
        "missing_read_enable",
        lambda e: e["kind"] == "phy" and number(e["read"]) >> 16,
        lambda e: e.update(read=hex(number(e["read"]) & 65535)),
    )
    mutate(
        "continuous_read_cs_hole",
        lambda e: e["kind"] == "phy" and number(e["read"]) & 65535,
        lambda e: e.update(read=hex(number(e["read"]) & ~65535)),
    )
    mutate(
        "incoherent_ck_timestamp",
        lambda e: e["kind"] == "phy" and e["ck"] > 0,
        lambda e: e.update(ps=e["ps"] + 1),
    )
    mutate(
        "axi_read_data",
        lambda e: e["kind"] == "r" and e["response"] == 0,
        lambda e: e.update(data=hex(number(e["data"]) ^ 1)),
    )
    mutate(
        "axi_response_identity",
        lambda e: e["kind"] == "b",
        lambda e: e.update(id=e["id"] ^ 1),
    )
    # Move the first B immediately after its source AW. Physical CA/data still
    # retain their original timing, independently invalidating early success.
    aw = next(e for e in events if e["kind"] == "aw")
    mutate(
        "premature_axi_success",
        lambda e: e["kind"] == "b",
        lambda e: e.update(ps=aw["ps"] + 1),
    )
    variants["truncated_trace"] = copy.deepcopy(events[:-1])
    return variants


def thermal_sensitivity(events: list[dict], observations: list[list[dict]]) -> dict[str, list[dict]]:
    hot = next(((c, t) for c, channel in enumerate(observations) for t in channel if t["mr4"] in (13, 15)), None)
    if hot is None:
        return {}
    channel, temperature = hot
    altered = copy.deepcopy(events)
    removed = 0
    for event in altered:
        if (
            event["kind"] == "phy"
            and event["channel"] == channel
            and event["ck"] > temperature["completed_ck"]
            and event["cs"]
            and decode(event["ca"]).kind == "REF"
        ):
            event.update(cs=0, ca=0)
            removed += 1
    if not removed:
        raise RuntimeError("Hot sensitivity needs actual subsequent refresh commands")
    return {"missed_hot_refresh": altered}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--profile", type=int, default=0)
    parser.add_argument("--ranks", type=int, default=1)
    parser.add_argument("--depth", type=int, default=16)
    parser.add_argument("--mapped-end", type=lambda value: int(value, 0), default=1 << 20)
    parser.add_argument("--mr4-change-ck", type=int)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Refusing to overwrite evidence")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) / "lpddr5-integration/sensitivity"
    storage.mkdir(parents=True, exist_ok=True)
    dependencies = [
        *sorted((IP / "dv/integration").glob("*.py")),
        *sorted((IP / "dv/system").rglob("*.py")),
        *sorted((IP / "dv/contracts").rglob("*.py")),
        *sorted((IP / "config/contracts").glob("*.json")),
    ]
    report = {
        "argv": sys.argv,
        "python": platform.python_version(),
        "trace": str(args.trace.resolve()),
        "trace_sha256": hashlib.sha256(args.trace.read_bytes()).hexdigest(),
        "source_sha256": {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in dependencies},
        "status": "running",
        "negative_cases": {},
    }
    profile = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"][args.profile]
    try:
        report["baseline"] = check(
            args.trace,
            profile,
            ranks=args.ranks,
            depth=args.depth,
            mapped_end=args.mapped_end,
            mr4_change_ck=args.mr4_change_ck,
        )
        events = [json.loads(line) for line in args.trace.read_text().splitlines()]
        variants = sensitivity(events, profile) | thermal_sensitivity(
            events, report["baseline"]["temperature_returns_by_channel"]
        )
        for name, altered in variants.items():
            path = storage / (name + ".jsonl")
            path.write_text("".join(json.dumps(e) + "\n" for e in altered))
            try:
                check(
                    path,
                    profile,
                    ranks=args.ranks,
                    depth=args.depth,
                    mapped_end=args.mapped_end,
                    mr4_change_ck=args.mr4_change_ck,
                )
            except (Violation, ValueError) as error:
                if name == "missed_hot_refresh":
                    if str(error) != "missed periodic refresh":
                        raise RuntimeError(
                            "Hot refresh mutation failed at an unrelated boundary: " + str(error)
                        ) from error
                elif not isinstance(error, Violation):
                    raise
                report["negative_cases"][name] = {
                    "rejected": True,
                    "reason": str(error),
                    "mutated_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            else:
                raise RuntimeError("Checker accepted deliberate violation: " + name)
            finally:
                path.unlink()
        report["status"] = "passed"
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("Observation replay and sensitivity passed:", args.report)


if __name__ == "__main__":
    main()
