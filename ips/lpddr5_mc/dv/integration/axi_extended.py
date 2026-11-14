"""Measure the directed extended AXI selection after independent pin replay.

This ledger establishes offered stimulus, response counts and actual B/R stalls.
The owning connected.check call remains mandatory for all physical effects,
complete return/pin tails, write validation and unchanged-memory claims. No DUT
completion or internal memory witness is accepted here as physical evidence.
"""

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from system.host import Address, Violation, byte_ranges, require
from system.trace import number, source_order


@dataclass(frozen=True)
class Case:
    name: str
    write: bool
    port: int
    address: int
    beats: int
    error: int = 0
    salt: int = 0
    lock: int = 0
    malformed: int = 0


CASES = (
    Case("max-write", True, 0, 0x20000, 256, salt=0x11),
    Case("max-read", False, 1, 0x20000, 256),
    Case("early-last", True, 0, 0x20000, 256, 2, 0xA5, malformed=1),
    Case("early-preserve", False, 1, 0x20000, 256),
    Case("missing-last", True, 1, 0x20000, 256, 2, 0x5A, malformed=2),
    Case("missing-preserve", False, 0, 0x20000, 256),
    Case("cross-write", True, 0, 0x20040, 256, 3, 0xFF),
    Case("cross-read", False, 1, 0x20040, 256, 3),
    Case("cross-preserve", False, 1, 0x20000, 256),
    Case("exclusive-write", True, 1, 0x24000, 4, salt=0x22, lock=1),
    Case("exclusive-read", False, 0, 0x24000, 4, lock=1),
    Case("intervening-write", True, 0, 0x24000, 4, salt=0x33),
    Case("exclusive-rewrite", True, 1, 0x24000, 4, salt=0x44, lock=1),
    Case("exclusive-after", False, 0, 0x24000, 4, lock=1),
    Case("ordinary-after", False, 1, 0x24000, 4),
    Case("final-prefix", False, 0, 0x20000, 256),
)
MARKER = "DATAPATH_AXI_EXTENDED_PASS cases=16 max_beats=256"


def payload_byte(at: int, salt: int) -> int:
    return ((at * 29) ^ (at >> 8) ^ (at >> 16) ^ salt) & 255


def word(at: int, salt: int) -> int:
    return sum(payload_byte(address, salt) << (lane * 8) for lane, address in enumerate(range(at, at + 16)))


