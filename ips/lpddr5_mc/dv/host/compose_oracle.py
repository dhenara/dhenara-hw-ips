"""Check focused host RTL observations with the independent serial/merge oracles.

The fixture supplies AW/W/AR, emitted granules, actual responder completions
and offered AXI responses. Expected bytes come from the independent serial
ledger. Request counts and unfinished contributors distinguish exercised
forwarding/merging from a serial fallback. No DFI or ECC arithmetic is modeled
by this owning-layer check.
"""

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from system.host import Address, HostScoreboard, Violation, require
from system.merge import Context, MergeMonitor


def events(text):
    result = []
    for line in text.splitlines():
        if line.startswith("COMPOSE_CASE "):
            result.append({"kind": "case", "name": line.split()[1]})
        elif line.startswith("HOST_EVENT "):
            result.append(json.loads(line.removeprefix("HOST_EVENT ")))
    return result


def check(trace, bus_bytes=16):
    h, merge = None, None
    slots, physical, requested, read_requests = {}, {}, set(), {}
    counts = {
        "forwarded_reads": 0,
        "merged_originals": 0,
        "stale_completions": 0,
        "responses": 0,
    }
    cases = []
    case = ""

    def finish():
        if h is not None:
            h.finish()
            if merge is not None:
                merge.finish()

    for e in trace:
        kind = e["kind"]
        if kind == "case":
            finish()
            h = None
            case = e["name"]
            cases.append(case)
        elif kind == "reset":
            finish()
            h = HostScoreboard(bus_bytes=bus_bytes, ports=2, capacity=64)
            h.memory = {b: (b * 29 + 7) % 256 for b in range(65536)}
            h.serial = dict(h.memory)
            merge = MergeMonitor(h)
            slots, physical, requested, read_requests = {}, {}, set(), {}
        elif kind in ("aw", "ar"):
            key = e["port"], kind == "aw", e["slot"], e["generation"]
            require(key not in slots, "source slot generation reused before response")
            slots[key] = h.admit(
                Address(e["port"], e["id"], e["address"], e["len"], e["size"], e["burst"]),
                kind == "aw",
            )
        elif kind == "w":
            h.w(e["port"], int(e["data"], 16), e["mask"], bool(e["last"]))
        elif kind == "fatal":
            # This fixture faults an unmerged writer. Check that no physical
            # merge is outstanding before abandoning the trusted merge epoch;
            # host failure still has to settle every original response.
            merge.finish()
            merge = None
            h.fail()
        elif kind == "request":
            key = e["tag"], e["generation"], e["boot"], e["config"]
            require(key not in physical, "duplicate physical identity")
            originals = [
                n
                for n, t in h.transactions.items()
                if t.write == bool(e["write"])
                and n not in requested
                and not t.error
                and any(e["address"] <= b < e["address"] + 64 for b in t.hazard)
            ]
            require(bool(originals), "request without original")
            if e["write"] and case in ("merge-three", "merge-failure"):
                require(len(originals) == 3, "missing merged original")
                for n in originals:
                    merge.context(n, Context(0, 0, 0, 0, 0, e["boot"], e["config"]))
                merge.begin(e["tag"], originals)
                counts["merged_originals"] += len(originals)
            else:
                originals = originals[:1]
            requested.update(originals)
            physical[key] = {
                "event": e,
                "originals": originals,
                "physical": False,
                "integrity": False,
            }
            if not e["write"]:
                n = originals[0]
                t = h.transactions[n]
                contributors = [
                    old
                    for k, old in h.transactions.items()
                    if k < n and old.write and not old.error and not old.integrity and old.hazard & t.hazard
                ]
                if contributors:
                    h.forwarded(n)
                    counts["forwarded_reads"] += 1
                read_requests[n] = physical[key]
        elif kind == "completion":
            key = e["tag"], e["generation"], e["boot"], e["config"]
            if key not in physical:
                counts["stale_completions"] += 1
                continue
            p = physical[key]
            request = p["event"]
            if e["physical"]:
                require(not p["physical"], "duplicate physical completion")
                if e["write"]:
                    data = int(request["data"], 16).to_bytes(64, "little")
                    if len(p["originals"]) > 1:
                        merge.pin(
                            request["tag"],
                            request["address"],
                            data,
                            request["mask"],
                            h.epoch,
                        )
                        merge.physical(request["tag"])
                    else:
                        n = p["originals"][0]
                        h.pin_write(n, request["address"], data, request["mask"], h.epoch)
                        h.physical_done(n)
                else:
                    n = p["originals"][0]
                    if not h.transactions[n].dependencies:
                        h.physical_done(n)
                p["physical"] = True
            if e["integrity"]:
                require(
                    not p["integrity"] and p["physical"],
                    "duplicate or premature integrity",
                )
                p["integrity"] = True
                p["failed"] = bool(e["failed"])
                if e["write"]:
                    for n in p["originals"]:
                        h.integrity_done(n, bool(e["failed"]))
        elif kind in ("b", "r"):
            key = e["port"], kind == "b", e["slot"], e["generation"]
            require(key in slots, "duplicate/unknown original response")
            n = slots[key]
            t = h.transactions[n]
            if kind == "r" and not t.error and not h.faulted and not t.integrity:
                p = read_requests[n]
                require(p["physical"] and p["integrity"], "R before actual read result")
                failed = p["failed"] or any(h.transactions[k].failed for k in t.dependencies)
                h.integrity_done(n, failed)
            h.offer(
                n,
                0 if kind == "b" else int(e["data"], 16),
                e["response"],
                kind == "b" or bool(e["last"]),
            )
            if e["ready"]:
                h.handshake(n)
                if t.done:
                    del slots[key]
                    counts["responses"] += 1
    finish()
    require(
        len(cases) == 10 and counts["forwarded_reads"] >= 6 and counts["merged_originals"] == 6,
        "missing focused cases",
    )
    require(counts["stale_completions"] == 1, "missing stale epoch probe")
    return {"cases": cases, **counts}


