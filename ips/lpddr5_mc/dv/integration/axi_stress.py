"""Account for seeded stress coverage from sampled AXI traffic.

Run after connected replay, which checks data, protocol and physical effects.
These counters require the actual error streams and offered response stalls;
the simulation completion marker alone cannot establish stress coverage.
"""

import re
from collections import Counter, defaultdict, deque
from pathlib import Path

from system.host import Address, Violation, byte_ranges, require
from system.trace import number, source_order


def measure(path: Path, log: Path, seed: int) -> dict:
    markers = re.findall(r"DATAPATH_AXI_STRESS_PASS seed=(\d+) cases=(\d+)", log.read_text())
    require(markers == [(str(seed), "64")], "AXI stress seed/completion marker")
    counts = Counter()
    descriptors, stalls, responses = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    pending, writes = defaultdict(deque), defaultdict(deque)
    ended = False
    last_ps = -1
    with path.open() as stream:
        events = source_order(stream)
        header = next(events)
        require(header["kind"] == "header" and header["version"] == 1, "AXI stress trace header")
        bus_bytes, ports = header["bus_bytes"], header["ports"]
        for e in events:
            kind, ps = e["kind"], e["ps"]
            require(not ended and type(ps) is int and ps >= last_ps, "AXI stress time/tail")
            last_ps = ps
            if kind in ("aw", "ar"):
                p = e["port"]
                require(0 <= p < ports, "AXI stress source port")
                a = Address(p, e["id"], number(e["address"]), e["length"], e["size"], e["burst"])
                try:
                    ranges = byte_ranges(a, bus_bytes)
                    error = 0
                except Violation:
                    ranges, error = [()] * (a.length + 1), 3
                t = {"address": a, "ranges": ranges, "error": error, "w": 0, "r": 0, "aw_ps": ps}
                pending[p, kind == "aw", a.id].append(t)
                if kind == "aw":
                    writes[p].append(t)
                counts[kind] += 1
                d = descriptors[kind]
                d[f"burst_{a.burst}"] += 1
                d[f"size_{a.size}"] += 1
                d[f"beats_{a.length + 1}"] += 1
                d["narrow"] += int(1 << a.size < bus_bytes)
                d["unaligned"] += int(a.address % (1 << a.size) != 0)
                d["nonbase_wrap"] += int(a.burst == 2 and a.address % ((1 << a.size) * (a.length + 1)) != 0)
                d["invalid_descriptor"] += int(error == 3)
            elif kind == "w":
                p = e["port"]
                require(bool(writes[p]), "AXI stress W without AW")
                t = writes[p][0]
                a, i = t["address"], t["w"]
                require(i <= a.length, "AXI stress excess W")
                strobe = number(e["strobe"])
                allowed = sum(1 << (b % bus_bytes) for b in t["ranges"][i])
                if t["error"] != 3:
                    invalid = bool(strobe & ~allowed)
                    early = bool(e["last"]) and i != a.length
                    missing = not e["last"] and i == a.length
                    counts["invalid_strobe_beats"] += int(invalid)
                    counts["early_last_beats"] += int(early)
                    counts["missing_last_beats"] += int(missing)
                    counts["sparse_strobe_beats"] += int(strobe != 0 and strobe & allowed != allowed)
                    if invalid or early or missing:
                        t["error"] = 2
                if i == 0:
                    require(ps > t["aw_ps"], "AXI stress W did not follow accepted AW")
                    counts["delayed_w_streams"] += 1
                t["w"] += 1
                counts["w_beats"] += 1
                if i == a.length:
                    writes[p].popleft()
            elif kind in ("b", "r"):
                p, write = e["port"], kind == "b"
                q = pending[p, write, e["id"]]
                require(bool(q), "AXI stress response without admission")
                t = q[0]
                require(e["response"] == t["error"], "AXI stress response does not settle source error")
                if not e["ready"]:
                    stalls[str(p)][kind] += 1
                    continue
                counts[f"{kind}_handshakes"] += 1
                if write:
                    require(t["w"] == t["address"].length + 1, "AXI stress B before full W settlement")
                else:
                    t["r"] += 1
                    require(bool(e["last"]) == (t["r"] == t["address"].length + 1), "AXI stress R length/last")
                if write or e["last"]:
                    responses[kind][str(e["response"])] += 1
                    q.popleft()
            elif kind == "end":
                require(not any(writes.values()) and not any(pending.values()), "AXI stress unsettled transaction tail")
                ended = True
    require(ended and counts["aw"] == 64 and counts["ar"] == 68, "AXI stress incomplete source cases")
    require(responses["b"] == {"0": 48, "2": 12, "3": 4}, "AXI stress missing write response classes")
    require(responses["r"] == {"0": 64, "3": 4}, "AXI stress missing read response classes")
    require(
        all(counts[key] == 4 for key in ("invalid_strobe_beats", "early_last_beats", "missing_last_beats"))
        and counts["delayed_w_streams"] == 64
        and counts["sparse_strobe_beats"] > 0,
        "AXI stress missing W error/delay/strobe coverage",
    )
    for kind in ("aw", "ar"):
        d = descriptors[kind]
        require(
            all(d[f"burst_{b}"] > 0 for b in range(3))
            and all(d[f"size_{s}"] > 0 for s in range(bus_bytes.bit_length()))
            and all(d[f"beats_{n}"] > 0 for n in (2, 4, 8, 16))
            and all(d[key] > 0 for key in ("narrow", "unaligned", "nonbase_wrap"))
            and d["invalid_descriptor"] == 4,
            "AXI stress missing burst/width/boundary coverage",
        )
    require(all(stalls[str(p)][k] > 0 for p in range(ports) for k in ("b", "r")), "AXI stress missing offered stalls")
    return {
        "seed": seed,
        "counts": dict(counts),
        "descriptors": {k: dict(v) for k, v in descriptors.items()},
        "completed_transaction_responses": {k: dict(v) for k, v in responses.items()},
        "offered_stall_samples_by_port": {k: dict(v) for k, v in stalls.items()},
        "limits": ["Sequential transactions; concurrency, overlap forwarding and merging require separate evidence."],
    }
