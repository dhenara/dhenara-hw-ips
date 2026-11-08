"""Replay actual benchmark observations and demonstrate active fault rejection.

A baseline must pass before any mutation counts as checker evidence. The
original trace/report stay unchanged; each mutation is recorded as an exact
record change and replayed through a disposable copy.
"""

import argparse
import copy
import gzip
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

IP = Path(__file__).resolve().parents[2]
REPO = IP.parents[1]
sys.path.insert(0, str(IP / "dv"))

from benchmark import check, number  # noqa: E402
from system.host import Violation, require  # noqa: E402


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def variants(events, direction):
    """Yield one exact record change at a time, keeping memory bounded."""

    def mutate(name, predicate, change):
        index = next(i for i, event in enumerate(events) if predicate(event))
        before = events[index]
        after = copy.deepcopy(before)
        change(after)
        return name, index, before, after

    yield mutate(
        "source_address",
        lambda e: e["kind"] in ("aw", "ar"),
        lambda e: e.update(address=hex(number(e["address"]) ^ 64)),
    )
    yield mutate(
        "source_or_response_payload",
        lambda e: e["kind"] == ("w" if direction == "write" else "r"),
        lambda e: e.update(data=hex(number(e["data"]) ^ 1)),
    )
    yield mutate(
        "response_identity",
        lambda e: e["kind"] == ("b" if direction == "write" else "r"),
        lambda e: e.update(id=e["id"] ^ 1),
    )
    first_source = next(e for e in events if e["kind"] in ("aw", "ar"))
    yield mutate(
        "premature_response",
        lambda e: e["kind"] == ("b" if direction == "write" else "r"),
        lambda e: e.update(ps=first_source["ps"] + 1),
    )
    returned = next(e for e in events if e["kind"] == "phy" and number(e["return"]) >> 32 & 255)
    lane = next(i for i in range(4) if number(returned["return"]) >> (32 + i * 2) & 3)
    yield mutate(
        "physical_return_data",
        lambda e: e is returned,
        lambda e: e.update({"return": hex(number(e["return"]) ^ (1 << (40 + lane * 32)))}),
    )
    yield mutate(
        "unexpected_return_parity",
        lambda e: e is returned,
        lambda e: e.update({"return": hex(number(e["return"]) ^ 1)}),
    )
    early = next(
        e
        for e in events
        if e["kind"] == "phy" and e["channel"] == returned["channel"] and e["ck"] == returned["ck"] - 1
    )
    yield mutate(
        "physically_premature_return",
        lambda e: e is early,
        lambda e: e.update({"return": returned["return"]}),
    )
    yield mutate(
        "missing_read_enable",
        lambda e: e["kind"] == "phy" and number(e["read"]) >> 16,
        lambda e: e.update(read=hex(number(e["read"]) & 65535)),
    )
    yield mutate(
        "continuous_read_cs_hole",
        lambda e: e["kind"] == "phy" and number(e["read"]) & 65535,
        lambda e: e.update(read=hex(number(e["read"]) & ~65535)),
    )
    yield mutate(
        "wck_edge",
        lambda e: e["kind"] == "phy" and number(e["wck"]) & 65535,
        lambda e: e.update(wck=hex(number(e["wck"]) ^ 1)),
    )
    yield mutate(
        "wrong_rank",
        lambda e: e["kind"] == "phy" and e["cs"] == 1,
        lambda e: e.update(cs=2),
    )
    yield mutate(
        "axi_clock",
        lambda e: e["kind"] == "axi",
        lambda e: e.update(ps=e["ps"] + 1),
    )
    if direction == "write":
        enabled = next(e for e in events if e["kind"] == "phy" and number(e["write"]) >> 24 & 255)
        lane = next(i for i in range(4) if number(enabled["write"]) >> (24 + i * 2) & 3)
        at = enabled["ck"] * 4 + lane + 4  # qualified profile4 tphy_wrdata
        yield mutate(
            "physical_write_data",
            lambda e: e["kind"] == "phy" and e["channel"] == enabled["channel"] and e["ck"] == at // 4,
            lambda e: e.update(write=hex(number(e["write"]) ^ (1 << (48 + (at % 4) * 32)))),
        )
    else:

        def different_return_words(e):
            if e["kind"] != "phy":
                return False
            raw = number(e["return"])
            return raw >> 32 & 15 == 15 and (raw >> 40 & 0xFFFFFFFF) != (raw >> 72 & 0xFFFFFFFF)

        def swap_words(e):
            raw = number(e["return"])
            difference = (raw >> 40 ^ raw >> 72) & 0xFFFFFFFF
            e["return"] = hex(raw ^ (difference << 40) ^ (difference << 72))

        yield mutate("return_word_identity", different_return_words, swap_words)
    end = next(i for i, e in enumerate(events) if e["kind"] == "end")
    yield "trace_tail_loss", end, events[end], None
    last_engine = max(i for i, e in enumerate(events) if e["kind"] == "engine")
    yield "diagnostic_tail_loss", last_engine, events[last_engine], None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--direction", choices=("read", "write"), required=True)
    parser.add_argument("--service", type=int, choices=(1, 2, 4), required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--sensitivity", action="store_true", help="Use small smoke traces for active mutation checks")
    args = parser.parse_args()
    require(not args.report.exists(), "Refusing to overwrite evidence")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    require(not args.sensitivity or args.smoke, "Mutation replay is bounded to smoke traces")
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
        "trace_sha256": sha(args.trace),
        "log_sha256": sha(args.log),
        "source_sha256": {str(p.relative_to(REPO)): sha(p) for p in dependencies},
        "status": "running",
        "negative_cases": {},
    }
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) / "lpddr5-integration" / args.report.stem
    temporary = storage / "mutation.jsonl"
    try:
        report["baseline"] = check(args.trace, args.log, args.direction, args.service, smoke=args.smoke)
        if args.sensitivity:
            storage.mkdir(parents=True, exist_ok=False)
            with gzip.open(args.trace, "rt") if args.trace.suffix == ".gz" else args.trace.open() as stream:
                events = [json.loads(line) for line in stream]
            for name, index, before, after in variants(events, args.direction):
                mutated = list(events)
                if after is None:
                    del mutated[index]
                else:
                    mutated[index] = after
                # Moving a response must test its physical prerequisite, not
                # merely violate record ordering in the adapter.
                header, body = mutated[0], sorted(mutated[1:], key=lambda e: e["ps"])
                temporary.write_text("".join(json.dumps(e) + "\n" for e in [header, *body]))
                try:
                    check(temporary, args.log, args.direction, args.service, smoke=args.smoke)
                except Violation as error:
                    report["negative_cases"][name] = {
                        "rejected": True,
                        "reason": str(error),
                        "record_index": index,
                        "before": before,
                        "after": after,
                        "mutated_sha256": sha(temporary),
                    }
                else:
                    raise RuntimeError("Checker accepted deliberate violation: " + name)
                finally:
                    temporary.unlink()
        report["status"] = "passed"
    except BaseException as error:
        report["status"], report["error"] = "failed", str(error)
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("Benchmark replay and selected sensitivity passed:", args.report)


if __name__ == "__main__":
    main()