def sensitivity(trace, bus_bytes):
    mutations = {}
    r = next(i for i, e in enumerate(trace) if e["kind"] == "r" and e["response"] == 0)
    m = next(
        i
        for i, e in enumerate(trace)
        if e["kind"] == "request"
        and e["write"]
        and i > next(j for j, e in enumerate(trace) if e.get("name") == "merge-three")
    )
    for name in (
        "forwarded_byte",
        "missing_dependency_result",
        "merged_byte",
        "missing_original_B",
        "duplicate_original_B",
    ):
        mutant = copy.deepcopy(trace)
        if name == "forwarded_byte":
            mutant[r]["data"] = f"{int(mutant[r]['data'], 16) ^ 1:064x}"
        elif name == "merged_byte":
            mutant[m]["data"] = f"{int(mutant[m]['data'], 16) ^ 1:0128x}"
        elif name == "missing_dependency_result":
            i = next(i for i, e in enumerate(mutant) if e["kind"] == "completion" and e["write"] and e["integrity"])
            del mutant[i]
        else:
            i = next(i for i, e in enumerate(mutant) if e["kind"] == "b" and e["ready"])
            if name == "missing_original_B":
                del mutant[i]
            else:
                mutant.insert(i + 1, dict(mutant[i]))
        try:
            check(mutant, bus_bytes)
        except (Violation, KeyError) as error:
            mutations[name] = str(error)
        else:
            raise AssertionError(f"mutation survived: {name}")
    return mutations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--bus-bytes", type=int, default=16)
    args = parser.parse_args()
    trace = events(args.log.read_text())
    result = {
        "observations": check(trace, args.bus_bytes),
        "mutations_rejected": sensitivity(trace, args.bus_bytes),
    }
    result.update(log_sha256=hashlib.sha256(args.log.read_bytes()).hexdigest(), status="passed")
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
