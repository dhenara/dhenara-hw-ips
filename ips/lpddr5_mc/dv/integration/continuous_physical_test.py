"""Synthetic C1 adapter sensitivity; no HDL or retained RTL trace is executed."""

import copy
import unittest

from continuous_physical import PHASE, RECEIVING_PHASE, Replay, binding_digest, check
from continuous_test import PROFILE, sample
from observations_test import Fixture
from system.host import Violation
from system.phy import DigitalPhy


def fixture():
    f = Fixture()
    # Use the actual C1 numeric image, not the C2 fixture's DBI/always-on image.
    f.p = copy.deepcopy(PROFILE)
    f.p["device"]["mr57"] = 3
    f.p["ranks"] = 1
    f.gen = DigitalPhy(f.p)
    return f


def records(f):
    events = [
        dict(
            kind="header",
            version=2,
            scope="inline_lifecycle",
            phase=PHASE,
            receiving_phase=RECEIVING_PHASE,
            channels=1,
            ranks=1,
            profile_number=0,
            profile_sha256=binding_digest(f.p),
            external_flips=1,
        )
    ]
    for e, _, qs in f.frames:
        ck = e["tick"]
        s = sample(ck)
        s.update(e)
        s.update(
            kind="transition",
            ck=ck,
            channel=0,
            ps=625 + ck * 1250,
            inline_dirty=e["data_committed"],
            fsp_busy=e["fsp_busy"],
        )
        if ck:
            q = {
                k: s[k]
                for k in (
                    "ck",
                    "ps",
                    "channel",
                    "reset_n",
                    "destructive",
                    "cs",
                    "ca",
                    "wck",
                    "write",
                    "read",
                    "return",
                    "mr4",
                    "mr43",
                )
            }
            q.update(kind="prepared", ps=s["ps"] - 625, reset_applied=int(not s["reset_n"] or bool(s["destructive"])))
            events.append(q)
        events.append(s)
        events.extend(dict(q, ck=ck, channel=0) for q in qs if q["kind"].startswith("inline_"))
    events.append(dict(kind="end", ck=len(f.frames), physical_closed=1))
    return events


def physical(events, p):
    c = Replay(p)
    for e in events[1:]:
        if e["kind"] == "prepared":
            c.prepare(e)
        elif e["kind"] == "transition":
            c.sample(e)
        elif e["kind"] == "memory_flip":
            c.flip(e)
        elif e["kind"] == "end":
            return c.finish(e["ck"])
        else:
            c.event(e)
    raise AssertionError("test stream has no end")


