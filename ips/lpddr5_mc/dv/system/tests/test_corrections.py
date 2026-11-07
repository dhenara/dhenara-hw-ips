"""Owning regressions for review counterexamples and reusable live seams."""

import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path

from contracts import commands
from contracts.integrity import transport
from system.control import ManagementPort, ResponseScript
from system.dram import DramModel
from system.host import Address, HostScoreboard, Violation
from system.merge import Context, MergeMonitor
from system.ownership import Identity, LifetimeMonitor
from system.phy import DigitalPhy
from system.stream import StreamMonitor

IP = Path(__file__).resolve().parents[3]
PROFILES = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"]
ROWS = json.loads((IP / "config/reference/link_ecc_matrix.json").read_text())


def activate(m, at=0, bank=0, rank=0):
    a, b = commands.activate(0, bank)
    m.command(at, rank + 1, a)
    m.command(at + 1, rank + 1, b)


def fill(m, b):
    for i, t in enumerate(b.times):
        m.beat(b, i, t, 0 if b.write else None)


class TimingCorrections(unittest.TestCase):
    def test_review_self_refresh_and_exit_boundary(self):
        for p in PROFILES:
            for offset in (-1, 0):
                m = DramModel(p)
                m.command(0, 1, commands.pack(104, 0))
                with self.assertRaisesRegex(Violation, "self-refresh"):
                    activate(copy.deepcopy(m), 1)
                m.command(20, 1, commands.pack(40, 0))
                at = 20 + p["dram_ck"]["txsr"] + offset
                if offset < 0:
                    with self.assertRaisesRegex(Violation, "rank recovery"):
                        activate(m, at)
                else:
                    activate(m, at)
                    m.finish()

    def test_review_pb_refresh_and_other_bank_act_boundaries(self):
        for field, kind in [("tpbr2pbr", "REF"), ("tpbr2act", "ACT")]:
            for offset in (-1, 0):
                m = DramModel(PROFILES[0])
                m.command(0, 1, commands.maintenance("REF", 0, False))
                at = PROFILES[0]["dram_ck"][field] + offset

                def attempt(m=m, at=at, kind=kind):
                    if kind == "REF":
                        m.command(at, 1, commands.maintenance("REF", 1, False))
                    else:
                        activate(m, at, 2)

                if offset < 0:
                    with self.assertRaises(Violation):
                        attempt()
                else:
                    attempt()
                    m.finish()

    def test_review_mrw_to_mrr_boundary(self):
        for p in PROFILES:
            for offset in (-1, 0):
                m = DramModel(p)
                a, b = commands.mode_write(3, p["mr"]["3"])
                m.command(0, 1, a)
                m.command(1, 1, b)
                at = 1 + p["dram_ck"]["tmrd"] + offset
                if offset < 0:
                    with self.assertRaisesRegex(Violation, "MR command recovery"):
                        m.command(at, 1, commands.mode_read(4))
                else:
                    m.command(at, 1, commands.mode_read(4))
                    m.finish()

    def test_mrw_to_mrw_and_mrr_to_mrr_boundaries(self):
        for write, field in [(True, "tmrw"), (False, "tmrr")]:
            for offset in (-1, 0):
                m = DramModel(PROFILES[0])
                if write:
                    a, b = commands.mode_write(3, 6)
                    m.command(0, 1, a)
                    m.command(1, 1, b)
                    origin = 1
                    nxt = a
                else:
                    m.command(0, 1, commands.mode_read(4))
                    origin = 0
                    nxt = commands.mode_read(4)
                at = origin + PROFILES[0]["dram_ck"][field] + offset
                if offset < 0:
                    with self.assertRaisesRegex(Violation, "MR command recovery"):
                        m.command(at, 1, nxt)
                else:
                    m.command(at, 1, nxt)
                    if write:
                        m.command(at + 1, 1, b)
                    m.finish()

    def test_precharge_refresh_and_act_origins(self):
        p = PROFILES[0]
        d = p["dram_ck"]
        for offset in (-1, 0):
            m = DramModel(p)
            activate(m)
            at = d["tras_min"] + offset
            if offset < 0:
                with self.assertRaises(Violation):
                    m.command(at, 1, commands.maintenance("PRE", 0, False))
            else:
                m.command(at, 1, commands.maintenance("PRE", 0, False))
                with self.assertRaises(Violation):
                    activate(copy.deepcopy(m), at + d["trp_pb"] - 1)
                activate(m, max(d["trc"], at + d["trp_pb"]))
                m.finish()
        m = DramModel(p)
        activate(m)
        b = m.command(d["trcd"], 1, commands.access(False, 16, 0, 0))
        fill(m, b)
        m.finish()

    def test_zq_boundary_and_unsupported_mpc(self):
        p = PROFILES[0]

        def mpc(op):
            return commands.pack(48 | (op >> 7) << 6, op & 127)

        m = DramModel(p)
        # Admitted MR28=0 is continuous background calibration; no new ZQ
        # start is required. The latch still blocks CA for the full tZQLAT.
        m.command(100, 1, mpc(0x86))
        with self.assertRaisesRegex(Violation, "latch recovery"):
            activate(copy.deepcopy(m), 100 + p["dram_ck"]["tzqlat"] - 1)
        activate(m, 100 + p["dram_ck"]["tzqlat"])
        m.finish()
        with self.assertRaisesRegex(ValueError, "unsupported MPC"):
            DramModel(p).command(0, 1, mpc(0x81))

    def test_power_down_sampled_cs_exit(self):
        for offset in (-1, 0):
            m = DramModel(PROFILES[0])
            m.command(0, 1, commands.pack(64, 0))
            with self.assertRaisesRegex(Violation, "power-down"):
                activate(copy.deepcopy(m), 1)
            m.power_exit(20, 0, cs_high=10, cs_low=13, ca_low=2)
            at = 20 + PROFILES[0]["dram_ck"]["txp"] + offset
            if offset < 0:
                with self.assertRaisesRegex(Violation, "rank recovery"):
                    activate(m, at)
            else:
                activate(m, at)
                m.finish()
        with self.assertRaisesRegex(Violation, "CS pulse"):
            m = DramModel(PROFILES[0])
            m.command(0, 1, commands.pack(64, 0))
            m.power_exit(20, 0, cs_high=10, cs_low=11, ca_low=2)

    def test_split_column_and_write_to_masked_boundaries(self):
        for bank, masked, gap in ((1, False, 8), (0, True, 20), (1, True, 8)):
            for offset in (-1, 0):
                m = DramModel(PROFILES[0])
                activate(m)
                if bank:
                    activate(m, 4, bank)
                b = m.command(20, 1, commands.access(True, 32, 0, 0))
                fill(m, b)
                if offset < 0:
                    with self.assertRaisesRegex(Violation, "spacing"):
                        m.command(20 + gap + offset, 1, commands.access(True, 16, bank, 0, masked=masked))
                else:
                    b = m.command(20 + gap, 1, commands.access(True, 16, bank, 0, masked=masked))
                    fill(m, b)
                    m.finish()

    def test_pdx_blocks_mr_and_mrr_needs_extra_recovery(self):
        p = PROFILES[0]
        m = DramModel(p)
        m.command(0, 1, commands.pack(64, 0))
        m.power_exit(20, 0, cs_high=10, cs_low=13, ca_low=2)
        xp = 20 + p["dram_ck"]["txp"]
        with self.assertRaisesRegex(Violation, "rank recovery"):
            copy.deepcopy(m).command(xp - 1, 1, commands.mode_read(4))
        with self.assertRaisesRegex(Violation, "PDX to MRR"):
            copy.deepcopy(m).command(m.pdx_mrr[0] - 1, 1, commands.mode_read(4))
        m.command(m.pdx_mrr[0], 1, commands.mode_read(4))
        m.finish()
        # The tXSR exception applies only to self-refresh, where Table 243
        # explicitly permits MR operations before bank commands recover.
        m = DramModel(p)
        m.command(0, 1, commands.pack(104, 0))
        m.command(20, 1, commands.pack(40, 0))
        m.command(21, 1, commands.mode_read(4))
        m.finish()

    def test_mrr_data_period_and_power_entry_spacing(self):
        for p in (PROFILES[0], next(p for p in PROFILES if p["ratio"] == 2)):
            for offset in (-1, 0):
                m = DramModel(p)
                activate(m)
                m.command(20, 1, commands.mode_read(4))
                gap = p["rl_ck"] + (4 if p["ratio"] == 4 else 8) + 2
                if offset < 0:
                    with self.assertRaisesRegex(Violation, "MR command recovery"):
                        m.command(20 + gap - 1, 1, commands.access(False, 16, 0, 0))
                else:
                    fill(m, m.command(20 + gap, 1, commands.access(False, 16, 0, 0)))
                    m.finish()
        m = DramModel(PROFILES[0])
        m.command(20, 1, commands.pack(124, 0))
        with self.assertRaisesRegex(Violation, "valid command to PDE"):
            m.command(22, 1, commands.pack(64, 0))
        m = DramModel(PROFILES[0])
        m.command(20, 1, commands.pack(124, 0))
        m.command(23, 1, commands.pack(64, 0))
        m.finish()

    def test_turnaround_uses_latency_and_full_effective_split(self):
        p = PROFILES[0]
        for write in (False, True):
            gap = p["wl_ck"] + 8 + p["dram_ck"]["twtr_l"] if write else p["rl_ck"] + 8 + 2 - p["wl_ck"]
            for offset in (-1, 0):
                m = DramModel(p)
                activate(m)
                first = m.command(20, 1, commands.access(write, 32, 0, 0))
                fill(m, first)
                at = 20 + gap + offset
                if offset < 0:
                    with self.assertRaisesRegex(Violation, "turnaround"):
                        m.command(at, 1, commands.access(not write, 32, 0, 0))
                else:
                    b = m.command(at, 1, commands.access(not write, 32, 0, 0))
                    fill(m, b)
                    m.finish()


