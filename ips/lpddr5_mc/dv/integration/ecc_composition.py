"""Correlate original AXI, host composition, real inline ECC and independent pins.

Internal identities/overlays explain ownership; source bytes and pin replay
establish effects. The fixed two-case family fails closed on serial fallback,
missing contributors, premature responses and absent individual settlement.
"""

import json
from collections import defaultdict, deque
from itertools import groupby
from pathlib import Path

from contracts.integrity import inline_decode
from ecc_composition_pins import replay
from system.ecc import InlineWitness
from system.host import require
from system.trace import number, source_order

DATA_BYTES = (1073741824 // 576) * 512
FULL = (1 << 64) - 1
AXI_PS = 2500


def payload(event, field="data"):
    return number(event[field]).to_bytes(64, "little")


def identity(event):
    return tuple(event[k] for k in ("tag", "generation", "boot", "config"))


def envelope(events, header, kinds):
    require(bool(events) and events[0] == header, "composition header")
    require(events[-1]["kind"] == "end", "composition missing end")
    last = -1
    for e in events[1:]:
        require(e["kind"] in kinds and type(e["ps"]) is int and e["ps"] >= last, "composition event/time")
        last = e["ps"]
    require(sum(e["kind"] == "end" for e in events) == 1, "composition duplicate/end coverage")


def source_cycles(events):
    """Bind every source payload record to its sampled VALID/READY edge.

    A missing record on an asserted VALID edge, or a vanished held VALID,
    must fail even if a later response has the expected ID and data.
    """
    last, held, stalls = None, {}, defaultdict(int)
    for ps, group in groupby(events[1:-1], key=lambda e: e["ps"]):
        rows = list(group)
        cycles = [e for e in rows if e["kind"] == "axi"]
        source_rows = [e for e in rows if e["kind"] in ("aw", "ar", "w", "b", "r")]
        if not cycles:
            require(not source_rows, "composition source record without cycle")
            continue
        require(len(cycles) == 1 and cycles[0]["port"] == 0, "composition source cycle identity")
        require(last is None or ps == last + AXI_PS, "composition source cycle gap")
        last, flags = ps, cycles[0]["flags"]
        require(type(flags) is int and 0 <= flags < 1024, "composition source flag width")
        for kind, valid, ready in (("aw", 9, 8), ("ar", 7, 6), ("w", 5, 4), ("b", 3, 2), ("r", 1, 0)):
            present = [e for e in source_rows if e["kind"] == kind]
            wanted = bool(flags >> valid & 1) and (kind in ("b", "r") or bool(flags >> ready & 1))
            require(len(present) == int(wanted), "composition cycle/payload coverage")
            if kind not in ("b", "r"):
                continue
            require(kind not in held or bool(present), "composition held VALID disappeared")
            if present:
                e = present[0]
                require(e["ready"] == (flags >> ready & 1), "composition response/cycle ready")
                value = (e["id"], e["response"], e.get("data"), e.get("last"))
                require(kind not in held or value == held[kind], "composition held response changed")
                if e["ready"]:
                    held.pop(kind, None)
                else:
                    held[kind] = value
                    stalls[kind, 0 if e["id"] < 168 else 1] += 1
    require(last is not None and not held, "composition source cycle/held tail")
    require(
        all(stalls[kind, case] >= 2 for kind in ("b", "r") for case in range(2)), "composition missing B/R hold probes"
    )
    return {f"{kind}_case{case}": stalls[kind, case] for kind in ("b", "r") for case in range(2)}


def source(events):
    envelope(
        events,
        {"kind": "header", "version": 1, "ports": 1, "channels": 1, "bus_bytes": 16},
        {"aw", "ar", "w", "b", "r", "axi", "phy", "end"},
    )
    ledger, waiting, admissions = {}, deque(), []
    for e in events:
        kind = e["kind"]
        if kind in ("aw", "ar"):
            n = e["id"]
            require(e["port"] == 0 and n not in ledger, "composition original identity")
            require(
                e["length"] == 3 and e["size"] == 4 and e["burst"] == 1 and not e["lock"],
                "composition source descriptor",
            )
            ledger[n] = {"event": e, "write": kind == "aw", "bytes": {}, "beats": 0, "last_ps": e["ps"]}
            admissions.append(n)
            if kind == "aw":
                waiting.append(n)
        elif kind == "w":
            require(e["port"] == 0 and bool(waiting), "composition W without AW")
            t = ledger[waiting[0]]
            require(t["beats"] < 4 and bool(e["last"]) == (t["beats"] == 3), "composition W count/last")
            data, mask = number(e["data"]).to_bytes(16, "little"), number(e["strobe"])
            require(0 <= mask < 1 << 16, "composition source strobe width")
            for lane, value in enumerate(data):
                if mask >> lane & 1:
                    t["bytes"][t["beats"] * 16 + lane] = value
            t["beats"] += 1
            t["last_ps"] = e["ps"]
            if e["last"]:
                waiting.popleft()
    require(admissions == [160, 161, 162, 163, 168, 169, 170, 171] and not waiting, "composition workload originals")
    for base, address in ((160, 0x4000), (168, 0x8000)):
        require(
            all(number(ledger[n]["event"]["address"]) == address for n in range(base, base + 4)),
            "composition source address",
        )
        require(
            ledger[base + 3]["event"]["ps"] < ledger[base + 2]["last_ps"],
            "composition missing incomplete-W read hazard",
        )
        require(
            len(ledger[base]["bytes"]) == 64
            and 0 < len(ledger[base + 1]["bytes"]) < 64
            and 0 < len(ledger[base + 2]["bytes"]) < 64,
            "composition missing full/partial source writes",
        )
        a, b = ledger[base + 1]["bytes"], ledger[base + 2]["bytes"]
        require(
            a.keys() & b.keys() and a.keys() - b.keys() and set(range(64)) - (a.keys() | b.keys()),
            "composition missing overwritten/surviving/preserved bytes",
        )
    return ledger, admissions


def correlate(events, witnesses, accesses):
    ledger, admissions = source(events)
    envelope(
        witnesses,
        {
            "kind": "header",
            "version": 1,
            "ports": 1,
            "channels": 1,
            "bus_bytes": 16,
            "inline_ecc": 1,
            "posted_early": 0,
        },
        {"import", "hazard", "request", "physical", "result", "complete", "flip", "end"},
    )
    imports = [e for e in witnesses if e["kind"] == "import"]
    require([e["id"] for e in imports] == admissions, "composition missing/reordered original import")
    require([e["sequence"] for e in imports] == list(range(8)), "composition import sequence")
    require(
        len({(e["write"], e["slot"], e["generation"]) for e in imports}) == 8, "composition original generation reuse"
    )
    imported = {e["id"]: e for e in imports}
    for e in imports:
        t = ledger[e["id"]]
        require(bool(e["write"]) == t["write"] and e["ps"] > t["event"]["ps"], "composition source/import binding")
    for base in (160, 168):
        hazards = [e for e in witnesses if e["kind"] == "hazard" and e["read_id"] == base + 3]
        require(
            len(hazards) >= 2 and hazards[0]["ps"] < ledger[base + 2]["last_ps"],
            "composition missing core incomplete-W hazard samples",
        )
        for e in hazards:
            require(
                e["writer_id"] == base + 2
                and e["read_generation"] == imported[base + 3]["generation"]
                and e["writer_generation"] == imported[base + 2]["generation"]
                and e["ps"] > imported[base + 3]["ps"]
                and e["blocked"] == e["read_buffered"] == e["read_validated"] == 1
                and e["forwardable"] == e["read_pending"] == e["read_offered"] == 0,
                "composition incomplete-W blocking/identity",
            )
    require(
        all(e["read_id"] in (163, 171) for e in witnesses if e["kind"] == "hazard"),
        "composition unknown core hazard",
    )
    phases = {phase: {} for phase in ("offer", "take")}
    completed = {}
    for e in witnesses:
        if e["kind"] == "request":
            require(e["phase"] in phases, "composition request phase")
            rows = phases[e["phase"]]
            require(identity(e) not in rows, "composition duplicate request phase")
            rows[identity(e)] = e
        elif e["kind"] == "complete":
            require(identity(e) not in completed, "composition duplicate final integrity")
            completed[identity(e)] = e
    offers, takes = phases["offer"], phases["take"]
    require(
        len(takes) == 6 and offers.keys() == takes.keys() == completed.keys(), "composition logical operation coverage"
    )
    operations, by_original = [], {}
    for key, take in takes.items():
        offer, done = offers[key], completed[key]
        require(offer["ps"] <= take["ps"] < done["ps"], "composition offer/accept/final order")
        for field in ("write", "id", "address", "mask", "data", "overlay_mask", "overlay_data", "owners"):
            require(offer[field] == take[field], "composition held request changed")
        owner_ids = [o["id"] for o in take["owners"]]
        require(owner_ids == sorted(set(owner_ids)) and bool(owner_ids), "composition original owner identities")
        require(
            take["id"] == owner_ids[0] and bool(done["write"]) == bool(take["write"]), "composition owner/direction"
        )
        require(take["generation"] == imported[owner_ids[0]]["generation"], "composition logical owner generation")
        for observation in (offer, take):
            for owner in observation["owners"]:
                n = owner["id"]
                require(n in ledger and n not in by_original, "composition duplicate/unknown original owner")
                t, imported_at = ledger[n], imported[n]
                require(
                    bool(t["write"]) == bool(take["write"])
                    and take["address"] == number(t["event"]["address"])
                    and owner["slot"] == imported_at["slot"]
                    and owner["generation"] == imported_at["generation"]
                    and owner["buffered"] == owner["validated"] == 1
                    and observation["ps"] > max(t["last_ps"], imported_at["ps"]),
                    "composition owner generation/complete validation",
                )
        require(
            [o["order"] for o in take["owners"]] == sorted({o["order"] for o in take["owners"]}),
            "composition merge original order",
        )
        expected_bytes = {}
        for n in owner_ids:
            expected_bytes.update(ledger[n]["bytes"])
        if take["write"]:
            require(number(take["mask"]) == sum(1 << b for b in expected_bytes), "composition merged byte enables")
            require(
                all(payload(take)[b] == value for b, value in expected_bytes.items()), "composition merged byte value"
            )
        else:
            require(len(owner_ids) == 1 and number(take["mask"]) == FULL, "composition read original/mask")
        op = {"take": take, "offer": offer, "done": done, "owners": owner_ids, "bytes": expected_bytes}
        operations.append(op)
        by_original.update((n, op) for n in owner_ids)
    require(by_original.keys() == ledger.keys(), "composition original ownership coverage")
    require(
        all(operations[i]["done"]["ps"] < operations[i + 1]["take"]["ps"] for i in range(5)),
        "composition serialized inline owner",
    )
    for case, base in enumerate((160, 168)):
        first, read, merged = operations[case * 3 : case * 3 + 3]
        require(
            [op["owners"] for op in (first, read, merged)] == [[base], [base + 3], [base + 1, base + 2]],
            "composition serial fallback/missing merge or forwarding",
        )
        require(read["take"]["ps"] < merged["take"]["ps"], "composition read did not precede contributor execution")
        expected = {}
        for n in range(base, base + 3):
            expected.update(ledger[n]["bytes"])
        for observation in (read["offer"], read["take"]):
            deps = observation["contributors"]
            ids = [d["id"] for d in deps]
            require(
                len(set(ids)) == len(ids) and {base + 1, base + 2} <= set(ids) <= set(range(base, base + 3)),
                "composition missing/unknown integrity contributor",
            )
            required_mask = 0
            for d in deps:
                imported_at = imported[d["id"]]
                require(observation["ps"] > ledger[d["id"]]["last_ps"], "composition read before contributor full W")
                require(
                    (d["slot"], d["generation"]) == (imported_at["slot"], imported_at["generation"]),
                    "composition contributor generation",
                )
                if by_original[d["id"]]["done"]["ps"] >= observation["ps"]:
                    required_mask |= sum(1 << b for b in ledger[d["id"]]["bytes"])
            mask = number(observation["overlay_mask"])
            require(0 <= mask <= FULL and mask & required_mask == required_mask, "composition missing byte overlay")
            require(
                all(payload(observation, "overlay_data")[b] == expected[b] for b in range(64) if mask >> b & 1),
                "composition forwarded byte overlay",
            )
        read["expected"] = bytes(expected[b] for b in range(64))
    physical = [e for e in witnesses if e["kind"] == "physical"]
    results = [e for e in witnesses if e["kind"] == "result"]
    require(len(physical) == len(results) == len(accesses) == 18, "composition physical operation coverage")
    require([e["tag"] for e in physical] == list(range(1, 19)), "composition physical sequence identity")
    bound = defaultdict(list)
    for request, result, pins in zip(physical, results, accesses, strict=True):
        key = request["owner"], request["owner_generation"], request["boot"], request["config"]
        require(key in takes, "composition unknown inline physical owner")
        take, done = takes[key], completed[key]
        require(
            request["generation"] == 0 and identity(request) == identity(result), "composition physical result identity"
        )
        require(
            bool(request["write"]) == bool(result["write"]) == bool(pins["write"])
            and request["address"] == pins["address"],
            "composition physical owner/address/direction",
        )
        require(number(request["mask"]) == FULL, "composition inline physical enables")
        tail = pins["tail_ps"] if pins["write"] else pins["return_tail_ps"]
        require(
            take["ps"] < request["ps"] < pins["ps"] and tail < result["ps"] < done["ps"],
            "composition physical/result/final chronology",
        )
        require(result["physical"] == result["integrity"] == 1 and not result["failed"], "composition backend result")
        if pins["write"]:
            require(payload(request) == pins["data"], "composition inline request/raw write bytes")
        else:
            require(payload(result) == pins["data"], "composition returned raw data/result")
        bound[key].append(pins)
    for index, op in enumerate(operations):
        take, done = op["take"], op["done"]
        pins = bound[identity(take)]
        address, write = take["address"], bool(take["write"])
        check_address = DATA_BYTES + (address // 512) * 64
        expected_steps = [(False, check_address), (False, address)]
        if write and index != 5:
            expected_steps += [(True, address), (True, check_address)]
        require([(p["write"], p["address"]) for p in pins] == expected_steps, "composition data/check owner steps")
        data, checks = bytearray(pins[1]["data"]), bytearray(pins[0]["data"])
        failed = False
        for word in range(8):
            old = int.from_bytes(data[word * 8 : word * 8 + 8], "little")
            check_index = (address // 8 + word) % 64
            if not write or not all(word * 8 + b in op["bytes"] for b in range(8)):
                decoded, status = inline_decode(InlineWitness.codeword(old, checks[check_index]))
                failed |= status == "uncorrectable"
                require(status != "corrected", "composition unexpected correction/scrub")
                data[word * 8 : word * 8 + 8] = decoded.to_bytes(8, "little")
        if write:
            for b, value in op["bytes"].items():
                data[b] = value
            for word in range(8):
                checks[(address // 8 + word) % 64] = InlineWitness.check_byte(
                    int.from_bytes(data[word * 8 : word * 8 + 8], "little")
                )
        require(
            failed == (index == 5) and bool(done["failed"]) == failed and done["integrity"] == 1,
            "composition independent ECC/final integrity",
        )
        require(
            done["physical"] == int(not failed) and done["response"] == (2 if failed else 0),
            "composition correlated completion outcome",
        )
        require(payload(done) == (bytes(64) if failed else data), "composition final logical data")
        if write and not failed:
            require(pins[2]["data"] == data and pins[3]["data"] == checks, "composition raw merged data/check image")
            require(pins[2]["tail_ps"] < pins[3]["tail_ps"] < done["ps"], "composition delayed final check integrity")
        if not write:
            base = op["owners"][0] - 3
            require(
                data == bytes(ledger[base]["bytes"][b] for b in range(64)) and data != op["expected"],
                "composition absent physical-old/forwarded-new witness",
            )
    flips = [e for e in witnesses if e["kind"] == "flip"]
    require(
        len(flips) == 1 and flips[0]["address"] == 0x800B and flips[0]["xor"] == 192,
        "composition external double-bit injection",
    )
    require(
        operations[4]["done"]["ps"] < flips[0]["ps"] < bound[identity(operations[5]["take"])][0]["ps"],
        "composition external fault after old read/before contributor RMW",
    )
    require(
        flips[0]["address"] - operations[5]["take"]["address"] not in operations[5]["bytes"],
        "composition injected byte must survive both partial writers",
    )
    response_check(events, ledger, by_original)
    stalls = source_cycles(events)
    require(
        {
            k: witnesses[-1].get(k)
            for k in (
                "settled",
                "host_fault",
                "channel_fault",
                "stale",
                "late_error",
                "state",
                "drained",
                "pin_drained",
                "ecc_drained",
            )
        }
        == dict(
            settled=1,
            host_fault=0,
            channel_fault=0,
            stale=0,
            late_error=0,
            state=1,
            drained=1,
            pin_drained=1,
            ecc_drained=1,
        ),
        "composition final owner settlement",
    )
    require(
        events[-1]["ps"] >= witnesses[-1]["ps"] > max(e["ps"] for e in events if e["kind"] in ("b", "r")),
        "composition observation closure",
    )
    return {
        "cases": 2,
        "original_writes": 6,
        "original_reads": 2,
        "logical_writes": 4,
        "logical_reads": 2,
        "forwarded_reads_with_unfinished_contributors": 2,
        "merged_original_pairs": 2,
        "physical_reads": 12,
        "physical_writes": 6,
        "success_case_physical_writes": 4,
        "success_case_unmerged_write_path_operations": 6,
        "individual_B": 6,
        "individual_R_beats": 8,
        "held_samples": stalls,
        "controller_qualified": False,
    }


def response_check(events, ledger, by_original):
    seen_b, read_at, held = set(), defaultdict(int), {}
    for e in events:
        kind = e["kind"]
        if kind not in ("b", "r"):
            continue
        n = e["id"]
        require(e["port"] == 0 and n in ledger and ledger[n]["write"] == (kind == "b"), "composition response original")
        op = by_original[n]
        ready_ps = op["done"]["ps"]
        if kind == "r":
            base = n - 3
            ready_ps = max(ready_ps, *(by_original[k]["done"]["ps"] for k in range(base, base + 3)))
        require(e["ps"] > ready_ps, "composition response before final contributor integrity")
        response = 2 if n in (169, 170, 171) else 0
        require(e["response"] == response, "composition response outcome/propagated contributor failure")
        value = (e["response"], e.get("data"), e.get("last"))
        key = kind, n
        require(key not in held or held[key] == value, "composition held response changed")
        if kind == "b":
            require(n not in seen_b, "composition duplicate original B")
        else:
            at = read_at[n]
            wanted = bytes(64) if response else op["expected"]
            require(
                at < 64
                and number(e["data"]) == int.from_bytes(wanted[at : at + 16], "little")
                and bool(e["last"]) == (at == 48),
                "composition R overlay/count/last",
            )
        if e["ready"]:
            held.pop(key, None)
            if kind == "b":
                seen_b.add(n)
            else:
                read_at[n] += 16
        else:
            held[key] = value
    require(
        not held and seen_b == {n for n in ledger if ledger[n]["write"]} and dict(read_at) == {163: 64, 171: 64},
        "composition individual response settlement",
    )


def check(trace: Path, witness_trace: Path, log: Path, profile):
    require(
        log.read_text().count(
            "DATAPATH_ECC_COMPOSITION_PASS cases=2 original_writes=6 original_reads=2 logical_writes=4 logical_reads=2"
        )
        == 1,
        "composition simulation completion",
    )
    with trace.open() as stream:
        events = list(source_order(stream))
    witnesses = [json.loads(line) for line in witness_trace.read_text().splitlines()]
    flips = [e for e in witnesses if e["kind"] == "flip"]
    accesses = replay(events, profile, flips)
    return correlate(events, witnesses, accesses)