class Tests(unittest.TestCase):
    def transfer(self, *, write=False):
        f = fixture()
        f.transfer(0, int(write), bytes(64), 1)
        return f, records(f)

    def reject(self, events, p, text):
        with self.assertRaisesRegex((Violation, ValueError), text):
            physical(events, p)

    def test_complete_inline_c1_numeric_image(self):
        f = fixture().dirty()
        # Record the fixture's one explicit external fault in an idle tail.
        f.emit()
        events = records(f)
        at = next(i for i, e in enumerate(events) if e["kind"] == "prepared" and e["ck"] == len(f.frames) - 1)
        events.insert(
            at,
            dict(
                kind="memory_flip",
                id=0,
                ck=len(f.frames) - 1,
                ps=1250 * (len(f.frames) - 1),
                channel=0,
                rank=0,
                address=2,
                mask=128,
            ),
        )
        r = check(events, f.p)
        self.assertEqual(r["structure"][0]["inline_completed"], 1)
        interval = r["physical_channels"][0]["intervals"][0]
        self.assertEqual(interval["write_words"], 32)
        self.assertEqual(interval["returned_slices"], 64)
        self.assertIsNone(interval["cancelled"])
        for key in ("profile_sha256", "phase", "receiving_phase"):
            mutant = copy.deepcopy(events)
            mutant[0][key] = "wrong"
            with self.subTest(header=key), self.assertRaisesRegex(Violation, "header/phase|numeric profile"):
                check(mutant, f.p)
        mutant = [e for e in events if e["kind"] != "memory_flip"]
        with self.assertRaisesRegex(Violation, "external memory flip"):
            check(mutant, f.p)

    def test_fault_retains_inflight_return_and_physical_owner(self):
        f, events = self.transfer()
        first = next(e["ck"] for e in events if e["kind"] == "transition" and e["return"])
        for e in events:
            if e["kind"] == "transition" and e["ck"] >= first - 1:
                e.update(fault=1, trusted=0, owner=4, memory_reset=1)
        r = physical(events, f.p)
        self.assertEqual(len(r["intervals"]), 1)
        self.assertEqual(r["intervals"][0]["returned_slices"], 32)
        self.assertTrue(r["intervals"][0]["retention_suspensions"])
        self.assertIsNone(r["intervals"][0]["cancelled"])
        mutant = copy.deepcopy(events)
        for e in mutant:
            if e.get("ck") == first and e["kind"] in ("transition", "prepared"):
                e["return"] = 0
        self.reject(mutant, f.p, "return|physical data")

    def test_fault_does_not_cancel_inflight_write(self):
        f, events = self.transfer(write=True)
        first = next(e["ck"] for e in events if e["kind"] == "transition" and e["write"] >> 24 & 255)
        for e in events:
            if e["kind"] == "transition" and e["ck"] >= first - 1:
                e.update(fault=1, trusted=0, owner=4, memory_reset=1)
        r = physical(events, f.p)
        self.assertEqual(r["intervals"][0]["write_words"], 16)
        mutant = copy.deepcopy(events)
        enable = next(e for e in events if e["kind"] == "transition" and e["ck"] == first)
        lane = next(i for i in range(f.p["ratio"]) if enable["write"] >> (24 + 2 * i) & 3)
        word = (first - 1) * f.p["ratio"] + lane + f.p["dfi_wck"]["tphy_wrdata"]
        word_ck, word_lane = word // f.p["ratio"] + 1, word % f.p["ratio"]
        for e in mutant:
            if e.get("ck") == word_ck and e["kind"] in ("transition", "prepared"):
                e["write"] ^= 1 << (48 + 32 * word_lane)
        self.reject(mutant, f.p, "pin bytes|write")

    def test_phase_missing_duplicate_and_propagation(self):
        f, events = self.transfer()
        index = next(i for i, e in enumerate(events) if e["kind"] == "prepared")
        mutant = copy.deepcopy(events)
        mutant[index]["ps"] += 1
        self.reject(mutant, f.p, "preparation phase")
        self.reject(events[:index] + events[index + 1 :], f.p, "missing continuous memory")
        self.reject(events[:index] + [events[index]] + events[index:], f.p, "duplicate continuous memory")
        mutant = copy.deepcopy(events)
        mutant[index]["return"] ^= 1 << 32
        self.reject(mutant, f.p, "bus mismatch")
        for key in ("mr4", "mr43", "reset_applied"):
            mutant = copy.deepcopy(events)
            del mutant[index][key]
            with self.subTest(missing=key):
                self.reject(mutant, f.p, f"missing fields.*{key}")

    def test_read_result_must_match_independent_memory(self):
        f, events = self.transfer()
        mutant = copy.deepcopy(events)
        next(e for e in mutant if e["kind"] == "inline_result")["data"] = 1
        self.reject(mutant, f.p, "independent returned slices")
        first = next(e["ck"] for e in events if e["kind"] == "transition" and e["return"])
        for e in events:
            if e.get("ck") == first and e["kind"] in ("transition", "prepared"):
                e["return"] ^= 1 << 40
        self.reject(events, f.p, "independent physical memory")

    def test_explicit_flip_changes_independent_memory_and_is_sensitive(self):
        f = fixture()
        f.transfer(0, 1, bytes(64), 1)
        flip_ck = len(f.frames)
        f.gen.dram.memory[(0, 0, 0, 2)] ^= 128
        value = bytearray(64)
        value[2] = 128
        f.transfer(0, 0, value, 2)
        events = records(f)
        index = next(i for i, e in enumerate(events) if e["kind"] == "prepared" and e["ck"] == flip_ck)
        flip = dict(kind="memory_flip", id=0, ck=flip_ck, ps=1250 * flip_ck, channel=0, rank=0, address=2, mask=128)
        events.insert(index, flip)
        r = physical(events, f.p)
        self.assertEqual(len(r["external_memory_flips"]), 1)
        self.assertEqual(r["intervals"][0]["returned_slices"], 32)
        self.reject(events[:index] + events[index + 1 :], f.p, "independent physical memory")
        self.reject(events[:index] + [flip] + events[index:], f.p, "duplicate external memory flip")
        mutant = copy.deepcopy(events)
        mutant[index]["mask"] = 64
        self.reject(mutant, f.p, "independent physical memory")

    def test_fault_keeps_memory_rows_raa_and_exact_owner(self):
        f, events = self.transfer(write=True)
        c = Replay(f.p)
        witnessed = False
        for e in events[1:]:
            if e["kind"] == "prepared":
                c.prepare(e)
            elif e["kind"] == "transition":
                if c.physical is not None and not witnessed and c.physical.words:
                    owner = c.physical
                    memory = dict(owner.phy.dram.memory)
                    raa = copy.deepcopy(owner.phy.dram.maintenance.raa)
                    banks = owner.phy.dram.banks
                    e = dict(e, trusted=0, fault=1, memory_reset=1)
                    c.sample(e)
                    self.assertIs(c.physical, owner)
                    self.assertIs(c.physical.phy.dram.banks, banks)
                    self.assertEqual(c.physical.phy.dram.maintenance.raa, raa)
                    self.assertTrue(memory.items() <= c.physical.phy.dram.memory.items())
                    witnessed = True
                    break
                c.sample(e)
            elif e["kind"].startswith("inline_"):
                c.event(e)
        self.assertTrue(witnessed)

    def test_unconfigured_ratio_zero_is_idle_with_one_physical_owner(self):
        f, events = self.transfer()
        c = Replay(f.p)
        owner = None
        for e in events[1:]:
            if e["kind"] == "prepared":
                c.prepare(e)
            elif e["kind"] == "transition":
                if not e["config_valid"]:
                    e["ratio"] = 0
                c.sample(e)
                if c.physical is not None:
                    if owner is None:
                        owner = c.physical
                    self.assertIs(c.physical, owner)
            elif e["kind"] == "end":
                c.finish(e["ck"])
            else:
                c.event(e)
        mutant = copy.deepcopy(events)
        for e in mutant:
            if e.get("ck") == 2 and e["kind"] in ("prepared", "transition"):
                e["cs"] = 1
        self.reject(mutant, f.p, "unconfigured physical startup")

    def test_last_cold_preparation_can_precede_reset_release(self):
        f, events = self.transfer()
        q = next(e for e in events if e["kind"] == "prepared")
        q.update(reset_n=0, reset_applied=1)
        self.assertEqual(physical(events, f.p)["intervals"][0]["returned_slices"], 32)
        mutant = copy.deepcopy(events)
        next(e for e in mutant if e["kind"] == "transition" and e["ck"] == 1)["config_valid"] = 1
        self.reject(mutant, f.p, "cold reset release must remain idle")

    def test_actual_reset_requires_confirmation_and_consumption(self):
        f, events = self.transfer()
        index = next(i for i, e in enumerate(events) if e["kind"] == "prepared" and e["ck"] > 5)
        mutant = copy.deepcopy(events)
        mutant[index]["reset_applied"] = 1
        self.reject(mutant, f.p, "without destructive consumption")
        for e in mutant:
            if e.get("ck") == mutant[index]["ck"] and e["kind"] in ("transition", "prepared"):
                e["destructive"] = 1
                e["reset_applied"] = 1
                e["confirmed"] = 0
        self.reject(mutant, f.p, "settlement/confirmation")

    def test_destructive_reset_inventories_pending_returns(self):
        f, events = self.transfer()
        first = next(e["ck"] for e in events if e["kind"] == "transition" and e["return"])
        # Keep the complete prefix through decoded READ, then witness the actual
        # destructive reset and its next-edge epoch invalidation.
        events = [e for e in events if e.get("ck", -1) < first or e["kind"] == "header"]
        prev = next(e for e in reversed(events) if e["kind"] == "transition")
        for ck, destructive in ((first, 1), (first + 1, 0)):
            s = dict(
                prev,
                ck=ck,
                ps=625 + ck * 1250,
                destructive=destructive,
                trusted=0,
                trained=0,
                config_valid=0,
                enabled=0,
                settled=1,
                boot=1,
                cs=0,
                ca=0,
                wck=0,
                write=0,
                read=0,
                **{"return": 0},
            )
            q = {
                k: s[k]
                for k in ("ck", "ps", "channel", "reset_n", "destructive", "cs", "ca", "wck", "write", "read", "return")
            }
            q.update(kind="prepared", ps=s["ps"] - 625, reset_applied=destructive, mr4=9, mr43=0)
            events.extend((q, s))
        events.append(dict(kind="end", ck=first + 2))
        r = physical(events, f.p)
        self.assertEqual(r["intervals"][0]["cancelled"]["requests"], 1)
        self.assertGreater(r["intervals"][0]["cancelled"]["return_slices"], 0)
        mutant = copy.deepcopy(events)
        next(e for e in reversed(mutant) if e["kind"] == "transition")["boot"] = 0
        self.reject(mutant, f.p, "next-edge epoch invalidation")

    def test_stale_epoch_trust_restore_and_truncated_tail(self):
        f, events = self.transfer()
        mutant = copy.deepcopy(events)
        next(e for e in mutant if e["kind"] == "inline_physical")["boot"] = 7
        self.reject(mutant, f.p, "stale physical request epoch")
        mutant = copy.deepcopy(events)
        samples = [e for e in mutant if e["kind"] == "transition"]
        samples[-3].update(trusted=0, fault=1)
        self.reject(mutant, f.p, "trust restored without destructive")
        first = next(e["ck"] for e in events if e["kind"] == "transition" and e["return"])
        mutant = [e for e in events if e.get("ck", -1) < first or e["kind"] == "header"]
        mutant.append(dict(kind="end", ck=first))
        self.reject(mutant, f.p, "unsettled physical request tail")


if __name__ == "__main__":
    unittest.main()