def measure_events(events, log: str) -> dict:
    require(re.findall(r"DATAPATH_AXI_EXTENDED_PASS[^\n]*", log) == [MARKER], "extended selection marker")
    events = iter(events)
    header = next(events)
    require(
        header == {"kind": "header", "version": 1, "ports": 2, "channels": 2, "bus_bytes": 16},
        "extended source header/binding",
    )
    active = None
    completed = []
    memory = {}
    counts = Counter()
    last_ps = -1
    ended = False
    for event in events:
        kind, ps = event["kind"], event["ps"]
        require(not ended and type(ps) is int and ps >= last_ps, "extended time/end tail")
        last_ps = ps
        if kind == "phy":
            continue
        if kind in ("aw", "ar"):
            require(active is None and len(completed) < len(CASES), "extended unexpected admission/overlap")
            case = CASES[len(completed)]
            identity = len(completed) + 80
            descriptor = (
                kind == "aw",
                event["port"],
                event["id"],
                number(event["address"]),
                event["length"] + 1,
                event["size"],
                event["burst"],
                event["lock"],
            )
            require(
                descriptor == (case.write, case.port, identity, case.address, case.beats, 4, 1, case.lock),
                "extended intended descriptor/lock",
            )
            try:
                byte_ranges(Address(case.port, identity, case.address, case.beats - 1, 4, 1, bool(case.lock)), 16)
                error = 0
            except Violation:
                error = 3
            active = dict(
                case=case,
                id=identity,
                w=0,
                r=0,
                admission_ps=ps,
                error=error,
                writes={},
                held=None,
                b_stalls=0,
                r_stalls=Counter(),
            )
            counts[kind] += 1
        elif kind == "w":
            require(active is not None and active["case"].write, "extended W without write")
            case, beat = active["case"], active["w"]
            require(event["port"] == case.port and beat < case.beats, "extended W port/count")
            require(ps > active["admission_ps"], "extended W before accepted AW")
            wanted_last = beat == case.beats - 1
            if case.malformed == 1:
                wanted_last = beat in (0, case.beats - 1)
            elif case.malformed == 2:
                wanted_last = False
            require(bool(event["last"]) == wanted_last, "extended intended WLAST stimulus")
            data = number(event["data"])
            require(
                number(event["strobe"]) == 65535 and data == word(case.address + beat * 16, case.salt),
                "extended intended W bytes/strobes",
            )
            if bool(event["last"]) != (beat == case.beats - 1) and active["error"] != 3:
                active["error"] = 2
            for lane in range(16):
                active["writes"][case.address + beat * 16 + lane] = (data >> (lane * 8)) & 255
            active["w"] += 1
            counts["w_beats"] += 1
        elif kind in ("b", "r"):
            require(active is not None and active["case"].write == (kind == "b"), "extended unexpected response")
            case = active["case"]
            payload = (event["id"], number(event.get("data", 0)), event["response"], bool(event.get("last", True)))
            require(active["held"] is None or active["held"] == payload, "extended offered payload changed")
            require(event["port"] == case.port and event["id"] == active["id"], "extended response identity")
            require(event["response"] == case.error, "extended response code/EXOKAY")
            if case.write:
                require(active["w"] == case.beats, "extended B before complete accepted W")
                require(active["error"] == case.error, "extended W validation outcome")
            else:
                beat = active["r"]
                require(beat < case.beats and bool(event["last"]) == (beat == case.beats - 1), "extended R count/last")
                wanted = (
                    0
                    if case.error
                    else sum(memory.get(case.address + beat * 16 + lane, 0) << (8 * lane) for lane in range(16))
                )
                require(number(event["data"]) == wanted, "extended preserved/ordinary read bytes")
            if not event["ready"]:
                active["held"] = payload
                if case.write:
                    active["b_stalls"] += 1
                else:
                    active["r_stalls"][active["r"]] += 1
                continue
            active["held"] = None
            counts[kind + "_handshakes"] += 1
            if not case.write:
                active["r"] += 1
            if case.write or active["r"] == case.beats:
                if case.write:
                    require(active["b_stalls"] >= 4, "extended missing actual B stall")
                    if not case.error:
                        memory.update(active["writes"])
                else:
                    required_beats = {0, case.beats - 1} | set(range(64, case.beats, 64))
                    require(
                        all(active["r_stalls"][beat] >= 4 for beat in required_beats),
                        "extended missing actual first/interior/final R stall",
                    )
                completed.append(
                    dict(
                        name=case.name,
                        write=case.write,
                        port=case.port,
                        id=active["id"],
                        beats=case.beats,
                        lock=case.lock,
                        response=case.error,
                        b_stall_samples=active["b_stalls"],
                        r_stall_samples=dict(active["r_stalls"]),
                    )
                )
                active = None
        elif kind == "end":
            require(active is None and len(completed) == len(CASES), "extended unsettled/absent selection tail")
            ended = True
        else:
            raise Violation("unexpected extended observation kind")
    require(ended, "extended truncated trace")
    require(
        counts == {"aw": 7, "ar": 9, "w_beats": 1036, "b_handshakes": 7, "r_handshakes": 1548},
        "extended incomplete transaction/beat totals",
    )
    return {
        "cases": completed,
        "counts": dict(counts),
        "limits": [
            "Source stimulus/count/stall ledger only; connected independent CA/data/return replay remains mandatory.",
            "Fixed plain P2/C2/R1 AXI128/depth16/profile0/CK2/64-byte-granule binding; no new ECC or lifecycle claim.",
        ],
    }


def measure(path: Path, log: Path) -> dict:
    with path.open() as stream:
        return measure_events(source_order(stream), log.read_text())
