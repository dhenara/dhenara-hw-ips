"""Synthetic sensitivity only; no connected HDL outcome is established."""

import copy
import json
import unittest
from pathlib import Path

from continuous_physical import Replay
from continuous_physical_test import fixture, records
from contracts import commands
from ecc_boundary import CK_FS, boundary, host, physical
from system.host import Violation
from system.phy import DigitalPhy
from wck_observations_test import fixture as wck_fixture

IP = Path(__file__).resolve().parents[2]


class BoundaryTests(unittest.TestCase):
    def case(self, kind=0, delta=0, posted=0):
        anchor = 20 * CK_FS
        h = dict(version=1, boundary=kind, delta_ck=delta, posted=posted, posted_early=0, epoch_posted=0, ck_fs=CK_FS)
        m = dict(
            held=dict(fs=CK_FS),
            settled=dict(fs=40 * CK_FS),
            inject=dict(fs=anchor + delta * CK_FS - int(kind == 1), anchor_fs=anchor),
        )
        edges = [
            dict(
                fs=anchor,
                channel=0,
                armed=1,
                epoch_result=1,
                epoch_failed=1,
                epoch_value=1,
                fatal=1,
                error=1,
                external_fault=1,
                late_error=1,
                posted=posted,
                host_posted=posted,
                posted_early=0,
                epoch_posted=0,
            ),
            dict(
                fs=anchor + 2 * CK_FS,
                channel=0,
                armed=1,
                epoch_result=0,
                epoch_failed=0,
                epoch_value=0,
                fatal=1,
                error=1,
                external_fault=1,
                late_error=1,
                posted=posted,
                host_posted=posted,
                posted_early=0,
                epoch_posted=0,
            ),
        ]
        field = "external_fault" if kind else "error"
        for edge in edges:
            edge[field] = int(edge["fs"] >= m["inject"]["fs"])
        return h, m, edges, dict(pin_fs=anchor, mr43_ready_fs=anchor - CK_FS)

    def test_strict_and_admitted_posted_before_at_after_both_boundaries(self):
        for kind in (0, 1):
            for delta in (-1, 0, 1):
                for posted in (0, 1):
                    with self.subTest(kind=kind, delta=delta, posted=posted):
                        h, m, e, kw = self.case(kind, delta, posted)
                        boundary(h, m, e, **kw)

    def test_injection_shift_and_guessed_pin_anchor_reject(self):
        h, m, e, kw = self.case()
        m["inject"]["fs"] += 1
        with self.assertRaisesRegex(Violation, "injection phase"):
            boundary(h, m, e, **kw)
        m["inject"]["fs"] -= 1
        kw["pin_fs"] += 1
        with self.assertRaisesRegex(Violation, "observed boundary"):
            boundary(h, m, e, **kw)

    def test_lost_mr43_return_or_early_result_reject(self):
        h, m, e, kw = self.case(1)
        for ready in (None, 21 * CK_FS):
            with self.subTest(ready=ready), self.assertRaisesRegex(Violation, "independent return"):
                boundary(h, m, e, **dict(kw, mr43_ready_fs=ready))
        e[0]["epoch_value"] = 0
        with self.assertRaisesRegex(Violation, "failed MR43"):
            boundary(h, m, e, **kw)

    def test_early_posting_and_wrong_installed_configuration_reject(self):
        h, m, e, kw = self.case(posted=1)
        h["posted_early"] = 1
        with self.assertRaisesRegex(Violation, "early posting"):
            boundary(h, m, e, **kw)
        h["posted_early"] = 0
        e[0]["host_posted"] = 0
        with self.assertRaisesRegex(Violation, "both owners"):
            boundary(h, m, e, **kw)

    def test_missing_error_input_or_late_status_reject(self):
        for field in ("error", "fatal", "late_error"):
            h, m, e, kw = self.case()
            for edge in e:
                edge[field] = 0
            with self.subTest(field=field), self.assertRaises(Violation):
                boundary(h, m, e, **kw)
        h, m, e, kw = self.case(delta=1)
        e[0]["error"] = 1
        with self.assertRaisesRegex(Violation, "before injection"):
            boundary(h, m, e, **kw)


