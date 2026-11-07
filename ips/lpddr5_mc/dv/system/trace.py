"""Strict JSONL adapter for sampled AXI/pin observations.

Adapters assign correlation keys at admission; keys never become memory data.
`pin` events must come from the external memory/PHY model, not DUT completion.
Every trace has a header and end marker. Unknown events/fields, stale identities,
truncation and unsettled transactions fail closed.
"""

import argparse
import json
from itertools import groupby
from pathlib import Path

from system.host import Address, HostScoreboard, require

FIELDS = {
    "aw": {"key", "port", "id", "address", "length", "size", "burst", "lock"},
    "ar": {"key", "port", "id", "address", "length", "size", "burst", "lock"},
    "w": {"port", "data", "strobe", "last"},
    "pin": {"key", "address", "hex", "enables", "epoch"},
    "physical": {"key"},
    "integrity": {"key", "failed"},
    "b": {"key", "response", "ready", "posted"},
    "r": {"key", "data", "response", "last", "ready"},
    "fault": set(),
    "reset": {"destructive"},
    "end": set(),
}


def number(value: str | int) -> int:
    return int(value, 0) if isinstance(value, str) else value


def source_order(stream):
    """Same-instant admissions use canonical ties, never print-process order.

    All timestamps are simulator source-edge picoseconds. CDC import events
    must not replace them. A bounded instant is buffered so independently
    scheduled SV observers cannot accidentally reverse simultaneous AW/AR.
    """

    def records():
        for line in stream:
            require(len(line) <= 65536, "oversized trace record")
            event = json.loads(line)
            require(type(event) is dict, "trace record must be an object")
            yield event

    events = records()
    header = next(events, None)
    require(header is not None, "empty trace")
    yield header
    for _, instant in groupby(events, key=lambda e: e.get("ps")):
        batch = []
        for event in instant:
            require(len(batch) < 4096, "oversized sampled instant")
            batch.append(event)
        admissions = [e for e in batch if e.get("kind") in ("aw", "ar")]
        yield from sorted(admissions, key=lambda e: (e["port"], e["kind"] == "ar"))
        yield from (e for e in batch if e.get("kind") not in ("aw", "ar"))


def read_trace(path: Path) -> dict:
    host = None
    keys = {}
    ended = False
    last = -1
    count = 0
    with path.open() as stream:
        for line_number, event in enumerate(source_order(stream), 1):
            if line_number == 1:
                require(
                    set(event) == {"kind", "version", "bus_bytes", "ports", "capacity", "source"}
                    and event["kind"] == "header"
                    and event["version"] == 1,
                    "trace header/version",
                )
                host = HostScoreboard(event["bus_bytes"], event["ports"], event["capacity"])
                source = event["source"]
                continue
            require(host is not None and not ended, "event after end")
            kind = event.get("kind")
            require(kind in FIELDS and set(event) == FIELDS[kind] | {"kind", "ps"}, "trace kind/fields")
            at = event["ps"]
            require(type(at) is int and at >= last, "trace time/order")
            last = at
            count += 1
            if kind in ("aw", "ar"):
                key = event["key"]
                require(key not in keys, "duplicate correlation key")
                a = Address(
                    **{
                        k: number(v) if k == "address" else v
                        for k, v in event.items()
                        if k in {"port", "id", "address", "length", "size", "burst", "lock"}
                    }
                )
                keys[key] = host.admit(a, kind == "aw")
            elif kind == "w":
                host.w(event["port"], number(event["data"]), number(event["strobe"]), event["last"])
            elif kind == "pin":
                host.pin_write(
                    keys[event["key"]],
                    number(event["address"]),
                    bytes.fromhex(event["hex"]),
                    number(event["enables"]),
                    event["epoch"],
                )
            elif kind == "physical":
                host.physical_done(keys[event["key"]])
            elif kind == "integrity":
                host.integrity_done(keys[event["key"]], event["failed"])
            elif kind in ("b", "r"):
                tag = keys[event["key"]]
                require(host.transactions[tag].write == (kind == "b"), "response direction/key mismatch")
                host.offer(
                    tag,
                    0 if kind == "b" else number(event["data"]),
                    event["response"],
                    True if kind == "b" else event["last"],
                    posted=event.get("posted", False),
                )
                if event["ready"]:
                    host.handshake(tag)
            elif kind == "fault":
                host.fail()
            elif kind == "reset":
                host.reset(destructive=event["destructive"])
                keys.clear()
            elif kind == "end":
                host.finish()
                ended = True
    require(ended and host is not None, "truncated/empty trace")
    return {
        "source": source,
        "events": count,
        "observed_until_ps": last,
        "write_bytes": host.handshaken_bytes[True],
        "read_bytes": host.handshaken_bytes[False],
        "late_errors": sorted(host.late_errors),
        "controller_qualified": False,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("trace", type=Path)
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args()
    result = read_trace(args.trace)
    with args.report.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