class IntegrityCorrections(unittest.TestCase):
    def test_write_link_correction_mask_dbi_into_physical_memory(self):
        for p in (p for p in PROFILES if p["write_link"]):
            for masked in (False, True):
                for error in ("clean", "dq", "dmi", "double"):
                    phy = DigitalPhy(p, ROWS)
                    activate(phy.dram)
                    b = phy.dram.command(20, 1, commands.access(True, 16 if masked else 32, 0, 0, masked=masked))
                    size = b.beats * 2
                    data = bytes((i * 73) % 256 for i in range(size))
                    enables = [not masked or i % 3 != 0 for i in range(size)]
                    wire = transport(ROWS, b.index, data, enables, write=True, dbi=p["write_dbi"])
                    if error == "dq":
                        wire[6] = replace(wire[6], dq=wire[6].dq ^ 4)
                    elif error == "dmi":
                        wire[6] = replace(wire[6], dmi=wire[6].dmi ^ 1)
                    elif error == "double":
                        wire[6] = replace(wire[6], dq=wire[6].dq ^ 3)

                    def send(b=b, wire=wire, phy=phy, p=p):
                        for word in range(b.beats // 2):
                            block = wire[word * 4 : word * 4 + 4]
                            phy.write_word(
                                b,
                                word,
                                b.times[word * 2] - p["phy_half_wck"]["write"],
                                sum(x.dq << (8 * i) for i, x in enumerate(block)),
                                sum(x.dmi << i for i, x in enumerate(block)),
                                sum(x.parity << i for i, x in enumerate(block)),
                            )

                    if error == "double":
                        with self.assertRaisesRegex(Violation, "uncorrectable"):
                            send()
                        self.assertFalse(phy.dram.memory)
                    else:
                        send()
                        phy.finish()
                        expected = {(0, 0, 0, i): data[i] for i in range(size) if enables[i]}
                        self.assertEqual(phy.dram.memory, expected)
                        self.assertIn("corrected" if error != "clean" else "clean", phy.write_status[b.index])

    def make_merge(self, intervening=False):
        h = HostScoreboard()
        tags = []
        for n in range(2):
            if n and intervening:
                h.admit(Address(1, 4, 0), False)
            t = h.admit(Address(0, n, 0), True)
            h.w(0, n + 1, 65535, True)
            tags.append(t)
        m = MergeMonitor(h)
        for t in tags:
            m.context(t, Context(0, 0, 0, 0, 1, 0, 1))
        return h, m, tags

    def test_merge_originals_and_response_dependencies(self):
        h, m, tags = self.make_merge()
        m.begin(100, tags)
        with self.assertRaisesRegex(Violation, "physical/integrity"):
            h.offer(tags[0], 0, 0, True)
        m.pin(100, 0, (2).to_bytes(16, "little"), 65535, 0)
        m.physical(100)
        for t in tags:
            with self.assertRaisesRegex(Violation, "physical/integrity"):
                h.offer(t, 0, 0, True)
            h.integrity_done(t)
            h.offer(t, 0, 0, True)
            h.handshake(t)
        h.finish()
        m.finish()
        self.assertEqual(h.memory, h.serial)

    def test_merge_rejects_context_read_hazard_and_wrong_data(self):
        h, m, tags = self.make_merge()
        m.contexts[tags[1]] = replace(m.contexts[tags[1]], rank=1)
        with self.assertRaisesRegex(Violation, "context"):
            m.begin(100, tags)
        h, m, tags = self.make_merge(True)
        with self.assertRaisesRegex(Violation, "intervening"):
            m.begin(100, tags)
        h, m, tags = self.make_merge()
        m.begin(100, tags)
        with self.assertRaisesRegex(Violation, "merged byte"):
            m.pin(100, 0, bytes(16), 65535, 0)
        with self.assertRaisesRegex(Violation, "all bytes"):
            m.physical(100)

    def test_mr43_poll_result_guard_and_rank_independence(self):
        m = LifetimeMonitor()
        a = Identity(0, 0, 0, 1)
        b = Identity(1, 0, 0, 1)
        m.allocate(a, 0, 0, posted=True, link=True, ck=0)
        m.physical(a)
        m.transfer(a, reserved=True)
        m.expose(a, 0, handshaken=True)
        m.poll(0, 10)
        with self.assertRaisesRegex(Violation, "poll/read-clear"):
            m.allocate(b, 0, 1, posted=True, link=True, ck=11)
        m.allocate(b, 1, 1, posted=False, link=True, ck=11)
        m.physical(b)
        m.result(0, 20, True, read_clear_ck=30)
        self.assertIn(a, m.late_errors)
        c = Identity(2, 0, 0, 1)
        with self.assertRaisesRegex(Violation, "guard"):
            m.allocate(c, 0, 0, posted=True, link=True, ck=29)
        m.allocate(c, 0, 0, posted=True, link=True, ck=30)
        m.physical(c)
        m.poll(1, 25)
        m.result(1, 33, False, read_clear_ck=45)
        m.poll(0, 35)
        m.result(0, 43, False, read_clear_ck=55)
        for key in (b, c):
            m.transfer(key, reserved=True)
            m.expose(key, 0, handshaken=True)
        m.finish()

    def test_mr43_return_and_clear_timing_boundaries(self):
        m = LifetimeMonitor()
        key = Identity(0, 0, 0, 1)
        m.allocate(key, 0, 0, posted=False, link=True, ck=0)
        m.physical(key)
        m.poll(0, 10)
        with self.assertRaisesRegex(Violation, "result/read-clear timing"):
            m.result(0, 17, False, read_clear_ck=30)
        with self.assertRaisesRegex(Violation, "result/read-clear timing"):
            m.result(0, 18, False, read_clear_ck=29)
        m.result(0, 18, False, read_clear_ck=30)
        m.transfer(key, reserved=True)
        m.expose(key, 0, handshaken=True)
        m.finish()

    def test_missing_unsolicited_and_early_mr43(self):
        m = LifetimeMonitor()
        key = Identity(0, 0, 0, 1)
        m.allocate(key, 0, 0, posted=False, link=True, ck=0)
        with self.assertRaisesRegex(Violation, "pin completion"):
            m.poll(0, 10)
        with self.assertRaisesRegex(Violation, "without poll"):
            m.result(0, 20, False, read_clear_ck=24)
        with self.assertRaisesRegex(Violation, "missing MR43"):
            m.advance(257)


class ManagementCorrections(unittest.TestCase):
    def run_update(self, script, release, *, mutate=None):
        port = ManagementPort("update", script)
        response = False
        for ck in range(34):
            request = ck < release
            if mutate and ck == mutate:
                response = True
            response = port.sample(ck, request=request, response=response)
        port.finish()

    def test_live_update_decline_early_ack_registered_release(self):
        self.run_update(ResponseScript(None, None), 5)
        self.run_update(ResponseScript(1, 3), 5)
        self.run_update(ResponseScript(3, 10), 12)
        self.run_update(ResponseScript(1, 30), 32)

    def test_live_update_late_stuck_reasserted_and_traffic(self):
        for script, release in [
            (ResponseScript(4, 6), 8),
            (ResponseScript(1, None), 33),
            (ResponseScript(1, 31), 33),
            (ResponseScript(1, 3), 4),
        ]:
            with self.assertRaises(Violation):
                self.run_update(script, release)
        with self.assertRaisesRegex(Violation, "reassertion"):
            self.run_update(ResponseScript(1, 3), 5, mutate=8)
        p = ManagementPort("update", ResponseScript(None, None))
        with self.assertRaisesRegex(Violation, "unsafe"):
            p.sample(0, request=True, response=False, traffic=True)

    def test_live_fsp_training_pm_master_and_stale_completion(self):
        for kind in ("fsp", "training", "master", "pm"):
            script = ResponseScript(1, 6, initial=kind in ("fsp", "training"))
            p = ManagementPort(kind, script)
            response = script.initial
            for ck in range(10):
                response = p.sample(ck, request=ck < 7, response=response)
            p.finish()
        p = ManagementPort("fsp", ResponseScript(None, None, initial=True))
        p.sample(0, request=True, response=True)
        with self.assertRaisesRegex(Violation, "stale"):
            p.sample(1, request=False, response=True)


class StreamCorrections(unittest.TestCase):
    def stream(self, mutation=None, profile=None, write=True):
        p = profile or next(
            p
            for p in PROFILES
            if p["always_on"]
            and not p["read_link"]
            and not p["write_link"]
            and not p["read_dbi"]
            and not p["write_dbi"]
        )
        m = DramModel(p)
        activate(m)
        s = StreamMonitor(p)
        ck = m.ck
        cas = 19 * ck
        enable_ck = (
            (4 if p["ratio"] == 4 else 3)
            if write
            else (7 + 2 * p["read_link"] + p["read_dbi"] if p["ratio"] == 4 else 5 + 2 * p["read_dbi"])
        )
        enable = cas + enable_ck * ck
        s.begin(
            cas=cas,
            enable=enable,
            slow=enable + 4 * ck,
            fast=enable + 5 * ck,
            rank=0,
            direction="write" if write else "read",
        )
        for at in (20, 28, 36):
            b = m.command(at, 1, commands.access(write, 32, 0, 0))
            s.access(b)
            fill(m, b)
        ws = s.last_data + 6 if p["always_on"] else None
        stop = ws + 5 * ck if ws is not None else s.last_data + 6
        disabled = stop + ck
        s.end(ws_off=ws, stop=stop, disabled=disabled)
        for at in range(disabled + 1):
            edge = (
                at >= s.first and at < stop and (at >= s.fast or (at - s.first) % 2 == 0)
                if s.first is not None
                else False
            )
            rank = 0 if s.selected <= at < disabled else None
            cs = 0 if any(begin <= at < end for begin, end, _ in s.windows) else None
            if mutation == "edge" and at == s.fast + 1:
                edge = False
            if mutation == "cs" and at == s.windows[0][0] + 20:
                cs = None
            if mutation == "rank" and at == stop:
                rank = 1
            s.sample(at, edge=edge, rank=rank, data_cs=cs)
        s.finish()
        m.finish()

    def test_fresh_epoch_after_disable_and_reuse_expiry(self):
        p = next(p for p in PROFILES if p["always_on"] and not p["write_link"] and not p["write_dbi"])
        m, s = DramModel(p), StreamMonitor(p)
        activate(m)
        for cas_ck in (19, 119):
            cas, ck = cas_ck * m.ck, m.ck
            s.begin(cas=cas, enable=cas + 4 * ck, slow=cas + 8 * ck, fast=cas + 9 * ck, rank=0, direction="write")
            b = m.command(cas_ck + 1, 1, commands.access(True, 32, 0, 0))
            s.access(b)
            fill(m, b)
            ws = b.times[-1] + 6
            stop, disabled = ws + 5 * ck, ws + 6 * ck
            s.end(ws_off=ws, stop=stop, disabled=disabled)
            for at in range(s.last_sample + 1, disabled + 1):
                edge = s.first <= at < stop and (at >= s.fast or (at - s.first) % 2 == 0)
                cs = 0 if any(begin <= at < end for begin, end, _ in s.windows) else None
                s.sample(at, edge=edge, rank=0 if s.selected <= at < disabled else None, data_cs=cs)
            s.finish()
        m.finish()
        p = dict(p, always_on=False)
        s = StreamMonitor(p)
        ck = m.ck
        s.begin(cas=19 * ck, enable=23 * ck, slow=27 * ck, fast=28 * ck, rank=0, direction="write")
        s.access(b := replace(b, command_ck=20))
        with self.assertRaisesRegex(ValueError, "expired"):
            s.access(replace(b, command_ck=200))

    def test_continuous_profile_direction_matrix(self):
        for p in PROFILES:
            for write in (False, True):
                self.stream(profile=p, write=write)

    def test_continuous_reused_wck_and_cs(self):
        self.stream()

    def test_continuous_holes_edges_and_rank_sensitivity(self):
        for mutation in ("edge", "cs", "rank"):
            with self.assertRaises(Violation):
                self.stream(mutation)
