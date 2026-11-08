"""Count actual concurrent admissions, retained credits and offered stalls."""

import re
from collections import Counter, defaultdict, deque

from system.host import Address, byte_ranges, require
from system.trace import number, source_order


def measure(path, log, depth):
    require(
        re.findall(r"DATAPATH_OVERLAP_PASS turns=(\d+) depth=(\d+)", log.read_text()) == [("4", str(depth))],
        "overlap completion/configuration marker",
    )
    live, source_w = defaultdict(deque), defaultdict(deque)
    counts, maximum, full_stalls, offered_stalls = Counter(), Counter(), Counter(), Counter()
    overlap_admissions = 0
    ended = False
    with path.open() as stream:
        events = source_order(stream)
        header = next(events)
        require(header["kind"] == "header" and header["ports"] == 2, "overlap source header")
        for e in events:
            require(not ended, "overlap event after tail")
            kind = e["kind"]
            if kind in ("aw", "ar"):
                p, write = e["port"], kind == "aw"
                a = Address(p, e["id"], number(e["address"]), e["length"], e["size"], e["burst"])
                hazard = {b for beat in byte_ranges(a, header["bus_bytes"]) for b in beat}
                overlap_admissions += int(any(hazard & t["hazard"] for q in live.values() for t in q))
                t = {"address": a, "hazard": hazard, "beats": 0, "w": 0}
                live[p, write].append(t)
                if write:
                    source_w[p].append(t)
                maximum[p, write] = max(maximum[p, write], len(live[p, write]))
                require(len(live[p, write]) <= depth, "source response credit overflow")
                counts[kind] += 1
            elif kind == "w":
                p = e["port"]
                require(bool(source_w[p]), "overlap W without AW")
                t = source_w[p][0]
                t["w"] += 1
                if e["last"]:
                    require(t["w"] == t["address"].length + 1, "overlap W count")
                    source_w[p].popleft()
            elif kind in ("b", "r"):
                key = e["port"], kind == "b"
                require(bool(live[key]), "overlap response without admission")
                t = live[key][0]
                require(e["response"] == 0 and e["id"] == t["address"].id, "overlap response order/status")
                if not e["ready"]:
                    offered_stalls[key] += 1
                else:
                    t["beats"] += 1
                    if kind == "b" or e["last"]:
                        require(
                            t["w"] == t["address"].length + 1 if kind == "b" else t["beats"] == t["address"].length + 1,
                            "overlap incomplete source response",
                        )
                        live[key].popleft()
            elif kind == "axi":
                p, f = e["port"], e["flags"]
                for write, valid, ready in ((True, 9, 8), (False, 7, 6)):
                    if f >> valid & 1 and not (f >> ready & 1) and len(live[p, write]) == depth:
                        full_stalls[p, write] += 1
            elif kind == "end":
                require(not any(live.values()) and not any(source_w.values()), "overlap unsettled tail")
                ended = True
    keys = [(p, write) for p in range(2) for write in (False, True)]
    require(ended and counts == {"aw": 8 + 2 * (depth + 1), "ar": 12 + 2 * (depth + 1)}, "overlap source cases")
    require(overlap_admissions >= 12, "missing accepted overlap hazards")
    require(all(maximum[k] == depth and full_stalls[k] >= 32 for k in keys), "missing retained full-credit pressure")
    require(all(offered_stalls[k] > 0 for k in keys), "missing offered B/R stalls")
    return {
        "admissions": dict(counts),
        "overlap_admissions": overlap_admissions,
        "by_port_direction": [
            {
                "port": p,
                "write": write,
                "maximum_live": maximum[p, write],
                "full_credit_blocked_samples": full_stalls[p, write],
                "offered_stall_samples": offered_stalls[p, write],
            }
            for p, write in keys
        ],
        "limits": ["Directed serial-equivalent granules; no forwarding/merging implementation or ECC coverage claim."],
    }
