"""Independent physical-image and raw-link checks for connected ECC smoke.

Inline snapshots come from the external CA-driven memory fixture. Source AXI
payloads, rather than a DUT ECC function or completion, supply expected bytes.
The link checker consumes actual delayed DFI data/masks/parity and the admitted
JEDEC matrix. These focused checks complement the fixture's fault/settlement
assertions; they do not qualify the complete ECC/reset matrix.
"""

import copy
import json
from collections import deque
from pathlib import Path

from contracts.integrity import WireBeat, check_transport
from system.ecc import InlineWitness
from system.host import Violation, require

IP = Path(__file__).resolve().parents[2]


def number(value):
    return int(value, 0) if isinstance(value, str) else value


def source_writes(events, bus_bytes):
    pending, writes = deque(), {}
    for e in events:
        if e["kind"] == "aw":
            require(
                e["id"] not in writes
                and number(e["address"]) == 0
                and (e["length"] + 1) * bus_bytes == 64
                and (1 << e["size"]) == bus_bytes
                and e["burst"] == 1
                and not e["lock"],
                "focused ECC write identity/address",
            )
            pending.append(e["id"])
            writes[e["id"]] = {
                "data": bytearray(),
                "mask": [],
                "beats": e["length"] + 1,
            }
        elif e["kind"] == "w":
            require(bool(pending), "ECC W without AW")
            write = writes[pending[0]]
            write["data"].extend(number(e["data"]).to_bytes(bus_bytes, "little"))
            write["mask"].extend(bool(number(e["strobe"]) >> i & 1) for i in range(bus_bytes))
            count = len(write["data"]) // bus_bytes
            require(bool(e["last"]) == (count == write["beats"]), "ECC W count/last")
            if e["last"]:
                pending.popleft()
    require(
        not pending and all(len(w["data"]) == 64 for w in writes.values()),
        "ECC write tail/size",
    )
    return writes


def responses(events, bus_bytes, reads, writes):
    indices = {identity: 0 for identity in reads}
    admitted_reads, seen_writes, held = set(), set(), {}
    for e in events:
        if e["kind"] == "ar":
            require(
                e["id"] in reads
                and e["id"] not in admitted_reads
                and number(e["address"]) == 0
                and (e["length"] + 1) * bus_bytes == 64
                and (1 << e["size"]) == bus_bytes
                and e["burst"] == 1
                and not e["lock"],
                "ECC AR identity/descriptor",
            )
            admitted_reads.add(e["id"])
        if e["kind"] not in ("r", "b"):
            continue
        identity = e["id"]
        if e["kind"] == "b":
            require(
                identity in writes and identity not in seen_writes,
                "ECC B identity/duplicate",
            )
            require(e["response"] == writes[identity], "ECC B response")
        else:
            require(identity in admitted_reads, "ECC R without accepted AR")
            payload, response = reads[identity]
            at = indices[identity]
            require(at < 64 and e["response"] == response, "ECC R count/response")
            expected = int.from_bytes(payload[at : at + bus_bytes], "little")
            require(
                number(e["data"]) == expected and bool(e["last"]) == (at + bus_bytes == 64),
                "ECC R data/last",
            )
        key = e["kind"], identity
        value = e["response"], e.get("data"), e.get("last")
        require(key not in held or held[key] == value, "ECC stalled response changed")
        if e["ready"]:
            held.pop(key, None)
            if e["kind"] == "b":
                seen_writes.add(identity)
            else:
                indices[identity] += bus_bytes
        else:
            held[key] = value
    require(
        not held
        and admitted_reads == reads.keys()
        and seen_writes == writes.keys()
        and all(i == 64 for i in indices.values()),
        "ECC response tail",
    )


def response_sensitivity(events, bus_bytes, reads, writes):
    """Prove the response oracle rejects active data, status, identity and loss faults."""
    responses(events, bus_bytes, reads, writes)
    first_r = next(i for i, event in enumerate(events) if event["kind"] == "r")
    first_b = next(i for i, event in enumerate(events) if event["kind"] == "b")
    last_r = max(i for i, event in enumerate(events) if event["kind"] == "r" and event["ready"])
    rejected = []
    for name in ("read_data", "write_status", "response_identity", "missing_read_beat"):
        mutant = list(events)
        if name == "missing_read_beat":
            del mutant[last_r]
        else:
            at, field, mask = {
                "read_data": (first_r, "data", 1),
                "write_status": (first_b, "response", 2),
                "response_identity": (first_r, "id", 512),
            }[name]
            mutant[at] = {**mutant[at], field: number(mutant[at][field]) ^ mask}
        try:
            responses(mutant, bus_bytes, reads, writes)
        except Violation:
            rejected.append(name)
    require(len(rejected) == 4, "ECC response oracle sensitivity")
    return rejected


def inline_images(images, initial, rewritten, partial):
    require(
        [e["phase"] for e in images]
        == [
            "initial",
            "single_fault",
            "scrubbed",
            "double_fault",
            "rejected_partial",
            "rewritten",
            "partial_rmw",
        ],
        "inline image phases",
    )
    single, double = bytearray(initial), bytearray(initial)
    single[2] ^= 128
    double[3] ^= 192
    expected = [initial, single, initial, double, double, rewritten, partial]
    for e, data in zip(images, expected, strict=True):
        require(
            number(e["data"]).to_bytes(64, "little") == data,
            "external inline data/scrub/RMW",
        )
        protected = initial if e["phase"] in ("single_fault", "double_fault", "rejected_partial") else data
        checks = bytes(
            InlineWitness.check_byte(int.from_bytes(protected[i : i + 8], "little")) for i in range(0, 64, 8)
        )
        require(
            number(e["check"]).to_bytes(8, "little") == checks,
            "external inline check bytes",
        )