class PhysicalLinkTests(unittest.TestCase):
    def stream(self, *, mr43=None):
        p = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"][8]
        p = copy.deepcopy(p)
        p["ranks"] = 1
        p["device"]["mr57"] = 3
        rows = json.loads((IP / "config/reference/link_ecc_matrix.json").read_text())
        f = fixture()
        f.p = p
        f.gen = DigitalPhy(p, rows)
        # All-zero data has independently valid zero data/DMI parity. This
        # exercises the real matrix receiver without altering the fixture.
        f.transfer(0, 1, bytes(64), 1)
        if mr43 is not None:
            # Delay the backend result until a separate complete raw MRR43
            # and WCK/recovery tail. This synthetic stream is not an RTL run.
            final_result = f.frames[-1][2].pop()
            rows, _, _ = wck_fixture(p, False, beats=16, mrr=True)
            start = len(f.frames) - 1
            origin = start + 6
            pending = list(range(8))
            pointer = 0
            for index, (_, cs, ca, wck) in enumerate(rows + [[0, 0, 0, 0]] * 70):
                if cs and ca == commands.mode_read(4):
                    ca = commands.mode_read(43)
                f.emit(cs=cs, ca=ca, wck=wck, mr43=mr43)
                e = f.frames[-1][0]
                ck = start + index
                read_cs = enable = 0
                for lane in range(p["ratio"]):
                    at = ck * p["ratio"] + lane
                    begin = origin * p["ratio"] + p["dfi_wck"]["tphy_rdcslat"]
                    if begin <= at < begin + 8:
                        read_cs |= 5 << (lane * 4)
                    if (
                        origin * p["ratio"] + p["dfi_wck"]["trddata_en"]
                        <= at
                        < origin * p["ratio"] + p["dfi_wck"]["trddata_en"] + 8
                    ):
                        enable |= 3 << (lane * 2)
                e["read"] = enable << 16 | read_cs
                valid = value = count = 0
                while pending and count < p["ratio"]:
                    word = pending[0]
                    last_half = origin * 8 + p["phy_half_wck"]["command"] + p["rl_ck"] * 8
                    last_half += p["phy_half_wck"]["dq_output"] + p["phy_half_wck"]["read"] + 2 * word + 1
                    if (last_half + 7) // 8 > ck:
                        break
                    pending.pop(0)
                    raw = 0xDA00DA00 | mr43 | mr43 << 16 if word < 4 else 0xDADADADA
                    lane = (pointer + count) % p["ratio"]
                    valid |= 3 << (lane * 2)
                    value |= raw << (lane * 32)
                    count += 1
                pointer = (pointer + count) % p["ratio"]
                e["return"] = value << 40 | valid << 32
            self.assertFalse(pending)
            f.emit(events=[final_result], mr43=mr43)
        return p, records(f)

    def replay(self, p, events, *, results=False):
        c = Replay(p)
        for e in events[1:]:
            if e["kind"] == "prepared":
                c.prepare(e)
            elif e["kind"] == "transition":
                c.sample(e)
            elif e["kind"] == "inline_result" and not results:
                continue
            elif e["kind"] != "end":
                c.event(e)
        return c

    def test_actual_matrix_write_words_are_checked(self):
        p, events = self.stream()
        c = self.replay(p, events)
        self.assertEqual(c.physical.words, 16)
        self.assertTrue(c.physical.phy.write_status)
        self.assertTrue(all(v == "clean" for x in c.physical.phy.write_status.values() for v in x))

    def test_corrupted_active_parity_is_rejected_even_when_correctable(self):
        p, events = self.stream()
        tick = next(e["ck"] for e in events if e["kind"] == "prepared" and int(e["write"]) >> 24 & 255)
        # tphy_wrdata can move the data lane into a later CK.
        r, delay = p["ratio"], p["dfi_wck"]["tphy_wrdata"]
        q = next(e for e in events if e["kind"] == "prepared" and e["ck"] == tick)
        lane = next(i for i in range(r) if int(q["write"]) >> (24 + 2 * i) & 3)
        target = tick + (lane + delay) // r
        bit = 4 * ((lane + delay) % r) + 2
        for e in events:
            if e.get("ck") == target and e["kind"] in ("prepared", "transition"):
                e["write"] = int(e["write"]) ^ (1 << bit)
        with self.assertRaisesRegex(Violation, "outgoing write-link parity"):
            self.replay(p, events)

    def test_internal_success_without_raw_mr43_cannot_close_write(self):
        p, events = self.stream()
        with self.assertRaisesRegex(Violation, "clean MR43 epoch"):
            self.replay(p, events, results=True)

    def test_complete_clean_raw_mr43_closes_write_and_bad_status_cannot(self):
        p, events = self.stream(mr43=0)
        c = self.replay(p, events, results=True)
        self.assertFalse(c.physical.requests)
        self.assertEqual(c.physical.slices, 16)
        p, events = self.stream(mr43=1)
        with self.assertRaisesRegex(Violation, "clean MR43 epoch"):
            self.replay(p, events, results=True)

    def test_mr43_tail_remains_independently_required(self):
        p, events = self.stream(mr43=0)
        last_return = max(e["ck"] for e in events if e["kind"] == "transition" and int(e["return"]) >> 32 & 255)
        result = next(e for e in events if e["kind"] == "inline_result")
        result = dict(result, ck=last_return)
        index = next(i for i, e in enumerate(events) if e["kind"] == "transition" and e["ck"] == last_return)
        events = [e for e in events if e["kind"] != "inline_result"]
        events.insert(index + 1, result)
        with self.assertRaisesRegex(Violation, "MR43 return/recovery/WCK tail"):
            self.replay(p, events, results=True)

    def test_records_after_physical_end_are_rejected(self):
        p, events = self.stream()
        with self.assertRaisesRegex(Violation, "records after end"):
            physical([*events, events[-2]], p, 0)


