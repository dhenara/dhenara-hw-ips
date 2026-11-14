"""Check raw source AXI and external DFI observations against independent models.

The first binding covers identity-mapped, non-ECC/non-DBI traffic. Physical
completion is derived from decoded CA, observed data and declared PHY delays.
No DUT completion/queue-empty signal is an input to this checker.
"""

import copy
from collections import deque
from itertools import islice
from pathlib import Path

from contracts.read_completion import CompletionOracle, ExpectedWord, ReadBusOracle
from physical_ownership import WriteOwnership
from read_ownership import ReadOwnership
from system.adapters import returned_snapshot
from system.dram import decode
from system.host import Address, HostScoreboard, require
from system.phy import DigitalPhy
from system.return_timing import ReturnTiming
from system.trace import source_order
from thermal import ThermalReturns
from wck_observations import WckObservations


def number(value):
    return int(value, 0) if isinstance(value, str) else value


def check(
    path: Path,
    profile: dict,
    *,
    ranks: int,
    depth: int,
    mapped_end: int = 1 << 20,
    mr4_change_ck: int | None = None,
    id_bits: int = 16,
    address_bits: int = 40,
    granule_bytes: int = 64,
    fault_ps: int | None = None,
    observe_axi: bool = False,
) -> dict:
    profile = copy.deepcopy(profile)
    profile["ranks"] = ranks
    require(
        not any(profile[k] for k in ("read_link", "write_link", "read_dbi", "write_dbi")),
        "plain-data checker profile",
    )
    with path.open() as stream:
        events = list(source_order(stream))
    header = events.pop(0)
    require(header["kind"] == "header" and header["version"] == 1, "raw observation header")
    ports, channels = header["ports"], header["channels"]
    # The test's programmed identity region, including channel/rank striping,
    # owns admission. The serial oracle's one-GiB default excludes rank one.
    require(0 < mapped_end <= (1 << 30) * channels * ranks, "identity region capacity")
    host = HostScoreboard(
        header["bus_bytes"], ports, 2 * ports * depth, mapped_end=mapped_end, id_bits=id_bits, address_bits=address_bits
    )
    read_ownership = ReadOwnership(host, granule_bytes)
    write_ownership = WriteOwnership(host, granule_bytes)
    phy = [DigitalPhy(profile) for _ in range(channels)]
    wck = [WckObservations(profile) for _ in range(channels)]
    thermal = [ThermalReturns(p.dram.maintenance) for p in phy]
    return_timing = [ReturnTiming(profile) for _ in range(channels)]
    completion = [CompletionOracle([], 16, profile["dfi_wck"]["tphy_rdlat"]) for _ in range(channels)]
    bus = [ReadBusOracle(profile["ratio"], c) for c in completion]
    bursts = [[] for _ in range(channels)]
    expected_words = [{} for _ in range(channels)]
    word_owner = [{} for _ in range(channels)]
    mrr_windows = [[] for _ in range(channels)]
    source_queues = {(p, w): deque() for p in range(ports) for w in (False, True)}
    physical_pending = set()
    ratio = profile["ratio"]
    origin_ps = None
    ended = False
    commands = writes = reads = 0
    physical_payload_bytes = [0, 0]
    observed_fault = None
    serial = 0

    def logical(channel, physical):
        return physical if channels == 1 else (physical & 63) | (channel << 6) | ((physical & ~63) << 1)

    def finish_physical(tag, ps):
        t = host.transactions[tag]
        if t.error or t.physical or host.faulted:
            return
        if t.write:
            write_ownership.complete_before(tag, ps)
        else:
            read_ownership.complete_before(tag, ps)
        host.physical_done(tag)
        host.integrity_done(tag)
        physical_pending.discard(tag)

    # Same-instant physical observations precede response eligibility checks.
    # Actual AW/AR ties retain the independent source-order rule.
    events.sort(
        key=lambda e: (
            e["ps"],
            0 if e["kind"] in ("aw", "ar") else 1 if e["kind"] == "phy" else 2,
        )
    )
    for e in events:
        require(not ended, "raw event after end")
        kind, ps = e["kind"], e["ps"]
        # Physical/integrity retirement follows independent pin/return
        # witnesses, even while an older source B/R remains backpressured.
        # Capture read snapshots before any younger overlapping write effect.
        for tag in sorted(physical_pending):
            t = host.transactions[tag]
            ownership = write_ownership if t.write else read_ownership
            if not (t.physical or t.error or host.faulted) and ownership.ready(tag, ps):
                finish_physical(tag, ps)
        if kind == "fault":
            require(fault_ps is not None and observed_fault is None and ps == fault_ps, "unexpected fault witness")
            observed_fault = ps
            host.fail()
            physical_pending.clear()
        elif kind in ("aw", "ar"):
            a = Address(
                **{
                    k: number(e[k])
                    for k in (
                        "port",
                        "id",
                        "address",
                        "length",
                        "size",
                        "burst",
                        "lock",
                    )
                }
            )
            tag = host.admit(a, kind == "aw")
            if not host.transactions[tag].error:
                physical_pending.add(tag)
            source_queues[e["port"], kind == "aw"].append(tag)
        elif kind == "w":
            tag = host.w(e["port"], number(e["data"]), number(e["strobe"]), bool(e["last"]))
            if host.transactions[tag].error:
                physical_pending.discard(tag)
        elif kind in ("b", "r"):
            q = source_queues[e["port"], kind == "b"]
            require(bool(q), "response without accepted source address")
            tag = q[0]
            require(
                host.transactions[tag].address.id == e["id"],
                "actual source response ID/order",
            )
            finish_physical(tag, ps)
            host.offer(tag, number(e.get("data", 0)), e["response"], bool(e.get("last", True)))
            if e["ready"]:
                host.handshake(tag)
                if host.transactions[tag].done:
                    q.popleft()
        elif kind == "phy":
            c, ck = e["channel"], e["ck"]
            if origin_ps is None:
                origin_ps = ps - ck * profile["ck_ps"]
            require(
                ps == origin_ps + ck * profile["ck_ps"],
                "incoherent CK observation clock",
            )
            model = phy[c]
            wck[c].sample(ck, e["cs"], e["ca"], number(e["wck"]))
            b = model.command(ck, e["cs"], e["ca"])
            if e["cs"]:
                commands += 1
            operation = decode(e["ca"]) if e["cs"] else None
            if b is not None:
                serial += 1
                origin = b.command_ck - profile["management_ck"]["tctrl_delay"]
                address = (b.rank << 30) | (b.row << 15) | (b.bank << 11) | (b.column << 5)
                values = []
                if not b.write:
                    values = [model.dram.beat(b, beat, b.times[beat]) for beat in range(b.beats)]
                words = b.beats // 2
                span = words + (8 if ratio == 4 and words == 16 else 0)
                d = profile["dfi_wck"]
                enable_latency = d["tphy_wrlat" if b.write else "trddata_en"]
                # Exclusive DFI-word endpoint of every enable, continuous-CS
                # and write-data observation. Returns and physical ownership
                # keep their independent pending state after this window ends.
                end = (
                    origin * ratio
                    + span
                    + max(
                        d["tphy_wrcslat" if b.write else "tphy_rdcslat"],
                        enable_latency + (d["tphy_wrdata"] if b.write else 0),
                        enable_latency,
                    )
                )
                bursts[c].append((serial, b, origin, address, values, end))
                physical_payload_bytes[int(b.write)] += b.beats * 2
                if b.write:
                    final_ps = origin_ps + b.times[-1] / (2 * ratio) * profile["ck_ps"]
                    final_ps += profile["dfi_wck"]["twrdata_delay"] / ratio * profile["ck_ps"]
                    write_ownership.begin((c, serial), (logical(c, address + i) for i in range(b.beats * 2)), final_ps)
                if not b.write:
                    read_ownership.begin((c, serial), (logical(c, address + i) for i in range(b.beats * 2)))
                    for w in range(b.beats // 2):
                        expected_words[c][serial, w] = values[2 * w] | (values[2 * w + 1] << 16)
                        word_owner[c][serial, w] = logical(c, address + w * 4)
                        for lane in range(2):
                            enable = (
                                origin * ratio
                                + profile["dfi_wck"]["trddata_en"]
                                + w
                                + (8 if ratio == 4 and b.beats == 32 and w >= 8 else 0)
                            )
                            completion[c].pending[lane].append(ExpectedWord(enable, serial, w, lane))
                    completion[c].received[serial] = {}
            # MRR returns share the same physical word-rotation owner.
            if operation is not None and operation.kind == "MRR":
                serial += 1
                thermal[c].begin(serial, ck, e["cs"], operation.operand)
                mrr_end = ck * ratio + max(profile["dfi_wck"]["trddata_en"], profile["dfi_wck"]["tphy_rdcslat"]) + 8
                mrr_windows[c].append((ck, e["cs"], mrr_end))
                for w in range(8):
                    byte = (
                        (13 if mr4_change_ck is not None and ck >= mr4_change_ck else 9)
                        if operation.operand == 4
                        else 0
                    )
                    expected_words[c][serial, w] = (0xDA00DA00 | byte | (byte << 16)) if w < 4 else 0xDADADADA
                    for lane in range(2):
                        completion[c].pending[lane].append(
                            ExpectedWord(
                                ck * ratio + profile["dfi_wck"]["trddata_en"] + w,
                                serial,
                                w,
                                lane,
                            )
                        )
                completion[c].received[serial] = {}
            if (b is not None and not b.write) or (operation is not None and operation.kind == "MRR"):
                for lane in range(2):
                    completion[c].pending[lane] = deque(sorted(completion[c].pending[lane], key=lambda w: w.enable))
            raw_write, raw_read, raw_return = (number(e[k]) for k in ("write", "read", "return"))
            return_timing[c].sample(ck, e["cs"], e["ca"], (raw_return >> 32) & 255)
            write_data, write_mask = raw_write >> 48, (raw_write >> 32) & 65535
            write_enable, write_cs = (raw_write >> 24) & 255, (raw_write >> 16) & 255
            read_enable, read_cs = raw_read >> 16, raw_read & 65535
            expected_write = expected_read = expected_wcs = expected_rcs = 0
            for identity, burst, at, address, _values, _end in bursts[c]:
                words = burst.beats // 2
                d = profile["dfi_wck"]
                for lane in range(ratio):
                    word_at = ck * ratio + lane
                    cs_start = at * ratio + d["tphy_wrcslat" if burst.write else "tphy_rdcslat"]
                    if cs_start <= word_at < cs_start + words + (8 if ratio == 4 and words == 16 else 0):
                        if burst.write:
                            expected_wcs |= (1 << burst.rank) << (lane * 2)
                        else:
                            expected_rcs |= ((1 << burst.rank) * 5) << (lane * 4)
                    for w in range(words):
                        enable_at = (
                            at * ratio
                            + d["tphy_wrlat" if burst.write else "trddata_en"]
                            + w
                            + (8 if ratio == 4 and words == 16 and w >= 8 else 0)
                        )
                        if word_at == enable_at:
                            if burst.write:
                                expected_write |= 3 << (lane * 2)
                            else:
                                expected_read |= 3 << (lane * 2)
                        if burst.write and word_at == enable_at + d["tphy_wrdata"]:
                            value, mask = (
                                write_data >> (lane * 32) & 0xFFFFFFFF,
                                write_mask >> (lane * 4) & 15,
                            )
                            model.write_word(burst, w, 2 * word_at, value, mask)
                            for byte in range(4):
                                if mask >> byte & 1:
                                    continue
                                logical_byte = logical(c, address + w * 4 + byte)
                                write_ownership.pin((c, identity), logical_byte, value >> (byte * 8) & 255)
                            writes += 1
                # Read data is checked against independently decoded physical bytes.
            # MRR uses eight words with continuous CS, independently derived
            # from its actual CA command, including the returned-data tail.
            for at, rank_mask, _end in mrr_windows[c]:
                d = profile["dfi_wck"]
                for lane in range(ratio):
                    word_at = ck * ratio + lane
                    if at * ratio + d["trddata_en"] <= word_at < at * ratio + d["trddata_en"] + 8:
                        expected_read |= 3 << (lane * 2)
                    if at * ratio + d["tphy_rdcslat"] <= word_at < at * ratio + d["tphy_rdcslat"] + 8:
                        expected_rcs |= (rank_mask * 5) << (lane * 4)
            # This CK's final DFI lane has been checked. A late enable/CS
            # still mismatches zero after retirement; no result flag or fault
            # cancels a scheduled word. Keep aggregate payload counts apart.
            bursts[c] = [window for window in bursts[c] if window[-1] > (ck + 1) * ratio]
            mrr_windows[c] = [window for window in mrr_windows[c] if window[-1] > (ck + 1) * ratio]
            require(write_enable == expected_write, "actual write enables disagree with CA")
            require(write_cs == expected_wcs, "actual continuous write CS disagrees with CA")
            require(
                read_cs == expected_rcs,
                "actual continuous read CS disagrees with CA",
            )
            require(read_enable == expected_read, "actual read enables disagree with CA")
            decoded = returned_snapshot(
                ratio,
                data=raw_return >> 40,
                valid=(raw_return >> 32) & 255,
                dnv=(raw_return >> 16) & 65535,
                link_parity=raw_return & 65535,
            )
            incoming = []
            for lane in range(2):
                count = sum(word[lane] is not None for word in decoded["data"])
                incoming.extend(islice(completion[c].pending[lane], count))
            # Snapshot remains the whole-snapshot authority for rotation,
            # duplicates, unsolicited words, DNV and missing-word deadlines.
            # Do not consume or validate the prospective prefix independently.
            bus[c].snapshot(ck, decoded["data"], decoded["dnv"])
            require(not bus[c].unavailable, "unexpected DNV in clean combined trace")
            # Serial order preserves the old received-dictionary order across
            # commands; the stable sort keeps snapshot order within a command.
            for item in sorted(incoming, key=lambda item: item.tag):
                tag, w, lane = item.tag, item.word, item.slice_index
                value = completion[c].received[tag][w, lane]
                raw = expected_words[c][tag, w]
                expected = ((raw >> (lane * 8)) & 255) | (((raw >> (16 + lane * 8)) & 255) << 8)
                require(
                    value == expected,
                    "actual returned slice disagrees with physical memory",
                )
                thermal[c].receive(tag, w, lane, value, ck, ps)
                if (tag, w) in word_owner[c]:
                    read_ownership.observe((c, tag), (word_owner[c][tag, w] + lane + 2 * half for half in range(2)), ps)
                reads += 1
        elif kind == "axi":
            require(observe_axi and 0 <= e["port"] < ports and 0 <= e["flags"] < 1024, "unexpected AXI cycle witness")
        elif kind == "end":
            require(observed_fault == fault_ps, "missing selected fault witness")
            host.finish()
            read_ownership.finish()
            require(
                not any(q for q in source_queues.values()),
                "unsettled source trace tail",
            )
            require(
                not any(q for value in completion for q in value.pending.values()),
                "unsettled physical read tail",
            )
            for model in [*phy, *return_timing, *wck]:
                model.finish()
            for observer in thermal:
                observer.finish()
            ended = True
        else:
            raise ValueError("unknown raw observation kind")
    require(ended, "truncated raw observation trace")
    result = {
        "commands": commands,
        "physical_write_words": writes,
        "returned_slices": reads,
        "physical_return_timing": [t.finish() for t in return_timing],
        "temperature_returns_by_channel": [t.finish() for t in thermal],
        "wck_by_channel": [t.finish() for t in wck],
        "write_bytes": host.handshaken_bytes[True],
        "read_bytes": host.handshaken_bytes[False],
        "limitations": [
            "Plain identity-mapped data, configured admission errors and serialized digital WCK controls; "
            "ECC and pin behavior through destructive reset remain separate. Active WCK at trace end is "
            "reported explicitly; it does not demonstrate final disable or electrical timing."
        ],
    }
    if fault_ps is not None:
        result["fault_ps"] = observed_fault
        result["failed_requests_by_direction"] = [
            sum(t.write == bool(direction) and t.offered[1] == 2 for t in host.transactions.values())
            for direction in (0, 1)
        ]
        result["physical_payload_bytes_by_direction"] = physical_payload_bytes
    return result
