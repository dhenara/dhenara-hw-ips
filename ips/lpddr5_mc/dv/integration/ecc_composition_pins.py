"""Replay the one-channel composition's actual CA, WCK, write and return pins.

Internal request/result witnesses are deliberately absent from this adapter.
External-memory fault markers change only the independent DRAM byte image.
"""

import copy
from collections import deque
from fractions import Fraction

from contracts.read_completion import CompletionOracle, ExpectedWord, ReadBusOracle
from system.adapters import returned_snapshot
from system.dram import decode
from system.host import require
from system.phy import DigitalPhy
from system.return_timing import ReturnTiming
from system.trace import number
from wck_observations import WckObservations


def write_tail(profile, command_ck, last_pin_half_wck, beats):
    """Latest independent pin, DFI data-CS and output-flush endpoint in CK.

    DFI 5.0 3.2/Table12/4.9.3 measures twrdata_delay from DFI enable.
    The admitted conservative data-end-plus-delay flush is separate from
    the physical pin path; adding that delay to the last pin double counts
    serialization. Keep all endpoints exclusive and preserve half-WCK time.
    """
    ratio, d = profile["ratio"], profile["dfi_wck"]
    span = beats // 2 + (8 if ratio == 4 and beats == 32 else 0)
    cs_end = Fraction(command_ck * ratio + d["tphy_wrcslat"] + span, ratio)
    flush = Fraction(command_ck * ratio + d["tphy_wrlat"] + d["tphy_wrdata"] + span + d["twrdata_delay"], ratio)
    return max(Fraction(last_pin_half_wck + 1, 2 * ratio), cs_end, flush)