class HostTests(unittest.TestCase):
    def stream(self):
        """Synthetic source/inline/control only; physical validation is separate."""
        source = [dict(kind="header", version=1, ports=2, channels=2, bus_bytes=16)]
        inline, controls, times = [], [], {}
        ps = 0

        def emit(kind, port, **fields):
            nonlocal ps
            ps += 10
            if kind in ("b", "r"):
                source.append(dict(kind="axi", ps=ps, port=port, flags=8 if kind == "b" else 2))
            source.append(dict(kind=kind, ps=ps, port=port, **fields))

        def aw(port, identity, address, words=4):
            emit("aw", port, id=identity, address=address, length=3, size=4, burst=1, lock=0)
            for word in range(words):
                emit("w", port, data=0, strobe=65535, last=int(word == 3))

        def operation(channel, address, tag, *, write=1, failed=0):
            nonlocal ps
            local = (address & 63) | ((address >> 7) << 6)
            q = dict(
                channel=channel,
                ck=tag,
                tag=tag,
                generation=0,
                boot=0,
                config=0,
                rank=0,
                row=local >> 15,
                bank=local >> 11 & 15,
                column=local >> 5 & 63,
                write=write,
                bytes=64,
                mask=(1 << 64) - 1,
                data=0,
            )
            ps += 10
            times[channel, tag] = ps
            inline.append(dict(q, kind="inline_admit"))
            inline.append(dict(q, kind="inline_complete", failed=failed, physical=1, integrity=1))

        aw(1, 108, 64)
        operation(1, 64, 0)
        emit("b", 1, id=108, response=0, ready=0)
        aw(0, 110, 512)
        aw(0, 112, 768, words=1)
        controls.append(dict(kind="mark", name="inject", fs=ps * 1000 + 1))
        ps += 10
        controls.append(dict(kind="edge", fs=ps * 1000, channel=0, fatal=1))
        operation(0, 512, 1, failed=1)
        for word in range(1, 4):
            emit("w", 0, data=0, strobe=65535, last=int(word == 3))
        emit("b", 1, id=108, response=0, ready=1)
        emit("b", 0, id=110, response=2, ready=1)
        emit("b", 0, id=112, response=2, ready=1)
        controls.append(dict(kind="mark", name="settled", fs=ps * 1000 + 1))
        ps += 10
        times[0, 2] = ps
        inline.append(dict(kind="transition", channel=0, ck=2, destructive=1))
        controls.append(dict(kind="mark", name="fresh", fs=ps * 1000 + 1))
        aw(0, 120, 0)
        operation(0, 0, 3)
        emit("b", 0, id=120, response=0, ready=1)
        emit("ar", 1, id=121, address=0, length=3, size=4, burst=1, lock=0)
        operation(0, 0, 4, write=0)
        for word in range(4):
            emit("r", 1, id=121, data=0, response=0, ready=1, last=int(word == 3))
        source.append(dict(kind="end", ps=ps + 10))
        return source, inline, controls, times

    def test_held_b_partial_w_abort_settlement_and_fresh_service(self):
        result = host(*self.stream())
        self.assertEqual(result["counts"]["b"], 4)
        self.assertEqual(result["late_errors"], 1)

    def test_held_offered_b_status_cannot_change(self):
        args = self.stream()
        next(e for e in args[0] if e["kind"] == "b" and e["id"] == 108 and e["ready"])["response"] = 2
        with self.assertRaisesRegex(Violation, "payload changed"):
            host(*args)

    def test_unoffered_work_cannot_succeed_after_fault(self):
        args = self.stream()
        next(e for e in args[0] if e["kind"] == "b" and e["id"] == 110)["response"] = 0
        with self.assertRaisesRegex(Violation, "response code"):
            host(*args)

    def test_missing_trailing_w_cannot_retire_b_or_reset(self):
        args = list(self.stream())
        dropped = next(e for e in args[0] if e["kind"] == "w" and e["last"] and e["ps"] > args[2][0]["fs"] // 1000)
        args[0].remove(dropped)
        with self.assertRaisesRegex(Violation, "B before accepted W"):
            host(*args)

    def test_premature_destructive_reset_rejects(self):
        args = self.stream()
        args[3][0, 2] = args[2][0]["fs"] // 1000 + 1
        with self.assertRaisesRegex(Violation, "reset before real AXI settlement"):
            host(*args)

    def test_injection_requires_real_held_b_and_accepted_partial_w(self):
        args = self.stream()
        next(e for e in args[0] if e["kind"] == "b" and not e["ready"])["ready"] = 1
        with self.assertRaisesRegex(Violation, "three actual original"):
            host(*args)
        args = list(self.stream())
        partial = next(e for e in args[0] if e["kind"] == "w" and e["ps"] == args[2][0]["fs"] // 1000)
        args[0].remove(partial)
        with self.assertRaisesRegex(Violation, "trailing W obligation"):
            host(*args)

    def test_response_without_source_edge_valid_rejects(self):
        args = self.stream()
        next(e for e in args[0] if e["kind"] == "axi")["flags"] = 0
        with self.assertRaisesRegex(Violation, "AXI edge VALID"):
            host(*args)

    def test_fresh_read_data_and_source_end_are_checked(self):
        args = self.stream()
        next(e for e in args[0] if e["kind"] == "r")["data"] = 1
        with self.assertRaisesRegex(Violation, "R data"):
            host(*args)
        args = self.stream()
        args[0].append(dict(kind="w", ps=1000, port=0, data=0, strobe=65535, last=1))
        with self.assertRaisesRegex(Violation, "records after end"):
            host(*args)
