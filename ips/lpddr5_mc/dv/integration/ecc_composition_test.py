"""Synthetic adapter tests; these do not stand in for real SV/pin observations."""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

import check as runner
from contracts import commands
from ecc_composition import AXI_PS, DATA_BYTES, FULL, correlate, source_cycles
from ecc_composition_pins import replay, write_tail
from system.ecc import InlineWitness
from system.host import Violation
from system.phy import DigitalPhy
from wck_observations_test import fixture as wck_fixture


def fixture():
    source = [{"kind": "header", "version": 1, "ports": 1, "channels": 1, "bus_bytes": 16}]
    witnesses = [
        {"kind": "header", "version": 1, "ports": 1, "channels": 1, "bus_bytes": 16, "inline_ecc": 1, "posted_early": 0}
    ]
    accesses, sequence = [], 0
    for case, base in enumerate((160, 168)):
        offset, address, generation = case * 1000, 0x4000 + case * 0x4000, case + 1
        byte_maps, source_data = [], []
        for w, (mask, salt, times) in enumerate(
            ((65535, 21, (10, 11, 12, 13)), (0x5555, 83, (20, 21, 22, 23)), (255, 155, (30, 31, 32, 40)))
        ):
            source.append(
                {
                    "kind": "aw",
                    "ps": offset + 1 + w,
                    "port": 0,
                    "id": base + w,
                    "address": hex(address),
                    "length": 3,
                    "size": 4,
                    "burst": 1,
                    "lock": 0,
                }
            )
            data = bytes((i ^ salt) & 255 for i in range(64))
            source_data.append(data)
            byte_maps.append({i: data[i] for i in range(64) if mask >> (i % 16) & 1})
            for beat, ps in enumerate(times):
                source.append(
                    {
                        "kind": "w",
                        "ps": offset + ps,
                        "port": 0,
                        "data": hex(int.from_bytes(data[beat * 16 : beat * 16 + 16], "little")),
                        "strobe": hex(mask),
                        "last": int(beat == 3),
                    }
                )
        source.append(
            {
                "kind": "ar",
                "ps": offset + 33,
                "port": 0,
                "id": base + 3,
                "address": hex(address),
                "length": 3,
                "size": 4,
                "burst": 1,
                "lock": 0,
            }
        )
        for i in range(4):
            witnesses.append(
                {
                    "kind": "import",
                    "ps": offset + (14 + i if i < 3 else 34),
                    "id": base + i,
                    "write": int(i < 3),
                    "slot": i if i < 3 else 0,
                    "generation": generation,
                    "sequence": case * 4 + i,
                }
            )
        for ps in (35, 36):
            witnesses.append(
                dict(
                    kind="hazard",
                    ps=offset + ps,
                    read_id=base + 3,
                    writer_id=base + 2,
                    read_generation=generation,
                    writer_generation=generation,
                    read_buffered=1,
                    read_validated=1,
                    read_pending=0,
                    read_offered=0,
                    blocked=1,
                    forwardable=0,
                )
            )
        expected, merged = {}, {}
        for b in byte_maps:
            expected.update(b)
        for b in byte_maps[1:]:
            merged.update(b)
        final = bytes(expected[i] for i in range(64))
        check_line = bytearray(64)
        for word in range(8):
            check_line[word] = InlineWitness.check_byte(
                int.from_bytes(source_data[0][word * 8 : word * 8 + 8], "little")
            )
        for op_index, ids, offer_ps, take_ps, done_ps, physical_times in (
            (0, [base], 18, 19, 200, (30, 60, 100, 130)),
            (1, [base + 3], 70, 210, 400, (230, 270)),
            (2, [base + 1, base + 2], 220, 410, 600, (430, 460) if case else (430, 460, 500, 530)),
        ):
            write, failed = op_index != 1, case == 1 and op_index == 2
            tag = case * 3 + op_index
            owners = [
                {
                    "id": n,
                    "slot": n - base if write else 0,
                    "generation": generation,
                    "order": case * 4 + n - base,
                    "buffered": 1,
                    "validated": 1,
                }
                for n in ids
            ]
            selected = byte_maps[0] if op_index == 0 else merged
            request_data = bytes(selected.get(i, 0) for i in range(64)) if write else final
            request = {
                "kind": "request",
                "tag": tag,
                "generation": generation,
                "boot": 1,
                "config": 1,
                "write": int(write),
                "id": ids[0],
                "address": address,
                "mask": hex(sum(1 << i for i in selected) if write else FULL),
                "data": hex(int.from_bytes(request_data, "little")),
                "overlay_mask": hex(FULL if not write else 0),
                "overlay_data": hex(int.from_bytes(final, "little")) if not write else "0x0",
                "owners": owners,
            }
            for phase, ps in (("offer", offer_ps), ("take", take_ps)):
                contributors = (
                    []
                    if write
                    else [
                        {"id": base + i, "slot": i, "generation": generation}
                        for i in ((0, 1, 2) if phase == "offer" else (1, 2))
                    ]
                )
                witnesses.append(
                    copy.deepcopy(request) | {"phase": phase, "ps": offset + ps, "contributors": contributors}
                )
            data_in = bytearray(source_data[0] if op_index else bytes(64))
            if failed:
                data_in[11] ^= 192
            checks_in = bytes(check_line) if op_index else bytes(64)
            checks_out = bytearray(check_line)
            data_out = source_data[0] if op_index == 0 else final
            for word in range(8):
                checks_out[word] = InlineWitness.check_byte(int.from_bytes(data_out[word * 8 : word * 8 + 8], "little"))
            pin_data = [checks_in, bytes(data_in), data_out, bytes(checks_out)]
            for step, ps in enumerate(physical_times):
                sequence += 1
                physical_write = step >= 2
                physical_address = address if step in (1, 2) else DATA_BYTES + (address // 512) * 64
                witnesses.append(
                    {
                        "kind": "physical",
                        "ps": offset + ps,
                        "owner": tag,
                        "owner_generation": generation,
                        "tag": sequence,
                        "generation": 0,
                        "boot": 1,
                        "config": 1,
                        "write": int(physical_write),
                        "address": physical_address,
                        "mask": hex(FULL),
                        "data": hex(int.from_bytes(pin_data[step] if physical_write else bytes(64), "little")),
                    }
                )
                witnesses.append(
                    {
                        "kind": "result",
                        "ps": offset + ps + 15,
                        "tag": sequence,
                        "generation": 0,
                        "boot": 1,
                        "config": 1,
                        "write": int(physical_write),
                        "physical": 1,
                        "integrity": 1,
                        "failed": 0,
                        "data": hex(int.from_bytes(pin_data[step], "little")),
                    }
                )
                accesses.append(
                    {
                        "write": physical_write,
                        "address": physical_address,
                        "ps": offset + ps + 5,
                        "tail_ps": offset + ps + 10,
                        "return_tail_ps": offset + ps + 10,
                        "data": pin_data[step],
                    }
                )
            logical_data = bytes(64) if failed else source_data[0] if op_index == 1 else data_out
            witnesses.append(
                {
                    "kind": "complete",
                    "ps": offset + done_ps,
                    "tag": tag,
                    "generation": generation,
                    "boot": 1,
                    "config": 1,
                    "write": int(write),
                    "physical": int(not failed),
                    "integrity": 1,
                    "failed": int(failed),
                    "response": 2 if failed else 0,
                    "data": hex(int.from_bytes(logical_data, "little")),
                }
            )
        if case:
            witnesses.append({"kind": "flip", "ps": offset + 405, "address": address + 11, "xor": 192})
        for i in range(3):
            response = 2 if case and i else 0
            source.append(
                {"kind": "b", "ps": offset + 650 + i, "port": 0, "id": base + i, "response": response, "ready": 1}
            )
        source.append({"kind": "b", "ps": offset + 220, "port": 0, "id": base, "response": 0, "ready": 0})
        for beat in range(4):
            value = 0 if case else int.from_bytes(final[beat * 16 : beat * 16 + 16], "little")
            source.append(
                {
                    "kind": "r",
                    "ps": offset + 630 + beat * 10,
                    "port": 0,
                    "id": base + 3,
                    "data": hex(value),
                    "response": 2 if case else 0,
                    "last": int(beat == 3),
                    "ready": 1,
                }
            )
        source.append(
            {
                "kind": "r",
                "ps": offset + 620,
                "port": 0,
                "id": base + 3,
                "data": hex(0 if case else int.from_bytes(final[:16], "little")),
                "response": 2 if case else 0,
                "last": 0,
                "ready": 0,
            }
        )
    # Stall records cover every source edge, as required of the actual observer.
    for e in list(source):
        if e["kind"] in ("b", "r") and not e["ready"]:
            stop = (650 if e["kind"] == "b" else 630) + (1000 if e["id"] >= 168 else 0)
            source.extend(dict(e, ps=ps) for ps in range(e["ps"] + 1, stop))
    for ps in range(1, 1702):
        flags = 0
        for e in source:
            if e.get("ps") != ps or e["kind"] not in ("aw", "ar", "w", "b", "r"):
                continue
            valid, ready = dict(aw=(9, 8), ar=(7, 6), w=(5, 4), b=(3, 2), r=(1, 0))[e["kind"]]
            flags |= 1 << valid | e.get("ready", 1) << ready
        source.append(dict(kind="axi", ps=ps, port=0, flags=flags))
    source[1:] = sorted(source[1:], key=lambda e: e["ps"])
    witnesses[1:] = sorted(witnesses[1:], key=lambda e: e["ps"])
    source.append({"kind": "end", "ps": 1702})
    witnesses.append(
        dict(
            kind="end",
            ps=1700,
            settled=1,
            host_fault=0,
            channel_fault=0,
            stale=0,
            late_error=0,
            state=1,
            drained=1,
            pin_drained=1,
            ecc_drained=1,
        )
    )
    for events in (source, witnesses, accesses):
        for e in events:
            for key in ("ps", "tail_ps", "return_tail_ps"):
                if key in e:
                    e[key] *= AXI_PS
    return source, witnesses, accesses


def pin_fixture():
    """Generate external buses from the existing independent PHY/WCK models.

    No internal request/completion witness reaches replay. The explicit fault
    changes the generator's DRAM image and is independently applied by replay.
    """
    config = Path(__file__).resolve().parents[2] / "config/contracts/profiles.json"
    p = json.loads(config.read_text())["profiles"][0]
    p["ranks"] = 1
    gen, events = DigitalPhy(p), []
    value = bytes(range(64))

    def emit(cs=0, ca=0, wck=0):
        ck, ratio = len(events), p["ratio"]
        burst = gen.command(ck, cs, ca)
        if burst is not None and not burst.write:
            gen.schedule_read(burst)
        write_data = we = wcs = re = rcs = 0
        for b in gen.dram.bursts:
            origin = b.command_ck - p["management_ck"]["tctrl_delay"]
            for lane in range(ratio):
                at = ck * ratio + lane
                start = origin * ratio + p["dfi_wck"]["tphy_wrcslat" if b.write else "tphy_rdcslat"]
                span = b.beats // 2 + (8 if ratio == 4 and b.beats == 32 else 0)
                if start <= at < start + span:
                    if b.write:
                        wcs |= (1 << b.rank) << (lane * 2)
                    else:
                        rcs |= ((1 << b.rank) * 5) << (lane * 4)
                for word in range(b.beats // 2):
                    en = origin * ratio + p["dfi_wck"]["tphy_wrlat" if b.write else "trddata_en"] + word
                    en += 8 if ratio == 4 and b.beats == 32 and word >= 8 else 0
                    if at == en:
                        if b.write:
                            we |= 3 << (lane * 2)
                        else:
                            re |= 3 << (lane * 2)
                    if b.write and at == en + p["dfi_wck"]["tphy_wrdata"]:
                        data = int.from_bytes(value[word * 4 : word * 4 + 4], "little")
                        write_data |= data << (lane * 32)
                        gen.write_word(b, word, at * 2, data)
        returned = valid = 0
        for lane, slices in enumerate(gen.returned(ck)):
            for byte, item in enumerate(slices):
                if item is not None:
                    valid |= 1 << (lane * 2 + byte)
                    returned |= (item.value & 255) << (lane * 32 + byte * 8)
                    returned |= (item.value >> 8) << (lane * 32 + 16 + byte * 8)
        events.append(
            dict(
                kind="phy",
                channel=0,
                ck=ck,
                ps=625 + ck * p["ck_ps"],
                cs=cs,
                ca=ca,
                wck=wck,
                write=write_data << 48 | we << 24 | wcs << 16,
                read=re << 16 | rcs,
                **{"return": returned << 40 | valid << 32},
            )
        )

    def transfer(write):
        rows, _, _ = wck_fixture(p, write)
        rows = [[i, 0, 0, 0] for i in range(25)] + [[i + 25, cs, ca, w] for i, cs, ca, w in rows]
        act = commands.activate(0, 0)
        rows[0][1:3], rows[1][1:3] = [1, act[0]], [1, act[1]]
        for _, cs, ca, wck in rows:
            emit(cs, ca, wck)
        for _ in range(70):
            emit()
        emit(1, commands.maintenance("PRE"))
        for _ in range(25):
            emit()

    transfer(True)
    flip = dict(kind="flip", ps=events[-1]["ps"] + 625, address=11, xor=192)
    gen.dram.memory[0, 0, 0, 11] ^= 192
    transfer(False)
    return events, p, [flip]


class CompositionPinTests(unittest.TestCase):
    def test_write_tail_separates_pin_path_from_dfi_flush(self):
        p = json.loads((Path(__file__).resolve().parents[2] / "config/contracts/profiles.json").read_text())[
            "profiles"
        ][0]
        # CAS100, physical last half-WCK937; DFI data/CS ends at115CK,
        # and the separate 64-WCK output flush ends at131CK.
        self.assertEqual(write_tail(p, 100, 937, 32), 131)
        self.assertEqual(write_tail(p, 100, 2000, 32), Fraction(2001, 8))
        p["dfi_wck"]["tphy_wrcslat"] = 1000
        self.assertEqual(write_tail(p, 100, 937, 32), 356)

    def test_real_pin_adapter_checks_raw_memory_and_external_flip(self):
        events, p, flips = pin_fixture()
        accesses = replay(events, p, flips)
        self.assertEqual([a["write"] for a in accesses], [True, False])
        self.assertEqual(accesses[0]["data"], bytes(range(64)))
        corrupted = bytearray(range(64))
        corrupted[11] ^= 192
        self.assertEqual(accesses[1]["data"], corrupted)
        self.assertGreater(accesses[1]["return_tail_ps"], accesses[1]["ps"])
        with self.assertRaisesRegex(Violation, "physical returned data"):
            replay(events, p, [])

    def test_rejects_active_ca_wck_bus_return_and_tail_corruptions(self):
        events, p, flips = pin_fixture()
        for mutation, message in (
            ("write_cs", "write CS/CA"),
            ("read_cs", "read enable/CS/CA"),
            ("read_enable", "read enable/CS/CA"),
            ("write_data", "physical returned data"),
            ("return_data", "physical returned data"),
            ("return_dnv", "unavailable return"),
            ("wck", "WCK"),
            ("ca", "access without ready open row"),
            ("sample_gap", "pin sample coverage"),
            ("tail", "tail|deadline"),
        ):
            altered = copy.deepcopy(events)
            if mutation == "sample_gap":
                del altered[10]
            elif mutation == "tail":
                first = next(i for i, e in enumerate(altered) if e["return"] >> 32 & 255)
                altered = altered[: first + 1]
            else:
                if mutation == "ca":
                    e = next(e for e in altered if e["cs"] and e["ca"] == commands.access(False, 32, 0, 0))
                    e["ca"] = commands.access(False, 32, 1, 0)
                elif mutation == "wck":
                    next(e for e in altered if e["wck"] >> 32 & 255)["wck"] ^= 1
                else:
                    field, flag, shift = {
                        "write_cs": ("write", 16, 16),
                        "read_cs": ("read", 0, 0),
                        "read_enable": ("read", 16, 16),
                        "write_data": ("write", 48, 48),
                        "return_data": ("return", 32, 40),
                        "return_dnv": ("return", 32, 16),
                    }[mutation]
                    next(e for e in altered if e[field] >> flag & 255)[field] ^= 1 << shift
            with self.subTest(mutation=mutation), self.assertRaisesRegex((Violation, ValueError), message):
                replay(altered, p, flips)


class CompositionTests(unittest.TestCase):
    def test_result_must_follow_each_independent_tail(self):
        for write in (False, True):
            s, w, accesses = fixture()
            index = next(i for i, a in enumerate(accesses) if a["write"] == write)
            result = [e for e in w if e["kind"] == "result"][index]
            tail = "tail_ps" if write else "return_tail_ps"
            accesses[index][tail] = result["ps"]
            with self.subTest(write=write), self.assertRaisesRegex(Violation, "physical/result/final chronology"):
                correlate(s, w, accesses)

    def test_synthetic_success_and_failed_contributor(self):
        result = correlate(*fixture())
        self.assertEqual(result["individual_B"], 6)
        self.assertEqual(result["individual_R_beats"], 8)
        self.assertEqual(result["forwarded_reads_with_unfinished_contributors"], 2)
        self.assertFalse(result["controller_qualified"])

    def test_rejects_wrong_physical_owner_outcome_and_missing_integrity(self):
        for mutation, message in (
            ("owner", "unknown inline physical owner"),
            ("owner_generation", "unknown inline physical owner"),
            ("physical_failed", "backend result"),
            ("physical_integrity", "backend result"),
            ("physical_incomplete", "backend result"),
            ("logical_failure_lost", "independent ECC/final integrity"),
            ("logical_integrity", "independent ECC/final integrity"),
            ("logical_status", "correlated completion outcome"),
            ("failed_write_side_effect", "physical operation coverage"),
        ):
            s, w, a = fixture()
            physical = next(e for e in w if e["kind"] == "physical")
            result = next(e for e in w if e["kind"] == "result")
            failed = next(e for e in w if e["kind"] == "complete" and e["failed"])
            if mutation in ("owner", "owner_generation"):
                physical[mutation] += 100
            elif mutation == "physical_failed":
                result["failed"] = 1
            elif mutation == "physical_integrity":
                result["integrity"] = 0
            elif mutation == "physical_incomplete":
                result["physical"] = 0
            elif mutation == "logical_failure_lost":
                failed["failed"] = 0
            elif mutation == "logical_integrity":
                failed["integrity"] = 0
            elif mutation == "logical_status":
                failed["response"] = 0
            else:
                a.append(dict(a[-1], write=True))
            with self.subTest(mutation=mutation), self.assertRaisesRegex(Violation, message):
                correlate(s, w, a)

    def test_reject_adapter_mutations(self):
        mutations = {
            "forwarded_byte": "composition forwarded byte overlay",
            "missing_overlay": "composition missing byte overlay",
            "missing_contributor": "composition missing/unknown integrity contributor",
            "contributor_generation": "composition contributor generation",
            "merge_original_loss": "composition held request changed",
            "merge_byte": "composition merged byte value",
            "physical_data_byte": "composition inline request/raw write bytes",
            "check_byte": "composition raw merged data/check image",
            "read_old_image": "composition returned raw data/result",
            "physical_identity": "composition physical result identity",
            "early_r": "composition response before final contributor integrity",
            "early_b": "composition response before final contributor integrity",
            "r_data": "composition R overlay/count/last",
            "r_failure_lost": "composition response outcome/propagated contributor failure",
            "missing_b": "composition individual response settlement",
            "duplicate_b": "composition duplicate original B",
            "missing_r": "composition individual response settlement",
            "missing_integrity": "composition logical operation coverage",
            "missing_physical": "composition physical operation coverage",
            "source_tail": "composition missing end",
            "witness_tail": "composition missing end",
            "wrong_epoch": "composition physical result identity",
            "read_before_full_W": "composition read before contributor full W",
        }
        for mutation, message in mutations.items():
            with self.subTest(mutation=mutation):
                s, w, a = fixture()
                read = next(e for e in w if e["kind"] == "request" and not e["write"])
                merge = next(e for e in w if e["kind"] == "request" and len(e["owners"]) == 2)
                if mutation == "forwarded_byte":
                    read["overlay_data"] = hex(int(read["overlay_data"], 0) ^ 1)
                    # Keep held fields consistent so the byte oracle is tested.
                    next(e for e in w if e["kind"] == "request" and e["tag"] == read["tag"] and e["phase"] == "take")[
                        "overlay_data"
                    ] = read["overlay_data"]
                elif mutation == "missing_overlay":
                    for e in w:
                        if e["kind"] == "request" and e["tag"] == read["tag"]:
                            e["overlay_mask"] = "0x0"
                elif mutation == "read_before_full_W":
                    read["ps"] = 35 * AXI_PS
                    w[1:-1] = sorted(w[1:-1], key=lambda e: e["ps"])
                elif mutation == "missing_contributor":
                    read["contributors"].pop()
                elif mutation == "contributor_generation":
                    read["contributors"][-1]["generation"] += 1
                elif mutation == "merge_original_loss":
                    merge["owners"].pop()
                elif mutation == "merge_byte":
                    for e in w:
                        if e["kind"] == "request" and e["tag"] == merge["tag"]:
                            e["data"] = hex(int(e["data"], 0) ^ 1)
                elif mutation in ("physical_data_byte", "check_byte"):
                    i = next(
                        i
                        for i, e in enumerate(a)
                        if e["write"] and (e["address"] >= DATA_BYTES) == (mutation == "check_byte")
                    )
                    value = bytearray(a[i]["data"])
                    value[0] ^= 1
                    a[i]["data"] = bytes(value)
                    if mutation == "check_byte":
                        p = [e for e in w if e["kind"] == "physical"][i]
                        p["data"] = hex(int.from_bytes(value, "little"))
                elif mutation == "read_old_image":
                    value = bytearray(a[5]["data"])
                    value[0] ^= 1
                    a[5]["data"] = bytes(value)
                elif mutation in ("physical_identity", "wrong_epoch"):
                    e = next(e for e in w if e["kind"] == "result")
                    e["tag" if mutation == "physical_identity" else "boot"] += 1
                elif mutation in ("early_r", "early_b"):
                    e = next(
                        e
                        for e in s
                        if e["kind"] == ("r" if mutation == "early_r" else "b")
                        and e["id"] == (163 if mutation == "early_r" else 161)
                    )
                    e["ps"] = 590 * AXI_PS
                    s[1:-1] = sorted(s[1:-1], key=lambda e: e["ps"])
                elif mutation in ("r_data", "r_failure_lost"):
                    e = next(
                        e for e in s if e["kind"] == "r" and e["id"] == (171 if mutation == "r_failure_lost" else 163)
                    )
                    e["response"] = 0 if mutation == "r_failure_lost" else e["response"]
                    if mutation == "r_data":
                        e["data"] = hex(int(e["data"], 0) ^ 1)
                elif mutation in ("missing_b", "duplicate_b", "missing_r"):
                    i = (
                        max(i for i, e in enumerate(s) if e["kind"] == "r" and e["ready"])
                        if mutation == "missing_r"
                        else next(i for i, e in enumerate(s) if e["kind"] == "b" and e["ready"])
                    )
                    if mutation == "duplicate_b":
                        s.insert(i + 1, dict(s[i]))
                    else:
                        del s[i]
                elif mutation in ("missing_integrity", "missing_physical"):
                    del w[
                        next(
                            i
                            for i, e in enumerate(w)
                            if e["kind"] == ("complete" if mutation == "missing_integrity" else "physical")
                        )
                    ]
                elif mutation == "source_tail":
                    s.pop()
                elif mutation == "witness_tail":
                    w.pop()
                with self.assertRaisesRegex(Violation, message):
                    correlate(s, w, a)

    def test_requires_actual_hazard_and_final_settlement(self):
        for mutation, message in (
            ("missing_hazard", "core incomplete-W"),
            ("forwardable", "incomplete-W blocking"),
            ("unsettled", "final owner settlement"),
            ("channel_fault", "final owner settlement"),
        ):
            s, w, a = fixture()
            if mutation == "missing_hazard":
                w[:] = [e for e in w if e["kind"] != "hazard"]
            elif mutation == "forwardable":
                next(e for e in w if e["kind"] == "hazard")["forwardable"] = 1
            elif mutation == "channel_fault":
                w[-1]["channel_fault"] = 1
            else:
                w[-1]["settled"] = 0
            with self.subTest(mutation=mutation), self.assertRaisesRegex(Violation, message):
                correlate(s, w, a)

    def test_source_cycles_detect_disappearance_identity_and_missing_stalls(self):
        s, _, _ = fixture()
        self.assertGreater(source_cycles(s)["b_case0"], 2)
        for mutation, message in (
            ("gap", "cycle gap"),
            ("missing_payload", "cycle/payload"),
            ("id_change", "held response changed"),
            ("disappear", "held VALID disappeared"),
            ("no_probe", "missing B/R hold probes"),
        ):
            events = copy.deepcopy(s)
            if mutation == "gap":
                del events[next(i for i, e in enumerate(events) if e["kind"] == "axi" and e["ps"] == 100 * AXI_PS)]
            elif mutation in ("missing_payload", "disappear"):
                del events[next(i for i, e in enumerate(events) if e["kind"] == "b" and e["ps"] == 222 * AXI_PS)]
                if mutation == "disappear":
                    next(e for e in events if e["kind"] == "axi" and e["ps"] == 222 * AXI_PS)["flags"] &= ~12
            elif mutation == "id_change":
                next(e for e in events if e["kind"] == "b" and e["ps"] == 222 * AXI_PS)["id"] += 1
            else:
                for e in events:
                    if e["kind"] == "axi" and 620 * AXI_PS <= e["ps"] < 630 * AXI_PS:
                        e["flags"] &= ~3
                events[:] = [e for e in events if not (e["kind"] == "r" and e["id"] == 163 and not e["ready"])]
            with self.subTest(mutation=mutation), self.assertRaisesRegex(Violation, message):
                source_cycles(events)


class CompositionRunnerTests(unittest.TestCase):
    def test_composition_witness_reserved_before_source_trace_creation(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as tmp:
            report = Path(tmp) / "report.json"
            witness = report.with_name("composition-observations.jsonl")
            witness.write_text("retained bytes\n")
            with self.assertRaises(FileExistsError):
                runner.reserve_observations(report, "datapath", False, False, composition=True)
            self.assertFalse(report.with_name("observations.jsonl").exists())
            self.assertEqual(witness.read_text(), "retained bytes\n")

    def test_valid_cli_selects_composition_oracle_and_retains_launch_inputs(self):
        # This proves runner routing only. No HDL tool or physical replay runs.
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            root = Path(temporary)
            ip = root / "ips/lpddr5_mc"
            for name in (
                "rtl/integration/controller_stub.sv",
                "dv/channel/lpddr5_mc_channel_memory.sv",
                "dv/integration/lpddr5_mc_observer.sv",
                "dv/integration/datapath_tb.sv",
            ):
                path = ip / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("")
            (ip / "rtl/files.f").write_text("integration/controller_stub.sv\n")
            config = ip / "config/contracts/profiles.json"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(json.dumps({"profiles": [{"unit_test_only": True}]}))
            report = root / "evidence/report.json"
            argv = [
                "check.py",
                "--report",
                str(report),
                "--family",
                "datapath",
                "--ports",
                "1",
                "--channels",
                "1",
                "--integrity-mode",
                "1",
                "--ecc-composition",
            ]

            def completed(argv, **kwargs):
                kwargs["stdout"].write("DATAPATH_INTEGRATION_PASS synthetic runner test\n")
                return CompletedProcess(argv, 0)

            def git_output(argv, **kwargs):
                return "" if "status" in argv else "unit-test-revision\n"

            with (
                mock.patch.object(runner, "REPO", root),
                mock.patch.object(runner, "IP", ip),
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(sys, "pycache_prefix", None),
                mock.patch.dict(os.environ, {"DHENARA_AGENT_TOOL_STORAGE_ROOT": str(root / "tools")}),
                mock.patch.object(runner.subprocess, "run", side_effect=completed),
                mock.patch.object(runner.subprocess, "check_output", side_effect=git_output),
                mock.patch("vectors.generate"),
                mock.patch("ecc_composition.check", return_value={"unit_only": True}) as oracle,
                mock.patch("workload_costs.measure", return_value={}),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                runner.main()
            saved = json.loads(report.read_text())
            self.assertEqual(saved["status"], "passed")
            self.assertIn("ips/lpddr5_mc/rtl/integration/controller_stub.sv", saved["source_sha256"])
            self.assertIn(str(ip / "rtl/integration/controller_stub.sv"), saved["planned_commands"][1])
            self.assertEqual(saved["ecc_composition_observations"], {"unit_only": True})
            self.assertTrue(saved["ecc_composition"])
            self.assertIn("-GECC_COMPOSITION=1", saved["planned_commands"][1])
            self.assertIn(
                "+composition_observations=" + str(report.with_name("composition-observations.jsonl")),
                saved["planned_commands"][2],
            )
            oracle.assert_called_once_with(
                report.with_name("observations.jsonl"),
                report.with_name("composition-observations.jsonl"),
                report.with_name("report-2.log"),
                {"unit_test_only": True},
            )

    def test_composition_rejects_incompatible_cli_before_tool_execution(self):
        for extra in (
            [],
            ["--family", "controller"],
            ["--ports", "2"],
            ["--axi-overlap"],
            ["--integrity-mode", "0"],
            ["--profile-number", "4"],
        ):
            with self.subTest(extra=extra):
                args = ["check.py", "--report", "/tmp/unused-composition-test.json", "--ecc-composition"]
                if extra:
                    args += ["--family", "datapath", "--ports", "1", "--channels", "1", "--integrity-mode", "1"] + extra
                with mock.patch.object(sys, "argv", args), contextlib.redirect_stderr(io.StringIO()):
                    with mock.patch.object(runner.subprocess, "run") as launch:
                        with self.assertRaises(SystemExit) as error:
                            runner.main()
                        self.assertEqual(error.exception.code, 2)
                        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