def replay(events, profile, flips):
    p = copy.deepcopy(profile)
    p["ranks"] = 1
    model, wck, timing = DigitalPhy(copy.deepcopy(p)), WckObservations(p), ReturnTiming(p)
    completion = CompletionOracle([], 16, p["dfi_wck"]["tphy_rdlat"])
    bus = ReadBusOracle(p["ratio"], completion)
    ratio, origin, serial, last_ck = p["ratio"], None, 0, -1
    pending_flips = deque(sorted(flips, key=lambda e: e["ps"]))
    accesses, bursts, controls, expected, returned = [], [], [], {}, {}
    for e in events:
        if e["kind"] != "phy":
            continue
        ck, ps = e["ck"], e["ps"]
        require(e["channel"] == 0 and ck == last_ck + 1, "composition pin sample coverage")
        last_ck = ck
        origin = ps - ck * p["ck_ps"] if origin is None else origin
        require(ps == origin + ck * p["ck_ps"], "composition pin sample clock")
        while pending_flips and pending_flips[0]["ps"] < ps:
            f = pending_flips.popleft()
            a = f["address"]
            key = (a >> 30, (a >> 11) & 15, (a >> 15) & 32767, a & 2047)
            model.dram.memory[key] = model.dram.memory.get(key, 0) ^ f["xor"]
        wck.sample(ck, e["cs"], e["ca"], number(e["wck"]))
        burst = model.command(ck, e["cs"], e["ca"])
        op = decode(e["ca"]) if e["cs"] else None
        if burst is not None:
            require(burst.beats == 32 and not burst.masked, "composition full physical granule")
            serial += 1
            command_at = burst.command_ck - p["management_ck"]["tctrl_delay"]
            address = (burst.rank << 30) | (burst.row << 15) | (burst.bank << 11) | (burst.column << 5)
            access = {"write": bool(burst.write), "address": address, "ps": ps, "words": 0, "data": bytearray(64)}
            access["tail_ps"] = origin + burst.times[-1] * p["ck_ps"] / (2 * ratio)
            if burst.write:
                access["tail_ps"] = origin + write_tail(p, command_at, burst.times[-1], burst.beats) * p["ck_ps"]
            accesses.append(access)
            bursts.append((burst, command_at, access))
            controls.append((command_at, bool(burst.write), burst.beats, burst.rank))
            if not burst.write:
                values = [model.dram.beat(burst, i, burst.times[i]) for i in range(32)]
                access["data"][:] = b"".join(value.to_bytes(2, "little") for value in values)
                returned[serial] = access
                for word in range(16):
                    expected[serial, word] = values[2 * word] | values[2 * word + 1] << 16
                    for lane in range(2):
                        enable = command_at * ratio + p["dfi_wck"]["trddata_en"] + word + (8 if word >= 8 else 0)
                        completion.pending[lane].append(ExpectedWord(enable, serial, word, lane))
                completion.received[serial] = {}
        if op is not None and op.kind == "MRR":
            require(e["cs"] == 1, "composition MRR rank")
            controls.append((ck, False, 16, 0))
            serial += 1
            for word in range(8):
                byte = 9 if op.operand == 4 else 0
                expected[serial, word] = 0xDA00DA00 | byte | byte << 16 if word < 4 else 0xDADADADA
                for lane in range(2):
                    enable = ck * ratio + p["dfi_wck"]["trddata_en"] + word
                    completion.pending[lane].append(ExpectedWord(enable, serial, word, lane))
            completion.received[serial] = {}
        for lane in range(2):
            completion.pending[lane] = deque(sorted(completion.pending[lane], key=lambda item: item.enable))
        raw, enabled = number(e["write"]), 0
        require(0 <= raw < 1 << 176, "composition write bus width")
        write_cs, read_cs, read_enable = 0, 0, 0
        for command_at, write, beats, rank in controls:
            words = beats // 2
            split = 8 if ratio == 4 and beats == 32 else 0
            cs_at = command_at * ratio + p["dfi_wck"]["tphy_wrcslat" if write else "tphy_rdcslat"]
            for phase in range(ratio):
                at = ck * ratio + phase
                if cs_at <= at < cs_at + words + split:
                    if write:
                        write_cs |= (1 << rank) << (phase * 2)
                    else:
                        read_cs |= ((1 << rank) * 5) << (phase * 4)
                if not write:
                    for word in range(words):
                        enable_at = command_at * ratio + p["dfi_wck"]["trddata_en"] + word
                        enable_at += split if word >= 8 else 0
                        if at == enable_at:
                            read_enable |= 3 << (phase * 2)
        for b, command_at, access in bursts:
            if not b.write:
                continue
            for word in range(16):
                enable = command_at * ratio + p["dfi_wck"]["tphy_wrlat"] + word + (8 if word >= 8 else 0)
                if enable // ratio == ck:
                    enabled |= 3 << ((enable % ratio) * 2)
                data_at = enable + p["dfi_wck"]["tphy_wrdata"]
                if data_at // ratio == ck:
                    lane = data_at % ratio
                    mask = raw >> (32 + lane * 4) & 15
                    require(mask == 0, "composition physical byte mask")
                    require(raw >> (lane * 4) & 15 == 0, "composition unexpected write parity")
                    value = raw >> (48 + lane * 32) & 0xFFFFFFFF
                    model.write_word(b, word, data_at * 2, value, mask)
                    access["data"][word * 4 : word * 4 + 4] = value.to_bytes(4, "little")
                    access["words"] += 1
        require(raw >> 24 & 255 == enabled, "composition write enables/CA")
        require(raw >> 16 & 255 == write_cs, "composition write CS/CA")
        require(number(e["read"]) == (read_enable << 16 | read_cs), "composition read enable/CS/CA")
        raw = number(e["return"])
        timing.sample(ck, e["cs"], e["ca"], raw >> 32 & 255)
        decoded = returned_snapshot(
            ratio, data=raw >> 40, valid=raw >> 32 & 255, dnv=raw >> 16 & 65535, link_parity=raw & 65535
        )
        require(
            all(
                not parity
                for lane, valid in zip(decoded["parity"], decoded["data"], strict=True)
                for parity, value in zip(lane, valid, strict=True)
                if value is not None
            ),
            "composition unexpected return parity",
        )
        bus.snapshot(ck, decoded["data"], decoded["dnv"])
        require(not bus.unavailable, "composition unavailable return")
        for tag, received in completion.received.items():
            for (word, lane), value in received.items():
                wanted = expected[tag, word]
                require(
                    value == ((wanted >> (lane * 8) & 255) | (wanted >> (16 + lane * 8) & 255) << 8),
                    "composition physical returned data",
                )
            if len(received) == 32 and tag in returned:
                returned[tag].setdefault("return_tail_ps", ps)
    require(last_ck >= 0 and not pending_flips, "composition missing pins/injection")
    require(all(a["words"] == 16 for a in accesses if a["write"]), "composition physical write tail")
    require(not any(completion.pending.values()), "composition physical return tail")
    for owner in (model, timing):
        owner.finish()
    require(not wck.finish()["active_at_trace_end"], "composition WCK not disabled at final settlement")
    return accesses