def link_blocks(events, profile, writes):
    phy = {e["ck"]: e for e in events if e["kind"] == "phy"}
    words = []
    ratio = profile["ratio"]
    for ck, e in phy.items():
        enable = number(e["write"]) >> 24 & 255
        for lane in range(ratio):
            if not (enable >> (2 * lane) & 3):
                continue
            require(enable >> (2 * lane) & 3 == 3, "link byte enables")
            at = ck * ratio + lane + profile["dfi_wck"]["tphy_wrdata"]
            require(at // ratio in phy, "missing delayed link data")
            raw, slot = number(phy[at // ratio]["write"]), at % ratio
            words.append(
                (
                    raw >> (48 + slot * 32) & 0xFFFFFFFF,
                    raw >> (32 + slot * 4) & 15,
                    raw >> (slot * 4) & 15,
                )
            )
    require(len(words) == 32, "two complete physical BL32 link writes")
    rows = json.loads((IP / "config/reference/link_ecc_matrix.json").read_text())
    all_wire, decoded = [], bytearray()
    for block in range(4):
        wire = []
        for w, (data, mask, parity) in enumerate(words[block * 8 : (block + 1) * 8]):
            for beat in range(2):
                for byte in range(2):
                    shift = beat * 2 + byte
                    wire.append(
                        WireBeat(
                            block,
                            w * 2 + beat,
                            byte,
                            data >> (8 * shift) & 255,
                            mask >> shift & 1,
                            parity >> shift & 1,
                        )
                    )
        payload, statuses = check_transport(rows, wire, write=True)
        require(
            all(s == "clean" for s in statuses),
            "outgoing link parity disagrees with JEDEC matrix",
        )
        decoded.extend(payload)
        all_wire.append(wire)
    require(
        decoded == writes[50]["data"] + writes[51]["data"],
        "physical link data disagrees with accepted AXI",
    )
    # Correctable parity mutations must still be reported, not silently clean.
    mutated = copy.copy(all_wire[0])
    beat = mutated[14]
    mutated[14] = WireBeat(beat.tag, beat.beat, beat.byte, beat.dq, beat.dmi, beat.parity ^ 1)
    _, statuses = check_transport(rows, mutated, write=True)
    require("corrected" in statuses, "link parity sensitivity")
    return {
        "physical_words": len(words),
        "jedec_blocks": 4,
        "parity_mutation_reported": True,
    }


def check(trace: Path, log: Path, profile: dict, mode: int) -> dict:
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    require(
        events[0]["kind"] == "header" and events[-1]["kind"] == "end",
        "ECC raw trace bounds",
    )
    require(
        events[0]["ports"] == events[0]["channels"] == 1,
        "focused ECC observer configuration",
    )
    bus_bytes = events[0]["bus_bytes"]
    writes = source_writes(events, bus_bytes)
    if mode == 1:
        require(writes.keys() == {40, 43, 44, 46}, "inline source writes")
        initial, rewritten = writes[40]["data"], writes[44]["data"]
        partial = bytearray(rewritten)
        for i, enabled in enumerate(writes[46]["mask"]):
            if enabled:
                partial[i] = writes[46]["data"][i]
        images = [
            json.loads(line.removeprefix("INLINE_SNAPSHOT "))
            for line in log.read_text().splitlines()
            if line.startswith("INLINE_SNAPSHOT ")
        ]
        inline_images(images, initial, rewritten, partial)
        negatives = 0
        for field in ("data", "check"):
            mutant = copy.deepcopy(images)
            mutant[2][field] = hex(number(mutant[2][field]) ^ 1)
            try:
                inline_images(mutant, initial, rewritten, partial)
            except Violation:
                negatives += 1
        require(negatives == 2, "inline data/check oracle sensitivity")
        response_mutations = response_sensitivity(
            events,
            bus_bytes,
            {
                41: (initial, 0),
                42: (bytes(64), 2),
                45: (rewritten, 0),
                47: (partial, 0),
            },
            {40: 0, 43: 2, 44: 0, 46: 0},
        )
        return {
            "physical_images": len(images),
            "protected_words_per_image": 8,
            "rejected_mutations": negatives,
            "rejected_response_mutations": response_mutations,
            "accepted_write_bytes": 256,
            "successful_write_bytes": 192,
            "returned_read_bytes": 256,
            "successful_read_bytes": 192,
            "limits": [
                "Physical data/check snapshots and actual AXI responses; "
                "full ECC pin timing and reset matrix remain pending."
            ],
        }
    require(mode == 2 and writes.keys() == {50, 51}, "write-link source identities")
    response_mutations = response_sensitivity(events, bus_bytes, {52: (writes[51]["data"], 0)}, {50: 2, 51: 0})
    result = link_blocks(events, profile, writes)
    result["rejected_response_mutations"] = response_mutations
    result["limits"] = [
        "MR43 device-error injection qualifies composed settlement; raw outgoing parity is independently checked. "
        "Device parity-bit error capture and read-link correction remain separate."
    ]
    return result
