"""Synthetic adapter/sensitivity fixtures; no RTL is executed by these tests."""

import argparse
import copy
import hashlib
import json
import unittest
from pathlib import Path

from continuous import DATA_BYTES, InlineDrain
from continuous_test import granule, inline_fixture, result
from contracts import commands
from contracts.integrity import dbi_encode
from observations import FLAGS, HANDSHAKE_PHASE, PHASE, TICK_FIELDS, Case, binding_digest, bindings, check
from physical_stream import PhysicalStream
from system.dram import decode
from system.ecc import InlineWitness
from system.host import Violation
from system.phy import DigitalPhy
from wck_observations_test import fixture as wck_fixture


class Fixture:
    """Generate raw buses with an independent digital PHY and WCK fixture.

    The model generates pin bytes/rotation from CA, never adapter results.
    Deliberate mutations below independently challenge the adapter wiring.
    """

    def __init__(self, kind=0, failed=0):
        self.p = bindings()[0]
        self.kind, self.failed = kind, failed
        self.frames = []
        self.gen = DigitalPhy(self.p)
        self.state = dict(
            reset_n=1,
            confirmed=1,
            settled=1,
            trusted=0,
            config=0,
            config_valid=0,
            trained=0,
            training_epoch=0,
            boot=0,
            ratio=4,
            profile=0,
            profile_image=1,
            inline_idle=1,
            drained=1,
            pin_drained=1,
            ecc_drained=1,
            thermal=9,
            owner=1,
            rank_valid=1,
        )
        self.emit(reset_n=0)
        self.emit()
        self.emit(config_commit=1, prepared=1, prepared_epoch=1)
        self.emit(train_ack=1, train_token=0)
        self.state.update(trusted=1, trained=1, config=1, config_valid=1, init_complete=1, owner=2)
        self.emit()
        self.q = granule("inline_admit", tag=101, write=1, data=int.from_bytes(bytes(range(64)), "little"))
        self.emit(events=[dict(self.q, kind="original_admit"), self.q])
        self.state.update(inline_idle=0, drained=0, ecc_drained=0, settled=0)

    def emit(self, *, events=(), cs=0, ca=0, wck=0, **changes):
        tick = len(self.frames)
        e = dict.fromkeys(TICK_FIELDS, 0)
        e.update(self.state)
        e.update(
            tick=tick,
            case_id=self.kind * 2 + self.failed,
            cycles=max(0, tick - 1),
            time_ps=1000 + tick * 5000 + tick // 7 * 1000,
            cs=cs,
            ca=ca,
            wck=wck,
            mr4=9,
        )
        e.update(changes)
        e["memory_reset"] = int(bool(e["destructive"]))
        if e["reset_n"] and not e["destructive"]:
            ck, r, p = tick - 1, self.p["ratio"], self.p
            b = self.gen.command(ck, cs, ca)
            if b is not None and not b.write:
                self.gen.schedule_read(b)
            write_data = write_mask = we = wcs = re = rcs = 0
            for b in self.gen.dram.bursts:
                origin = b.command_ck - p["management_ck"]["tctrl_delay"]
                for lane in range(r):
                    at = ck * r + lane
                    start = origin * r + p["dfi_wck"]["tphy_wrcslat" if b.write else "tphy_rdcslat"]
                    span = b.beats // 2 + (8 if r == 4 and b.beats == 32 else 0)
                    if start <= at < start + span:
                        if b.write:
                            wcs |= (1 << b.rank) << (lane * 2)
                        else:
                            rcs |= ((1 << b.rank) * 5) << (lane * 4)
                    for word in range(b.beats // 2):
                        en = origin * r + p["dfi_wck"]["tphy_wrlat" if b.write else "trddata_en"] + word
                        en += 8 if r == 4 and b.beats == 32 and word >= 8 else 0
                        if at == en:
                            if b.write:
                                we |= 3 << (lane * 2)
                            else:
                                re |= 3 << (lane * 2)
                        if b.write and at == en + p["dfi_wck"]["tphy_wrdata"]:
                            value = mask = 0
                            for byte in range(4):
                                raw = self.value[word * 4 + byte]
                                raw, invert = dbi_encode(raw) if p["write_dbi"] else (raw, 0)
                                value |= raw << (byte * 8)
                                mask |= invert << byte
                            write_data |= value << (lane * 32)
                            write_mask |= mask << (lane * 4)
                            self.gen.write_word(b, word, at * 2, value, mask)
            e["write"] = write_data << 48 | write_mask << 32 | we << 24 | wcs << 16
            e["read"] = re << 16 | rcs
            ret = valid = 0
            for lane, slices in enumerate(self.gen.returned(ck)):
                for byte, item in enumerate(slices):
                    if item is None:
                        continue
                    valid |= 1 << (lane * 2 + byte)
                    ret |= (item.value & 255) << (lane * 32 + byte * 8)
                    ret |= (item.value >> 8) << (lane * 32 + 16 + byte * 8)
            e["return"] = ret << 40 | valid << 32
        h = dict.fromkeys(FLAGS.values(), 0)
        for q in events:
            q.setdefault("payload", 0)
            if q["kind"] in FLAGS:
                h[FLAGS[q["kind"]]] = 1
        self.frames.append((e, h, list(events)))

    def transfer(self, at, write, value, tag):
        q = granule("inline_physical", at=at, tag=tag, write=write, data=int.from_bytes(value, "little"))
        self.value = value
        self.emit(events=[q])
        rows, _, _ = wck_fixture(self.p, bool(write))
        bank, row, col = at >> 11 & 15, at >> 15, at >> 5 & 63
        act = commands.activate(row, bank)
        rows = [[i, 0, 0, 0] for i in range(25)] + [[i + 25, cs, ca, w] for i, cs, ca, w in rows]
        rows[0][1:3] = [1, act[0]]
        rows[1][1:3] = [1, act[1]]
        rows[31][2] = commands.access(bool(write), 32, bank, col)
        for _, cs, ca, w in rows:
            self.emit(cs=cs, ca=ca, wck=w)
        for _ in range(70):
            self.emit()
        self.emit(cs=1, ca=commands.maintenance("PRE"))
        for _ in range(25):
            self.emit()
        self.emit(events=[result(q, data=int.from_bytes(value, "little"))])

    def dirty(self):
        self.transfer(DATA_BYTES, 0, bytes(64), 1)
        self.transfer(0, 0, bytes(64), 2)
        new = bytes(range(64))
        self.transfer(0, 1, new, 3)
        self.state.update(data_committed=1)
        cmd = (3, 7, 4, -1)[self.kind]
        if self.kind == 3:
            self.state.update(phymstr_req=1)
        self.emit(command_valid=int(self.kind != 3), command_ready=1, command=cmd, phymstr_req=int(self.kind == 3))
        if self.failed:
            self.state.update(fault=1, trusted=0, owner=4)
            self.emit(error=1)
            self.emit()
            self.emit(destructive=1, settled=1, confirmed=1)
            self.state.update(
                boot=1,
                config=0,
                config_valid=0,
                trained=0,
                data_committed=0,
                inline_idle=1,
                drained=1,
                ecc_drained=1,
                settled=1,
                fault=0,
                owner=1,
            )
            # A destructive reset clears the generator; the adapter must retain
            # and separately count the old interval before discarding it.
            self.gen = DigitalPhy(self.p)
            self.emit()
        else:
            checks = bytearray(64)
            for w in range(8):
                checks[w] = InlineWitness.check_byte(int.from_bytes(new[w * 8 : w * 8 + 8], "little"))
            self.transfer(DATA_BYTES, 1, checks, 4)
            # Hardware retains its data_committed bit after check completion.
            offer = dict(result(self.q), kind="original_offer")
            for _ in range(9):
                self.emit(completion_valid=1, events=[offer])
            self.emit(
                completion_valid=1,
                completion_ready=1,
                events=[offer, dict(offer, kind="inline_complete"), dict(offer, kind="original_complete")],
            )
            self.state.update(inline_idle=1, drained=1, pin_drained=1, ecc_drained=1, settled=1)
            entry = ("update", "fsp", "power", "master")[self.kind]
            if self.kind in (0, 1):
                # Lifecycle ownership precedes the engine's request output.
                self.state["owner"] = 1
                self.emit()
            self.state[entry] = 1
            if self.kind == 1:
                self.state["fsp_busy"] = 1
            elif self.kind == 3:
                self.state["owner"] = 1
            self.emit()
            if self.kind == 0:
                self.emit(ctrlupd_ack=1)
                for _ in range(self.p["management_ck"]["tctrlupd_min"]):
                    self.emit()
            elif self.kind == 1:
                self.state["init_complete"] = 0
                self.emit()
                for ca in commands.mode_write(1, self.p["mr"]["1"]):
                    self.emit(cs=1, ca=ca)
                for _ in range(max(self.p["dram_ck"]["tmrw"], self.p["dram_ck"]["tmrd"])):
                    self.emit()
                self.state["fsp"] = 0
                self.emit()
                self.state["init_complete"] = 1
                self.emit(init_complete=1, fsp_ready=1)
                self.emit(config_commit=1, prepared=1, prepared_epoch=2)
                self.p = bindings()[1]
                self.state.update(config=2, profile=1, ratio=2, profile_image=2)
                self.gen.p = self.gen.dram.p = self.p
                self.gen.dram.ck = 4
                self.gen.dram.bus_slots.clear()
                self.state["fsp_busy"] = 0
            elif self.kind == 2:
                self.emit(lp_ctrl_ack=1, lp_data_ack=1)
                self.emit(command_valid=1, command_ready=1, command=6)
            else:
                self.emit(rank_valid=0)
                self.emit(rank_valid=1)
                self.state["phymstr_req"] = 0
                self.emit()
            self.state[entry] = 0
            if self.kind in (0, 1, 3):
                # ACK/request release is sampled before lifecycle handback.
                if self.kind == 3:
                    self.state["owner"] = 3
                self.emit()
                self.state["owner"] = 2
        self.emit()
        return self

    def records(self, tick=0):
        out = [
            dict(
                kind="case_begin",
                case_id=self.kind * 2 + self.failed,
                tick=tick,
                management=self.kind,
                failed=self.failed,
            )
        ]
        for e, h, events in self.frames:
            out.append(dict(e, kind="tick", tick=e["tick"] + tick, time_ps=e["time_ps"] + tick * 7000))
            out.append(
                dict(
                    h,
                    kind="handshakes",
                    tick=e["tick"] + tick,
                    case_id=e["case_id"],
                    time_ps=e["time_ps"] + tick * 7000 + 1000,
                )
            )
            out.extend(dict(q, tick=e["tick"] + tick, case_id=e["case_id"]) for q in events)
        out.append(dict(kind="case_end", case_id=self.kind * 2 + self.failed, tick=tick + len(self.frames)))
        return out


def trace():
    h = dict(
        kind="header",
        version=2,
        phase=PHASE,
        handshake_phase=HANDSHAKE_PHASE,
        time_unit="ps",
        timing="one_tick_one_profile_CK",
        profile_binding="dirty-management-v1",
        profile_sha256=binding_digest(bindings()),
        cases=8,
    )
    out, tick = [h], 0
    for k in range(4):
        for failed in range(2):
            f = Fixture(k, failed).dirty()
            out.extend(f.records(tick))
            tick += len(f.frames)
    out.append(dict(kind="end", tick=tick, complete=1))
    return out


REJECTIONS = []
LEGAL_PATHS = []


class ObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = trace()

    def test_all_eight_raw_compositions(self):
        r = check(self.events)
        self.assertEqual(len(r["cases"]), 8)
        self.assertEqual(sum(c["original_completions"] for c in r["cases"]), 4)
        self.assertEqual(sum(c["original_cancellations"] for c in r["cases"]), 4)
        self.assertTrue(all(c["physical_intervals"] for c in r["cases"]))
        for c in r["cases"]:
            self.assertTrue(all(b["ready"] and not b["open_banks"] for b in c["management_boundaries"]))
            if not c["failed"] and c["management"] in ("update", "fsp", "master"):
                self.assertTrue(any(o["owner"] == 1 and not o["reset"] for o in c["ownership"]))
            for key, value in c["wck_total"].items():
                self.assertEqual(value, sum(i["wck_total"][key] for i in c["physical_intervals"]))
            if c["failed"]:
                self.assertIsNotNone(c["physical_intervals"][0]["cancelled"])
                self.assertTrue(c["physical_intervals"][0]["retention_suspensions"])
        fsp = r["cases"][2]["physical_intervals"][0]
        self.assertTrue(fsp["prior_wck_profiles"])
        self.assertGreater(fsp["wck_total"]["accesses"], fsp["wck"]["accesses"])
        LEGAL_PATHS.append(dict(path="eight-case ownership/row baseline", cases=r["cases"]))

    def reject(self, change, reason=None):
        altered = copy.deepcopy(self.events)
        change(altered)
        with self.assertRaisesRegex((Violation, ValueError, KeyError), reason or ".") as caught:
            check(altered)
        REJECTIONS.append(dict(test=self.id(), expected=reason, observed=str(caught.exception)))

    def test_missing_duplicate_tick_phase_profile_and_tails(self):
        for change in (
            lambda t: t.pop(2),
            lambda t: t.insert(3, t[2]),
            lambda t: t[0].pop("phase"),
            lambda t: t[0].pop("handshake_phase"),
            lambda t: t[0].update(profile_sha256="bad"),
            lambda t: t.pop(),
            lambda t: t[-1].update(complete=0),
            lambda t: t.pop(-2),
        ):
            with self.subTest(change=change):
                self.reject(change)

    def test_missing_handshake_and_raw_field(self):
        self.reject(lambda t: t.pop(next(i for i, e in enumerate(t) if e["kind"] == "inline_physical")))
        self.reject(lambda t: next(e for e in t if e["kind"] == "tick").pop("return"))

    def test_original_inline_payload_offer_and_reset_binding(self):
        for field in ("column", "write", "bytes", "mask", "data", "payload"):
            with self.subTest(field=field):
                self.reject(
                    lambda t, field=field: next(e for e in t if e["kind"] == "inline_admit").update(
                        {field: next(e for e in t if e["kind"] == "inline_admit")[field] ^ 1}
                    ),
                    "inline admission differs",
                )
        for field in ("write", "physical", "integrity", "failed", "response", "data", "payload"):
            with self.subTest(field=field):
                self.reject(
                    lambda t, field=field: next(e for e in t if e["kind"] == "original_complete").update(
                        {field: next(e for e in t if e["kind"] == "original_complete")[field] ^ 1}
                    ),
                    "original completion identity",
                )
        self.reject(
            lambda t: t.pop(next(i for i, e in enumerate(t) if e["kind"] == "original_offer")), "clock-edge handshake"
        )
        self.reject(lambda t: next(e for e in t if e["kind"] == "handshakes").pop("offer"))
        self.reject(
            lambda t: next(e for e in t if e["kind"] == "tick" and e["trusted"]).update(memory_reset=1),
            "memory reset without witnessed",
        )

    def test_missing_check_write_and_neighbor_corruption(self):
        def mutate(t, physical):
            q = next(e for e in t if e["kind"] == "inline_physical" and e["tag"] == 4)
            if physical:
                q["data"] ^= 1 << 400
            else:
                q["write"] = 0

        self.reject(lambda t: mutate(t, False), "data/check order")
        self.reject(lambda t: mutate(t, True), "shared check-line preservation")

    def test_pin_data_return_cs_wck_and_row_mutations(self):
        changes = (
            ("write", lambda e: e["kind"] == "tick" and e["write"] >> 48, 1 << 48),
            ("return", lambda e: e["kind"] == "tick" and e["return"] >> 32 & 255, 1 << 40),
            ("read", lambda e: e["kind"] == "tick" and e["read"], 1),
            ("wck", lambda e: e["kind"] == "tick" and e["wck"] & 3 == 3, 1),
            ("ca", lambda e: e["kind"] == "tick" and e["cs"] and e["ca"] & 7 == 3, 4),
        )
        for key, predicate, bit in changes:

            def change(t, predicate=predicate, key=key, bit=bit):
                e = next(e for e in t if predicate(e))
                e[key] ^= bit

            with self.subTest(key=key):
                self.reject(change)

    def test_early_completion_ownership_and_stale_epochs(self):
        for field, val, predicate in (
            ("physical", 0, lambda e: e["kind"] == "inline_result"),
            ("update", 1, lambda e: e["kind"] == "tick" and e["data_committed"]),
            ("boot", 99, lambda e: e["kind"] == "original_admit"),
            ("config", 99, lambda e: e["kind"] == "inline_admit"),
            ("training_epoch", 99, lambda e: e["kind"] == "tick" and e["train_ack"]),
            ("train_token", 99, lambda e: e["kind"] == "tick" and e["train_ack"]),
            ("config_commit", 0, lambda e: e["kind"] == "tick" and e["config_commit"]),
        ):

            def change(t, predicate=predicate, field=field, val=val):
                next(e for e in t if predicate(e))[field] = val

            with self.subTest(field=field):
                self.reject(change)

    def test_stale_scrub_rejected_before_new_version(self):
        events = inline_fixture(correction=True)
        m = InlineDrain()
        for e in events:
            m.event(e)
        self.assertEqual(m.scrubs, 1)
        m.event(events[0])
        stale = copy.deepcopy(events[2])
        stale["generation"] = 9
        m.event(events[1])
        with self.assertRaisesRegex(Violation, "stale inline physical result"):
            m.event(stale)

    def test_fault_does_not_cancel_pending_return_or_write(self):
        f = Fixture(0, 1).dirty()
        c = Case(dict(case_id=1, management=0, failed=1), bindings())
        for e, h, events in f.frames:
            c.sample(e)
            c.events(h, events)
        self.assertEqual(c.inline.aborted, 1)
        # A new request with no pin data cannot gain completion by faulting.
        p = PhysicalStream(bindings()[0])
        p.expect(granule("inline_physical", write=1))
        with self.assertRaisesRegex(Violation, "physical request tail"):
            p.summary()
        r = p.summary(cancelled=True)
        self.assertEqual(r["cancelled"]["requests"], 1)

    def test_pending_return_fault_deadline_and_reset_cancellation(self):
        f = Fixture(0, 1).dirty()
        p = PhysicalStream(bindings()[0])
        issued = False
        for e, _, events in f.frames[1:]:
            if issued:
                e = dict(e, trusted=0, **{"return": 0})
            try:
                p.sample(e)
            except Violation as error:
                self.assertIn("missing returned slice", str(error))
                REJECTIONS.append(dict(test=self.id(), observed=str(error)))
                break
            for q in events:
                if q["kind"] == "inline_physical":
                    p.expect(q)
            if e["cs"] and e["ca"] & 7 == 5:
                issued = True
                cancelled = p.summary(cancelled=True)
                self.assertGreater(cancelled["cancelled"]["return_slices"], 0)
                self.assertEqual(cancelled["wck"]["accesses"], 1)
        else:
            self.fail("missing return during fault was not rejected")

    def test_missing_refresh_rejected_without_fault_cancellation(self):
        for keep_refresh in (True, False):
            p = PhysicalStream(bindings()[0])
            for i in range(3126):
                ref = keep_refresh and i == 3000
                e = dict(
                    trusted=1,
                    thermal=9,
                    cs=int(ref),
                    ca=commands.maintenance("REF") if ref else 0,
                    wck=0,
                    write=0,
                    read=0,
                    **{"return": 0},
                )
                if i == 3125 and not keep_refresh:
                    with self.assertRaisesRegex(ValueError, "missed periodic refresh") as caught:
                        p.sample(e)
                    REJECTIONS.append(dict(test=self.id(), observed=str(caught.exception)))
                else:
                    p.sample(e)

    def test_management_ack_return_and_held_completion_mutations(self):
        self.reject(lambda t: next(e for e in t if e["kind"] == "tick" and e["ctrlupd_ack"]).update(ctrlupd_ack=0))
        self.reject(lambda t: next(e for e in t if e["kind"] == "tick" and e["lp_ctrl_ack"]).update(lp_ctrl_ack=0))
        self.reject(lambda t: next(e for e in t if e["kind"] == "tick" and e["command"] == 6).update(command=0))
        self.reject(
            lambda t: next(
                e for e in t if e["kind"] == "tick" and e["case_id"] == 6 and e["master"] and not e["rank_valid"]
            ).update(rank_valid=1)
        )

        def held(t):
            for e in t:
                if e["kind"] == "tick" and e["completion_valid"]:
                    e["completion_ready"] = 1

        self.reject(held, "held completion")

    def test_missing_last_pre_and_unrecovered_management_entry(self):
        for case in (0, 6):
            for late in (False, True):

                def change(t, case=case, late=late):
                    ticks = [e for e in t if e["kind"] == "tick" and e["case_id"] == case]
                    pre = [e for e in ticks if e["cs"] and decode(e["ca"]).kind == "PRE"][-1]
                    pre.update(cs=0, ca=0)
                    if late:
                        first = next(i for i, e in enumerate(ticks) if e["owner"] == 1 and e["trained"])
                        ticks[first - 1].update(cs=1, ca=commands.maintenance("PRE"))

                with self.subTest(case=case, late=late):
                    self.reject(change, "management ownership before independent row closure/recovery")

    def test_exclusive_ca_setup_active_and_return_windows(self):
        forbidden = [
            commands.activate(0, 0)[0],
            commands.activate(0, 0)[1],
            commands.maintenance("PRE"),
            commands.maintenance("REF"),
            commands.maintenance("RFM"),
            commands.access(False, 32, 0, 0),
            commands.access(True, 32, 0, 0),
            commands.mode_write(1, 0)[0],
            commands.mode_read(4),
            commands.cas("off"),
            commands.pack(104, 0),
            commands.pack(40, 0),
            commands.pack(48, 0x06),
        ]
        for case, phase in ((0, "setup"), (0, "active"), (0, "return"), (6, "active")):
            for ca in forbidden:

                def change(t, case=case, phase=phase, ca=ca):
                    ticks = [
                        e
                        for e in t
                        if e["kind"] == "tick" and e["case_id"] == case and e["owner"] == 1 and e["trained"]
                    ]
                    e = ticks[0] if phase == "setup" else ticks[-1] if phase == "return" else ticks[1]
                    e.update(cs=1, ca=ca)

                with self.subTest(case=case, phase=phase, ca=ca):
                    self.reject(change, "forbidden controller CA during exclusive PHY ownership")

    def test_ownership_request_ack_and_fsp_associations(self):
        for case, flag in ((0, "update"), (2, "fsp_busy"), (6, "master")):
            self.reject(
                lambda t, case=case, flag=flag: next(
                    e for e in t if e["kind"] == "tick" and e["case_id"] == case and e[flag]
                ).update(owner=2),
                "management request/ACK without PHY ownership",
            )
        for field, value in (("fsp_busy", 0), ("fsp", 0), ("init_complete", 1)):
            self.reject(
                lambda t, field=field, value=value: next(
                    e
                    for e in t
                    if e["kind"] == "tick" and e["case_id"] == 2 and e["cs"] and decode(e["ca"]).kind == "MRW1"
                ).update({field: value}),
                "forbidden controller CA",
            )
        for ca in (commands.activate(0, 0)[0], commands.maintenance("PRE")):
            self.reject(
                lambda t, ca=ca: next(
                    e
                    for e in t
                    if e["kind"] == "tick" and e["case_id"] == 2 and e["cs"] and decode(e["ca"]).kind == "MRW1"
                ).update(ca=ca),
                "forbidden controller CA",
            )

        def withdraw(t, simultaneous):
            ticks = [e for e in t if e["kind"] == "tick" and e["case_id"] == 6]
            i = next(i for i in range(1, len(ticks)) if ticks[i - 1]["master"] and not ticks[i]["master"])
            ticks[i - 1]["phymstr_req"] = 1
            if not simultaneous:
                ticks[i]["phymstr_req"] = 1

        for simultaneous in (False, True):
            self.reject(lambda t, simultaneous=simultaneous: withdraw(t, simultaneous), "master ACK return")

        def ack_without_request(t):
            ticks = [e for e in t if e["kind"] == "tick" and e["case_id"] == 6]
            i = next(i for i, e in enumerate(ticks) if e["master"])
            ticks[i - 1]["phymstr_req"] = 0

        self.reject(ack_without_request, "master ACK before sampled request")

        def early_update_return(t):
            ticks = [e for e in t if e["kind"] == "tick" and e["case_id"] == 0]
            i = next(i for i in range(1, len(ticks)) if ticks[i - 1]["update"] and not ticks[i]["update"])
            ticks[i].update(owner=2, cs=1, ca=commands.maintenance("PRE"))

        self.reject(early_update_return, "management request/ACK without PHY ownership")

    def test_phy_update_registered_request_ack_and_return(self):
        altered = copy.deepcopy(self.events)
        ticks = [e for e in altered if e["kind"] == "tick" and e["case_id"] == 0]
        window = [e for e in ticks if e["owner"] == 1 and e["trained"]]
        for i, e in enumerate(window):
            e.update(
                update=0, ctrlupd_ack=0, phyupd_req=int(i < len(window) - 2), phy_update=int(0 < i < len(window) - 1)
            )
        baseline = check(altered)["cases"][0]
        self.assertGreater(baseline["phyupd_ack_ticks"], 0)
        LEGAL_PATHS.append(dict(path="PHY-update registered ACK/return", case=baseline))
        for ca in (commands.maintenance("PRE"), commands.activate(0, 0)[0]):
            bad = copy.deepcopy(altered)
            next(e for e in bad if e["kind"] == "tick" and e["phy_update"]).update(cs=1, ca=ca)
            with self.assertRaisesRegex(Violation, "forbidden controller CA") as caught:
                check(bad)
            REJECTIONS.append(dict(test=self.id(), observed=str(caught.exception)))
        for field in ("phyupd_req", "phy_update"):
            bad = copy.deepcopy(altered)
            sample = next(e for e in bad if e["kind"] == "tick" and e["phy_update"])
            before = next(e for e in bad if e["kind"] == "tick" and e["tick"] == sample["tick"] - 1)
            before[field] = int(field == "phy_update")
            with self.assertRaisesRegex(Violation, "PHY update ACK") as caught:
                check(bad)
            REJECTIONS.append(dict(test=self.id(), observed=str(caught.exception)))

    def test_decoded_recovery_exact_sample_boundary(self):
        # Closed PREpb can recover before the same bank's last ACT+tRC.
        # Separately exercise PREab, REF/RFM, FSP tFC and ZQ recovery.
        for kind in ("PREpb", "PREab", "REF", "RFM", "FSP", "ZQ"):
            p = PhysicalStream(bindings()[0])

            def sample(ca=None, p=p):
                p.sample(dict(trusted=0, cs=int(ca is not None), ca=ca or 0, wck=0, write=0, read=0, **{"return": 0}))

            d, delay = p.p["dram_ck"], p.p["management_ck"]["tctrl_delay"]
            if kind.startswith("PRE"):
                for ca in commands.activate(0, 0):
                    sample(ca)
                self.assertFalse(p.drained())
                while p.ck < d["tras_min"]:
                    sample()
                pre = p.ck
                sample(commands.maintenance("PRE", all_banks=kind == "PREab"))
                end = max(pre + d["trp_ab" if kind == "PREab" else "trp_pb"], d["trc"]) + delay
            elif kind == "FSP":
                for ca in commands.mode_write(16, 0):
                    sample(ca)
                end = 1 + delay + d["tfc"]
            else:
                ca = commands.pack(112, 6) if kind == "ZQ" else commands.maintenance(kind)
                sample(ca)
                end = delay + d["tzqlat" if kind == "ZQ" else "trfc_ab" if kind == "REF" else "trfm_ab"]
            while p.phy.dram.last_ck < end - 1:
                sample()
            self.assertEqual(p.phy.dram.last_ck, end - 1)
            self.assertEqual(p.row_recovery()["open_banks"], [])
            self.assertFalse(p.drained(), kind)
            before = p.row_recovery()
            with self.assertRaisesRegex(Violation, "profile change before independent") as caught:
                p.profile(bindings()[1])
            REJECTIONS.append(dict(test=self.id(), recovery=kind, observed=str(caught.exception)))
            sample()
            self.assertEqual(p.phy.dram.last_ck, end)
            self.assertTrue(p.drained(), kind)
            LEGAL_PATHS.append(dict(path=kind + " recovery boundary", before=before, at=p.row_recovery()))
            p.profile(bindings()[1])

    def test_write_pin_and_dfi_flush_have_independent_origins(self):
        for profile in bindings():
            for delay in (0, profile["dfi_wck"]["twrdata_delay"]):
                profile = copy.deepcopy(profile)
                profile["dfi_wck"]["twrdata_delay"] = delay
                p = PhysicalStream(profile)
                for ck, ca in enumerate(commands.activate(0, 0)):
                    p.phy.command(ck, 1, ca)
                at = profile["dram_ck"]["trcd"] + 2
                p.phy.command(at - 1, 1, commands.cas("write"))
                burst = p.phy.command(at, 1, commands.access(True, 32, 0, 0))
                q = granule("inline_physical", write=1)
                p.expect(q)
                t = p.requests[0]
                p.add(burst, at, 0, t)
                r, d = profile["ratio"], profile["dfi_wck"]
                span = 24 if r == 4 else 16
                pin_end = (burst.times[-1] + 1 + 2 * r - 1) // (2 * r)
                dfi_end = (at * r + max(d["tphy_wrcslat"], d["tphy_wrlat"] + d["tphy_wrdata"]) + span + r - 1) // r
                flush_end = (at * r + d["tphy_wrlat"] + d["tphy_wrdata"] + span + delay + r - 1) // r
                expected = max(pin_end, dfi_end, flush_end)
                self.assertEqual(t["final"], expected)
                # Isolate readiness from payload capture; separate fixtures
                # check every actual write byte and command/data identity.
                t.update(bytes=64, seen=set(range(64)))
                p.ck = expected
                with self.assertRaisesRegex(Violation, "before independent physical") as caught:
                    p.ready(t)
                REJECTIONS.append(dict(test=self.id(), ratio=r, delay=delay, observed=str(caught.exception)))
                p.ck += 1  # sample(expected) has completed before its later edge
                p.ready(t)
                t["seen"].remove(63)
                with self.assertRaisesRegex(Violation, "before independent physical") as caught:
                    p.ready(t)
                REJECTIONS.append(dict(test=self.id(), ratio=r, delay=delay, observed=str(caught.exception)))
                LEGAL_PATHS.append(
                    dict(
                        path="independent write tails", ratio=r, delay=delay, pin=pin_end, dfi=dfi_end, flush=flush_end
                    )
                )

    def test_legal_boot_and_master_self_refresh_handback(self):
        f = Fixture(3).dirty()
        c = Case(dict(case_id=6, management=3, failed=0), bindings())
        for e, h, events in f.frames:
            c.sample(e)
            c.events(h, events)
        self.assertEqual(f.frames[1][0]["owner"], 1)  # boot has no MC CA
        samples = []

        def sample(**changes):
            e = dict(c.last)
            e.update(tick=e["tick"] + 1, cycles=e["cycles"] + 1, time_ps=e["time_ps"] + 5000, cs=0, ca=0)
            e.update(changes)
            c.sample(e)
            samples.append(e)

        # Legal SRE precedes exclusive ACK; SRX follows the registered return.
        sample(owner=3, phymstr_req=1, cs=1, ca=commands.pack(104, 0))
        for _ in range(12):
            sample()
        sample(owner=1, master=1)
        sample(rank_valid=0)
        sample(rank_valid=1, rank_sr=1)
        sample(phymstr_req=0)
        sample(master=0, owner=3)
        sample(cs=1, ca=commands.pack(40, 0))
        self.assertTrue(c.master_withdraw)
        self.assertFalse(c.physical.row_recovery()["ready"])
        while not c.physical.drained():
            sample(owner=2)
        c.physical.summary()
        LEGAL_PATHS.append(dict(path="boot and self-refresh master handback CA", samples=samples))

    def test_fault_held_ack_and_reserved_fsp_pair_do_not_cancel(self):
        # FAILED ownership can coexist with a held external ACK. A fault is
        # neither a healthy ownership return nor cancellation of a CA pair.
        for kind in (0, 1, 3):
            f = Fixture(kind).dirty()
            c = Case(dict(case_id=kind * 2, management=kind, failed=0), bindings())
            for e, h, events in f.frames:
                c.sample(e)
                c.events(h, events)
                if kind == 1 and e["cs"] and decode(e["ca"]).kind == "MRW1":
                    break
                if kind != 1 and e["update" if kind == 0 else "master"]:
                    break
            physical = c.physical
            fault = dict(
                c.last,
                tick=c.last["tick"] + 1,
                cycles=c.last["cycles"] + 1,
                time_ps=c.last["time_ps"] + 5000,
                owner=4,
                trusted=0,
                fault=1,
                cs=0,
                ca=0,
            )
            if kind == 1:
                fault.update(cs=1, ca=commands.mode_write(1, c.profiles[0]["mr"]["1"])[1])
            c.sample(fault)
            self.assertIs(c.physical, physical)
            self.assertEqual(c.physical.phy.dram.pending, None)
            self.assertEqual(len(c.reset_intervals), 1)  # initial cold reset only
            LEGAL_PATHS.append(dict(path="fault holds ACK/finishes reserved pair", kind=kind, sample=fault))

    def test_fresh_recovery_and_stale_configuration_training(self):
        f = Fixture(0, 1).dirty()
        c = Case(dict(case_id=1, management=0, failed=1), bindings())
        for e, h, events in f.frames:
            c.sample(e)
            c.events(h, events)

        def next_sample(**changes):
            e = dict(c.last)
            e.update(tick=e["tick"] + 1, cycles=e["cycles"] + 1, time_ps=e["time_ps"] + 5000)
            e.update(changes)
            return e

        for changes in (
            dict(config_valid=1, config=1),
            dict(train_ack=1, train_token=0),
            dict(trusted=1, trained=1, config_valid=1, config=1),
            dict(boot=0),
        ):
            bad = copy.deepcopy(c)
            with self.assertRaises(Violation) as caught:
                bad.sample(next_sample(**changes))
            REJECTIONS.append(dict(test=self.id(), observed=str(caught.exception)))
        c.sample(next_sample(config_commit=1, prepared=1, prepared_epoch=1))
        c.sample(next_sample(config_commit=0, train_ack=1, train_token=1, training_epoch=1))
        c.sample(next_sample(train_ack=0, trusted=1, trained=1, config_valid=1, config=1, owner=2))
        self.assertTrue(c.last["trusted"])

    def test_original_offer_before_physical_completion_is_rejected(self):
        def early(t):
            admit = next(i for i, e in enumerate(t) if e["kind"] == "original_admit")
            q = next(e for e in t if e["kind"] == "original_offer")
            t[admit - 1]["offer"] = 1
            t.insert(admit + 2, dict(q, tick=t[admit]["tick"], case_id=0))

        self.reject(early, "inline success before")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    args, remaining = parser.parse_known_args()
    if args.report and args.report.exists():
        parser.error("refuse to overwrite observer unit evidence")
    run = unittest.main(argv=[__file__, *remaining], exit=False, verbosity=2)
    if args.report:
        raw = args.report.with_suffix(".fixtures.jsonl")
        if raw.exists():
            raise SystemExit("refuse to overwrite synthetic fixture")
        raw.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in ObservationTests.events))
        result = dict(
            status="passed" if run.result.wasSuccessful() else "failed",
            tests=run.result.testsRun,
            negative_cases=REJECTIONS,
            legal_paths=LEGAL_PATHS,
            synthetic_fixture_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
            fixture=raw.name,
            limitations=["Synthetic parser/adapter fixtures only; no hardware simulation."],
        )
        if run.result.wasSuccessful():
            result["synthetic_replay"] = check(ObservationTests.events)
        args.report.write_text(json.dumps(result, indent=2) + "\n")
    raise SystemExit(not run.result.wasSuccessful())
