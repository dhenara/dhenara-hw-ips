"""Independent source, physical image and dirty-transition checks for the wrapper.

The workload uses aligned 64-byte accesses and unique identities. The oracle
reconstructs source bytes, every clean-prefix CA/write/returned slice, shared
check-line contents and final response handshakes. PHY-error injection ends the
clean protocol claim; fresh boot has a separate complete physical trace.
"""

import copy
import json
import re
from collections import Counter, defaultdict, deque

from contracts.read_completion import CompletionOracle, ExpectedWord, ReadBusOracle
from system.adapters import returned_snapshot
from system.dram import decode
from system.ecc import InlineWitness
from system.host import Violation, require
from system.phy import DigitalPhy
from system.return_timing import ReturnTiming
from system.trace import number, source_order
from wck_observations import WckObservations

DATA_BYTES = (1073741824 // 576) * 512


def location(address):
    return (address >> 6) & 1, (address & 63) | ((address >> 7) << 6)


def check_images(images, memory, writes):
    """Bind all 64 check bytes, including untouched neighboring protected words."""
    for image in images:
        channel, address = image["channel"], image["address"]
        require(location(address)[0] == channel, "inline snapshot channel map")
        data = bytes(memory.get(address + i, 0) for i in range(64))
        if image["phase"] == "torn":
            data = bytes(writes[110]["data"])
        require(number(image["data"]).to_bytes(64, "little") == data, "inline source/physical data image")
        local = location(address)[1]
        checks = []
        for word in range(64):
            start = (local // 512) * 512 + word * 8
            logical = (start & 63) | (channel << 6) | ((start & ~63) << 1)
            value = int.from_bytes(bytes(memory.get(logical + i, 0) for i in range(8)), "little")
            checks.append(InlineWitness.check_byte(value))
        require(number(image["check"]).to_bytes(64, "little") == bytes(checks), "shared inline check-line merge")


def source(events, images, *, recovered=False):
    memory, writes, reads, held = {}, {}, {}, {}
    awaiting = defaultdict(deque)
    completed = Counter()
    events = sorted([*events, *({**x, "kind": "image"} for x in images)], key=lambda e: e.get("ps", -1))
    ended = False
    for e in events:
        kind = e["kind"]
        if kind == "image":
            check_images([e], memory, writes)
            continue
        require(not ended, "inline source event after tail")
        if kind in ("aw", "ar"):
            require(
                e["length"] == 3 and e["size"] == 4 and e["burst"] == 1 and not e["lock"], "inline source descriptor"
            )
            identity, address = e["id"], number(e["address"])
            require(
                address % 64 == 0 and identity not in writes and identity not in reads, "inline source identity/address"
            )
            if kind == "aw":
                writes[identity] = {
                    "port": e["port"],
                    "address": address,
                    "data": bytearray(),
                    "mask": [],
                    "last_ps": None,
                }
                awaiting[e["port"]].append(identity)
            else:
                reads[identity] = {
                    "port": e["port"],
                    "data": bytes(memory.get(address + i, 0) for i in range(64)),
                    "beats": 0,
                }
        elif kind == "w":
            require(bool(awaiting[e["port"]]), "inline W without AW")
            t = writes[awaiting[e["port"]][0]]
            t["data"].extend(number(e["data"]).to_bytes(16, "little"))
            t["mask"].extend(bool(number(e["strobe"]) >> i & 1) for i in range(16))
            require(bool(e["last"]) == (len(t["data"]) == 64), "inline W count/last")
            if e["last"]:
                identity = awaiting[e["port"]].popleft()
                t["last_ps"] = e["ps"]
                # The final failing pair updates physical data only, never its
                # authoritative check state. The torn snapshot checks it below.
                if identity != 110:
                    for i, enabled in enumerate(t["mask"]):
                        if enabled:
                            memory[t["address"] + i] = t["data"][i]
        elif kind in ("b", "r"):
            identity = e["id"]
            ledger = writes if kind == "b" else reads
            require(identity in ledger and ledger[identity]["port"] == e["port"], "inline response identity")
            t = ledger[identity]
            response = 2 if identity == 110 else 0
            require(e["response"] == response and completed[kind, identity] == 0, "inline response status/duplicate")
            if kind == "b":
                require(t["last_ps"] is not None and e["ps"] > t["last_ps"], "inline B before full W validation")
            else:
                at = t["beats"] * 16
                require(at < 64 and bool(e["last"]) == (at == 48), "inline R count/last")
                require(
                    number(e["data"]).to_bytes(16, "little") == t["data"][at : at + 16],
                    "inline older read snapshot/data",
                )
            key = kind, identity
            value = e["response"], e.get("data"), e.get("last")
            require(key not in held or held[key] == value, "inline held response changed")
            if e["ready"]:
                held.pop(key, None)
                if kind == "b" or e["last"]:
                    completed[key] += 1
                if kind == "r":
                    t["beats"] += 1
            else:
                held[key] = value
        elif kind == "end":
            require(not held and not any(awaiting.values()), "inline incomplete source tail")
            ended = True
    expected_writes = {120} if recovered else {*range(20, 24), *range(30, 34), 101, 108, 110}
    expected_reads = {121} if recovered else {*range(40, 44), 100, 102}
    require(ended and writes.keys() == expected_writes and reads.keys() == expected_reads, "inline workload identities")
    require(
        all(completed["b", i] == 1 for i in writes) and all(completed["r", i] == 1 for i in reads),
        "inline complete response settlement",
    )
    return writes, {"writes": len(writes), "reads": len(reads), "failed_writes": int(not recovered)}


def physical(events, profile, *, flip_ps=None, fault_ps=None, images=()):
    """Check the clean physical prefix without using DUT completion witnesses."""
    models = [DigitalPhy(copy.deepcopy(profile)) for _ in range(2)]
    wck = [WckObservations(profile) for _ in range(2)]
    timing = [ReturnTiming(profile) for _ in range(2)]
    completions = [CompletionOracle([], 16, profile["dfi_wck"]["tphy_rdlat"]) for _ in range(2)]
    buses = [ReadBusOracle(profile["ratio"], c) for c in completions]
    expected = [{}, {}]
    bursts, accesses = [[], []], []
    returned_access = [{}, {}]
    ratio, serial, flipped = profile["ratio"], 0, False
    origin_ps = None
    for e in sorted([*events, *({**x, "kind": "image"} for x in images)], key=lambda e: e.get("ps", -1)):
        if e["kind"] == "image":
            c, local = location(e["address"])
            for name, address in (("data", local), ("check", DATA_BYTES + (local // 512) * 64)):
                observed = bytes(
                    models[c].dram.memory.get(
                        (
                            (address + i) >> 30,
                            ((address + i) >> 11) & 15,
                            ((address + i) >> 15) & 32767,
                            (address + i) & 2047,
                        ),
                        0,
                    )
                    for i in range(64)
                )
                require(observed == number(e[name]).to_bytes(64, "little"), "raw pins/inline physical image")
            continue
        if e["kind"] != "phy" or fault_ps is not None and e["ps"] >= fault_ps:
            continue
        if flip_ps is not None and not flipped and e["ps"] > flip_ps:
            key = (0, 0, 0, 2)
            models[0].dram.memory[key] = models[0].dram.memory.get(key, 0) ^ 128
            flipped = True
        c, ck, ps = e["channel"], e["ck"], e["ps"]
        if origin_ps is None:
            origin_ps = ps - ck * profile["ck_ps"]
        require(ps == origin_ps + ck * profile["ck_ps"], "inline physical clock")
        model = models[c]
        wck[c].sample(ck, e["cs"], e["ca"], number(e["wck"]))
        b = model.command(ck, e["cs"], e["ca"])
        op = decode(e["ca"]) if e["cs"] else None
        if b is not None:
            require(b.beats == 32 and not b.masked, "inline full physical granule")
            serial += 1
            at = b.command_ck - profile["management_ck"]["tctrl_delay"]
            address = (b.rank << 30) | (b.row << 15) | (b.bank << 11) | (b.column << 5)
            access = {"channel": c, "write": b.write, "address": address, "ps": ps, "words": 0}
            # This is the conservative final physical write/path tail used by
            # the declared integration profile, independent of channel status.
            access["tail_ps"] = origin_ps + b.times[-1] * profile["ck_ps"] / (2 * ratio)
            if b.write:
                access["tail_ps"] += profile["dfi_wck"]["twrdata_delay"] * profile["ck_ps"] / ratio
            accesses.append(access)
            bursts[c].append((b, at, access))
            if not b.write:
                returned_access[c][serial] = access
                values = [model.dram.beat(b, i, b.times[i]) for i in range(b.beats)]
                for word in range(16):
                    expected[c][serial, word] = values[2 * word] | values[2 * word + 1] << 16
                    for lane in range(2):
                        enable = at * ratio + profile["dfi_wck"]["trddata_en"] + word + (8 if word >= 8 else 0)
                        completions[c].pending[lane].append(ExpectedWord(enable, serial, word, lane))
                completions[c].received[serial] = {}
        if op is not None and op.kind == "MRR":
            serial += 1
            for word in range(8):
                byte = 9 if op.operand == 4 else 0
                expected[c][serial, word] = 0xDA00DA00 | byte | byte << 16 if word < 4 else 0xDADADADA
                for lane in range(2):
                    enable = ck * ratio + profile["dfi_wck"]["trddata_en"] + word
                    completions[c].pending[lane].append(ExpectedWord(enable, serial, word, lane))
            completions[c].received[serial] = {}
        for lane in range(2):
            completions[c].pending[lane] = deque(sorted(completions[c].pending[lane], key=lambda w: w.enable))
        raw = number(e["write"])
        enabled = 0
        for burst, at, access in bursts[c]:
            if not burst.write:
                continue
            for word in range(16):
                enable = at * ratio + profile["dfi_wck"]["tphy_wrlat"] + word + (8 if word >= 8 else 0)
                if enable // ratio == ck:
                    enabled |= 3 << ((enable % ratio) * 2)
                data_at = enable + profile["dfi_wck"]["tphy_wrdata"]
                if data_at // ratio == ck:
                    lane = data_at % ratio
                    mask = raw >> (32 + lane * 4) & 15
                    require(mask == 0, "inline unexpected physical byte mask")
                    model.write_word(burst, word, data_at * 2, raw >> (48 + lane * 32) & 0xFFFFFFFF, mask)
                    access["words"] += 1
        require(raw >> 24 & 255 == enabled, "inline write enables/CA")
        raw = number(e["return"])
        timing[c].sample(ck, e["cs"], e["ca"], raw >> 32 & 255)
        decoded = returned_snapshot(
            ratio, data=raw >> 40, valid=raw >> 32 & 255, dnv=raw >> 16 & 65535, link_parity=raw & 65535
        )
        buses[c].snapshot(ck, decoded["data"], decoded["dnv"])
        require(not buses[c].unavailable, "inline unexpected unavailable slice")
        for tag, received in completions[c].received.items():
            for (word, lane), value in received.items():
                wanted = expected[c][tag, word]
                require(
                    value == ((wanted >> (lane * 8) & 255) | (wanted >> (16 + lane * 8) & 255) << 8),
                    "inline physical returned data",
                )
            if len(received) == 32 and tag in returned_access[c]:
                returned_access[c][tag].setdefault("return_tail_ps", ps)
    require(flip_ps is None or flipped, "missing physical fault injection")
    require(all(a["words"] == 16 for a in accesses if a["write"]), "inline physical write tail")
    if fault_ps is None:
        require(not any(q for c in completions for q in c.pending.values()), "inline returned physical tail")
        for m in [*models, *timing, *wck]:
            m.finish()
    return accesses


def ordered_effects(events, accesses, writes):
    """Bind each serialized check/data operation to a validated source owner."""
    physical_queues = [deque(a for a in accesses if a["channel"] == c) for c in range(2)]
    for e in events:
        if e["kind"] not in ("aw", "ar"):
            continue
        c, local = location(number(e["address"]))
        check_address = DATA_BYTES + (local // 512) * 64
        steps = [(False, check_address), (False, local)]
        if e["kind"] == "aw" or e["id"] == 100:
            steps.extend(((True, local), (True, check_address)))
        if e["id"] == 110:
            steps.pop()  # Injected loss follows data completion, before check.
        bound = []
        for write, address in steps:
            require(bool(physical_queues[c]), "missing inline physical owner effects")
            a = physical_queues[c].popleft()
            require((a["write"], a["address"]) == (write, address), "inline physical owner/order")
            validation = writes[e["id"]]["last_ps"] if e["kind"] == "aw" else e["ps"]
            require(a["ps"] > validation, "inline command before source validation")
            bound.append(a)
        if e["id"] != 110:
            ready_ps = max(a["tail_ps"] if a["write"] else a["return_tail_ps"] for a in bound)
            responses = [r for r in events if r["kind"] == ("b" if e["kind"] == "aw" else "r") and r["id"] == e["id"]]
            require(
                bool(responses) and all(r["ps"] > ready_ps for r in responses), "inline success before data/check tail"
            )
    require(not any(physical_queues), "unexpected inline physical owner effects")


def check(trace, recovered_trace, log, profile):
    text = log.read_text()
    require(
        text.count("CONTROLLER_INLINE_LIFECYCLE_PASS shared_granules=4 scrub=1 phy_error=1 boot=1") == 1,
        "inline lifecycle completion",
    )
    times = {}
    for name in ("FLIP", "QUIESCE_REQUEST", "PHY_ERROR"):
        values = re.findall(r"INLINE_" + name + r" ps=(\d+)", text)
        require(len(values) == 1, "inline injection marker")
        times[name] = int(values[0])
    require(times["FLIP"] < times["QUIESCE_REQUEST"] < times["PHY_ERROR"], "inline transition order")
    images = [json.loads(s.removeprefix("INLINE_IMAGE ")) for s in text.splitlines() if s.startswith("INLINE_IMAGE ")]
    require(
        [x["phase"] for x in images] == [*(["quiesced"] * 4), "torn", "held_success", "recovered"],
        "inline image phases",
    )
    events, recovery = [list(source_order(p.read_text().splitlines())) for p in (trace, recovered_trace)]
    for items in (events, recovery):
        require(
            items[0] == {"kind": "header", "version": 1, "ports": 2, "channels": 2, "bus_bytes": 16}
            and items[-1]["kind"] == "end",
            "inline observation bounds",
        )
    writes, primary = source(events, images[:-1])
    fresh_writes, fresh = source(recovery, images[-1:], recovered=True)
    accesses = physical(events, profile, flip_ps=times["FLIP"], fault_ps=times["PHY_ERROR"], images=images[:-1])
    fresh_accesses = physical(recovery, profile, images=images[-1:])
    ordered_effects(events, accesses, writes)
    ordered_effects(recovery, fresh_accesses, fresh_writes)
    # The scrub data is physically complete before the quiesce request. Its
    # check-line write remains outstanding at that request. The younger write
    # cannot overtake that check commit, even though its W has been accepted.
    scrub = [a for a in accesses if a["channel"] == 0 and a["write"] and a["address"] == 0 and a["ps"] > times["FLIP"]]
    require(len(scrub) == 2 and scrub[0]["tail_ps"] < times["QUIESCE_REQUEST"], "scrub physical data prerequisite")
    check_writes = [
        a
        for a in accesses
        if a["channel"] == 0 and a["write"] and a["address"] == DATA_BYTES and a["ps"] > times["FLIP"]
    ]
    require(
        len(check_writes) == 2 and times["QUIESCE_REQUEST"] < check_writes[0]["tail_ps"] < scrub[1]["ps"],
        "scrub check lock/younger write ordering",
    )
    dirty = [a for a in accesses if a["channel"] == 0 and a["write"] and a["address"] == 256]
    require(len(dirty) == 1 and dirty[0]["tail_ps"] < times["PHY_ERROR"], "PHY error after data physical completion")
    require(
        not any(a["channel"] == 0 and a["write"] and a["ps"] > dirty[0]["ps"] for a in accesses),
        "PHY error before check physical update",
    )
    offered = [e for e in events if e["kind"] == "b" and e["id"] == 108]
    require(
        any(e["ps"] < times["PHY_ERROR"] and not e["ready"] for e in offered)
        and any(e["ps"] > times["PHY_ERROR"] and not e["ready"] for e in offered),
        "exposed B held across actual fault",
    )
    rejected = []
    for name in ("read_data", "held_status", "check_neighbor", "missing_response"):
        mutant, snapshots = copy.deepcopy(events), copy.deepcopy(images[:-1])
        if name == "check_neighbor":
            snapshots[0]["check"] = hex(number(snapshots[0]["check"]) ^ (1 << 63))
        elif name == "missing_response":
            mutant = [e for e in mutant if not (e["kind"] == "b" and e["id"] == 110)]
        else:
            e = next(
                e
                for e in mutant
                if e["kind"] == ("r" if name == "read_data" else "b")
                and e["id"] == (100 if name == "read_data" else 108)
            )
            field = "data" if name == "read_data" else "response"
            e[field] = number(e[field]) ^ 1
        try:
            source(mutant, snapshots)
        except Violation:
            rejected.append(name)
    require(len(rejected) == 4, "inline lifecycle oracle sensitivity")
    return {
        "source": primary,
        "recovered_source": fresh,
        "images": len(images),
        "shared_check_line_granules": 4,
        "clean_prefix_accesses": len(accesses),
        "recovered_accesses": len(fresh_accesses),
        "transition_ps": times,
        "rejected_mutations": rejected,
        "limits": [
            "Aligned AXI128 unique-identity workload; complete ECC matrix uses owning and other combined tests.",
            "Clean physical protocol replay stops at actual PHY error; post-error settlement is source/image checked "
            "and fresh boot has a separate complete replay.",
        ],
    }
