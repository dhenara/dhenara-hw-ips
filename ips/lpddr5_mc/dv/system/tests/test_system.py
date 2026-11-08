"""Executable environment qualification; fixtures never count as DUT evidence."""

import copy
import json
import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from contracts import commands, integrity, profiles
from contracts.read_completion import CompletionOracle, ExpectedWord, ReadBusOracle
from contracts.update import check_update
from system.adapters import ApbMonitor, returned_snapshot
from system.admission import AdmissionMonitor
from system.control import PublicationMonitor, RecoveryMonitor, ResponseScript
from system.dram import DramModel, decode
from system.ecc import InlineWitness, LinkCapture
from system.host import Address, HostScoreboard, Violation, byte_ranges
from system.measurement import Benchmark, Caps, Fairness
from system.ownership import Identity, LifetimeMonitor
from system.phy import DigitalPhy, WaveCapture
from system.trace import read_trace

IP = Path(__file__).resolve().parents[3]
PROFILES = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"]
ROWS = json.loads((IP / "config/reference/link_ecc_matrix.json").read_text())


def write(host, address=0, port=0, ident=0, length=0, data=0x1234):
    tag = host.admit(Address(port, ident, address, length, 4), True)
    for n in range(length + 1):
        host.w(port, data + n, 65535, n == length)
    for n in range(length + 1):
        host.pin_write(tag, address + n * 16, (data + n).to_bytes(16, "little"), 65535, host.epoch)
    host.physical_done(tag)
    return tag


def capture(wave):
    c = WaveCapture(wave.ratio, wave.direction, wave.beats, wave.rank)
    for name in c.scalar:
        if getattr(wave, name) is not None:
            c.sample(name, getattr(wave, name))
    for name in c.sequence:
        for value in getattr(wave, name):
            if name in ("cs", "data_cs"):
                c.sample(name, value[0], value[1])
            else:
                c.sample(name, value)
    return c


class HostTests(unittest.TestCase):
    def test_configured_id_and_address_bounds_settle_every_beat(self):
        for id_bits, address_bits in ((4, 32), (8, 36), (16, 40)):
            with self.subTest(id_bits=id_bits, address_bits=address_bits):
                h = HostScoreboard(id_bits=id_bits, address_bits=address_bits, mapped_end=1 << address_bits)
                # The highest admitted ID and final aligned bus beat are valid.
                a = Address(0, (1 << id_bits) - 1, (1 << address_bits) - 16, size=4)
                tag = h.admit(a, False)
                self.assertEqual(h.transactions[tag].error, 0)
                h.physical_done(tag)
                h.integrity_done(tag)
                h.offer(tag, 0, 0, True)
                h.handshake(tag)
                invalid = [Address(0, 1, 1 << address_bits, length=3, size=4)]
                if id_bits < 16:
                    invalid.append(Address(0, 1 << id_bits, 0, length=3, size=4))
                for descriptor in invalid:
                    for write_access in (False, True):
                        tag = h.admit(descriptor, write_access)
                        self.assertEqual(h.transactions[tag].error, 3)
                        if write_access:
                            for beat in range(4):
                                h.w(0, 1, 65535, beat == 3)
                            with self.assertRaisesRegex(Violation, "response code"):
                                h.offer(tag, 0, 0, True)
                            h.offer(tag, 0, 3, True)
                            h.handshake(tag)
                        else:
                            for beat in range(4):
                                h.offer(tag, 0, 3, beat == 3)
                                h.handshake(tag)
                h.finish()

    def test_write_read_pin_effects_and_backpressure(self):
        h = HostScoreboard()
        t = write(h, length=3)
        h.integrity_done(t)
        h.offer(t, 0, 0, True)
        h.offer(t, 0, 0, True)
        h.handshake(t)
        r = h.admit(Address(1, 9, 0, 3, 4), False)
        h.physical_done(r)
        h.integrity_done(r)
        for n in range(4):
            h.offer(r, 0x1234 + n, 0, n == 3)
            h.handshake(r)
        h.finish()
        self.assertEqual(h.handshaken_bytes, {True: 64, False: 64})

    def test_source_acceptance_survives_reversed_cdc_import(self):
        h = HostScoreboard()
        # Independent source clocks: AW at 1250ps, AR at 1708ps. Their CDC
        # arrivals reverse at CK. Scoreboard admission stays at source edges.
        aw = h.admit(Address(0, 1, 0), True)
        ar = h.admit(Address(1, 2, 0), False)
        with self.assertRaisesRegex(Violation, "overlap"):
            h.physical_done(ar)
        h.w(0, 0x5A, 65535, True)
        h.pin_write(aw, 0, (0x5A).to_bytes(16, "little"), 65535, 0)
        h.physical_done(aw)
        h.integrity_done(aw)
        # B may be stalled while the serially safe read proceeds.
        h.physical_done(ar)
        h.integrity_done(ar)
        h.offer(ar, 0x5A, 0, True)
        h.handshake(ar)
        h.offer(aw, 0, 0, True)
        h.handshake(aw)
        h.finish()
        reverse = HostScoreboard()
        r = reverse.admit(Address(1, 2, 0), False)
        reverse.admit(Address(0, 1, 0), True)
        reverse.physical_done(r)
        reverse.integrity_done(r)
        with self.assertRaisesRegex(Violation, "R data"):
            reverse.offer(r, 0x5A, 0, True)

    def test_source_ties_port_then_aw(self):
        h = HostScoreboard()
        h.edge([(Address(1, 4, 0), False), (Address(0, 3, 0), False), (Address(0, 2, 0), True)])
        self.assertEqual(
            [(t.address.port, t.address.id, t.write) for t in h.transactions.values()],
            [(0, 2, True), (0, 3, False), (1, 4, False)],
        )

    def test_aw_before_w_hazard(self):
        h = HostScoreboard()
        h.admit(Address(0, 0, 0), True)
        r = h.admit(Address(1, 0, 0), False)
        with self.assertRaisesRegex(Violation, "overlap"):
            h.physical_done(r)

    def test_no_effect_before_full_validation(self):
        h = HostScoreboard()
        t = h.admit(Address(0, 0, 0, 3), True)
        h.w(0, 99, 65535, False)
        with self.assertRaisesRegex(Violation, "full validation"):
            h.pin_write(t, 0, bytes(16), 65535, 0)
        for i in range(3):
            h.w(0, 0, 65535, i == 2)
        with self.assertRaisesRegex(Violation, "all pin bytes"):
            h.physical_done(t)

    def test_malformed_w_full_error_settlement(self):
        h = HostScoreboard()
        t = h.admit(Address(0, 0, 0, 2), True)
        h.w(0, 0, 65535, True)
        with self.assertRaisesRegex(Violation, "accepted W"):
            h.offer(t, 0, 2, True)
        h.w(0, 0, 65535, False)
        h.w(0, 0, 65535, True)
        with self.assertRaises(Violation):
            h.pin_write(t, 0, bytes(16), 65535, 0)
        h.offer(t, 0, 2, True)
        h.handshake(t)
        h.finish()
        self.assertEqual(h.memory, {})

    def test_decerr_read_count_and_no_effect(self):
        h = HostScoreboard(mapped_end=4096)
        t = h.admit(Address(0, 33, 4096, 7), False)
        for n in range(8):
            h.offer(t, 0, 3, n == 7)
            h.handshake(t)
        h.finish()
        self.assertFalse(h.memory)

    def test_response_credit_and_tail(self):
        h = HostScoreboard(capacity=1)
        t = h.admit(Address(0, 0, 0), False)
        with self.assertRaisesRegex(Violation, "credit"):
            h.admit(Address(1, 0, 64), False)
        with self.assertRaisesRegex(Violation, "trace end"):
            h.finish()
        h.physical_done(t)
        h.integrity_done(t)
        h.offer(t, 0, 0, True)
        h.handshake(t)
        with self.assertRaises(Violation):
            h.handshake(t)

    def test_physical_and_integrity_prerequisites(self):
        h = HostScoreboard()
        t = h.admit(Address(0, 0, 0), True)
        h.w(0, 1, 65535, True)
        with self.assertRaisesRegex(Violation, "physical/integrity"):
            h.offer(t, 0, 0, True, posted=True)
        h.pin_write(t, 0, (1).to_bytes(16, "little"), 65535, 0)
        h.physical_done(t)
        with self.assertRaisesRegex(Violation, "physical/integrity"):
            h.offer(t, 0, 0, True)
        h.offer(t, 0, 0, True, posted=True)
        h.integrity_done(t, True)
        h.offer(t, 0, 0, True, posted=True)
        self.assertIn(t, h.late_errors)
        with self.assertRaisesRegex(Violation, "payload changed"):
            h.offer(t, 0, 2, True, posted=True)
        h.handshake(t)

    def test_same_id_order_and_full_ids(self):
        h = HostScoreboard()
        a = write(h, ident=65535)
        b = write(h, address=64, ident=65535)
        h.integrity_done(b)
        with self.assertRaisesRegex(Violation, "same-ID"):
            h.offer(b, 0, 0, True)
        h.integrity_done(a)
        h.offer(a, 0, 0, True)
        h.handshake(a)
        h.offer(b, 0, 0, True)
        h.handshake(b)
        h.finish()

    def test_fault_settlement_reset_epoch(self):
        h = HostScoreboard()
        t = h.admit(Address(0, 0, 0, 3), True)
        r = h.admit(Address(1, 1, 128, 3), False)
        h.fail()
        with self.assertRaises(Violation):
            h.reset(destructive=True)
        for n in range(4):
            h.w(0, 0, 65535, n == 3)
            h.offer(r, 0, 2, n == 3)
            h.handshake(r)
        h.offer(t, 0, 2, True)
        h.handshake(t)
        h.reset(destructive=True)
        u = write(h)
        with self.assertRaisesRegex(Violation, "stale"):
            h.pin_write(u, 0, bytes(16), 65535, 0)

    def test_random_bursts_strobes_and_widths(self):
        rng = random.Random(2095)
        for bus in (8, 16, 32):
            for burst in (0, 1, 2):
                for _ in range(32):
                    size = rng.randrange(bus.bit_length())
                    count = rng.choice((2, 4, 8, 16))
                    address = rng.randrange(0, 8) * 64
                    a = Address(0, 0, address, count - 1, size, burst)
                    ranges = byte_ranges(a, bus)
                    self.assertEqual(len(ranges), count)
                    self.assertTrue(all(len(b) == 1 << size for b in ranges))
        self.assertEqual(byte_ranges(Address(0, 0, 3, 1, 2), 16), [(3,), (4, 5, 6, 7)])
        self.assertEqual(byte_ranges(Address(0, 0, 24, 3, 3, 2), 16)[1], tuple(range(0, 8)))
        for a in (Address(0, 0, 4090, 1), Address(0, 0, 3, 3, 2, 2)):
            with self.assertRaises(Violation):
                byte_ranges(a, 16)

    def test_partial_strobe_and_wrong_physical_byte(self):
        h = HostScoreboard()
        t = h.admit(Address(0, 0, 3, 0, 2), True)
        h.w(0, 0xAA000000, 8, True)
        with self.assertRaisesRegex(Violation, "physical write byte"):
            h.pin_write(t, 3, b"\xbb", 1, 0)
        h.pin_write(t, 3, b"\xaa", 1, 0)
        h.physical_done(t)
        h.integrity_done(t)
        h.offer(t, 0, 0, True)
        h.handshake(t)
        h.finish()


class ProtocolTests(unittest.TestCase):
    def test_sampled_power_down_exit_pulse(self):
        for high_cycles in (1, 3):
            with self.subTest(high_cycles=high_cycles):
                phy = DigitalPhy(PROFILES[0])
                phy.command(10, 1, 64)
                for ck in range(11, 20):
                    phy.command(ck, 0, 0)
                for ck in range(20, 20 + high_cycles):
                    phy.command(ck, 1, 0)
                if high_cycles == 1:
                    with self.assertRaisesRegex(Violation, "PDX CS pulse"):
                        for ck in range(21, 26):
                            phy.command(ck, 0, 0)
                else:
                    for ck in range(23, 28):
                        phy.command(ck, 0, 0)
                    self.assertIsNone(phy.dram.power[0])
                    self.assertEqual(phy.dram.rank_ready[0], 29 + PROFILES[0]["dram_ck"]["txp"])

    def test_mrr_read_synchronization(self):
        m = DramModel(PROFILES[0])
        m.command(0, 1, commands.cas("read"))
        m.command(1, 1, commands.mode_read(4))
        m.finish()
        m = DramModel(PROFILES[0])
        m.command(0, 1, commands.cas("write"))
        with self.assertRaisesRegex(Violation, "adjacency/direction/rank"):
            m.command(1, 1, commands.mode_read(4))

    def test_literal_ca_and_seeded_operands(self):
        rng = random.Random(2095)
        self.assertEqual(decode(0x58 | 4 << 7).kind, "MRW1")
        for _ in range(128):
            row, bank = rng.randrange(1 << 18), rng.randrange(16)
            first, second = map(decode, commands.activate(row, bank))
            self.assertEqual((first.operand | second.operand, first.bank), (row, bank))
            for write_op in (False, True):
                for beats in (16, 32):
                    column = rng.randrange(32) * 2
                    c = decode(commands.access(write_op, beats, bank, column, True))
                    self.assertEqual(
                        (c.kind, c.bank, c.operand, c.beats, c.ap),
                        ("WR" if write_op else "RD", bank, column, beats, True),
                    )
        self.assertEqual(decode(commands.cas("read")).operand, 2)

    def test_pairs_deadline_rank_and_timing(self):
        first, second = commands.activate(0x123, 0)
        for at, rank, ca in ((2, 1, second), (1, 2, second), (1, 1, commands.mode_read(4))):
            m = DramModel(PROFILES[0])
            m.command(0, 1, first)
            with self.assertRaises(Violation):
                m.command(at, rank, ca)
        m = DramModel(PROFILES[0])
        m.command(0, 1, first)
        m.command(1, 1, second)
        with self.assertRaisesRegex(Violation, "ready open"):
            m.command(2, 1, commands.access(False, 32, 0, 0))

    def test_pin_model_write_read_and_split(self):
        phy = DigitalPhy(PROFILES[0])
        a, b = commands.activate(0, 0)
        phy.command(0, 1, a)
        phy.command(1, 1, b)
        phy.command(17, 1, commands.cas("write"))
        w = phy.command(18, 1, commands.access(True, 32, 0, 0))
        for word in range(16):
            phy.write_word(w, word, w.times[word * 2] - PROFILES[0]["phy_half_wck"]["write"], word + 0x11223300)
        phy.command(99, 1, commands.cas("read"))
        r = phy.command(100, 1, commands.access(False, 32, 0, 0))
        phy.schedule_read(r, skew_ck=(0, 1))
        received = {}
        for ck in range(100, 145):
            for lane in phy.returned(ck):
                for item in lane:
                    if item:
                        received[item.word, item.slice] = item.value
        self.assertEqual(len(received), 32)
        for word in range(16):
            expected = word + 0x11223300
            for s in range(2):
                value = (expected >> (s * 8) & 255) | (expected >> (16 + s * 8) & 255) << 8
                self.assertEqual(received[word, s], value)
        phy.finish()
        with self.assertRaisesRegex(Violation, "duplicate pin"):
            phy.dram.beat(w, 0, w.times[0], 0)

    def test_missing_split_word_and_ca_collision(self):
        m = DramModel(PROFILES[0])
        a, b = commands.activate(0, 0)
        m.command(0, 1, a)
        m.command(1, 1, b)
        w = m.command(17, 1, commands.access(True, 32, 0, 0))
        for i in range(16):
            m.beat(w, i, w.times[i], i)
        with self.assertRaisesRegex(Violation, "missing pin"):
            m.finish()
        with self.assertRaisesRegex(Violation, "time/identity"):
            m.beat(w, 16, w.times[16] - 16, 0)

    def test_wave_capture_matrix(self):
        for p in PROFILES:
            for direction in ("read", "write"):
                for beats in (16, 32):
                    for rank in (0, 1):
                        for fast in (False, True):
                            with self.subTest(
                                profile=p["name"], direction=direction, beats=beats, rank=rank, fast=fast
                            ):
                                w = profiles.waveform(p, direction, beats, rank, fast)
                                capture(w).finish(p, fast_sync=fast)

    def test_wave_sensitivity_and_rank_switch(self):
        p = PROFILES[0]
        w = profiles.waveform(p, "write", 32)
        mutations = [
            replace(w, data=w.data[:16]),
            replace(w, command=w.command + 1),
            replace(w, edges=w.edges[:-1]),
            replace(w, data_cs=w.data_cs[:8] + w.data_cs[9:]),
            replace(w, data_cs_end=w.data_cs_end - 2),
            replace(w, disabled=w.stop - 1),
            replace(w, stop=w.data[-1] + 1),
            replace(w, dfi_data=tuple(t + 1 for t in w.dfi_data)),
        ]
        for bad in mutations:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    capture(bad).finish(p)
        other = profiles.waveform(p, "write", 32, 1)
        with self.assertRaises(ValueError):
            WaveCapture.ranks(p, [(0, w), (8, other)])
        WaveCapture.ranks(p, [(0, w), (1000 * 8, other)])
        for field in ("tphy_rdcsgap", "tphy_wrcsgap"):
            invalid = copy.deepcopy(p)
            invalid["dfi_wck"][field] = 1
            with self.assertRaises(ValueError):
                DramModel(invalid)

    def test_rotation_dnv_missing_duplicate_reset(self):
        for ratio in (2, 4):
            expected = [ExpectedWord(ck * ratio, 1, word, s) for ck, word in enumerate(range(8)) for s in range(2)]
            oracle = CompletionOracle(expected, 8, ratio * 8)
            bus = ReadBusOracle(ratio, oracle)
            for ck in range(8):
                values, dnv = [[None, None] for _ in range(ratio)], [[0, 0] for _ in range(ratio)]
                values[ck % ratio] = [ck, ck]
                dnv[ck % ratio] = [1 if ck == 7 else 0, 0]
                completed = bus.snapshot(ck, values, dnv)
                self.assertEqual(completed, [1] if ck == 7 else [])
            self.assertIn((1, 7, 0), bus.unavailable)
            with self.assertRaises(ValueError):
                bus.snapshot(9, [[1, 1]] * ratio, [[0, 0]] * ratio)
            bus.resynchronize("dfi_init_start")
            self.assertEqual(bus.pointer, [0, 0])

    def test_all_inline_single_and_double_faults(self):
        payload = 0x123456789ABCDEF0
        word = integrity.inline_encode(payload)
        for i in range(72):
            self.assertEqual(integrity.inline_decode(word ^ (1 << i)), (payload, "corrected"))
            for j in range(i + 1, 72):
                self.assertEqual(integrity.inline_decode(word ^ (1 << i) ^ (1 << j))[1], "uncorrectable")

    def test_inline_physical_pair_poison_and_stale_scrub(self):
        w = InlineWitness()
        w.begin(1, 0, 0x1234, 255)
        w.physical(1, "data", 0x1234, True)
        with self.assertRaisesRegex(Violation, "both physical"):
            w.settle(1, True)
        w.physical(1, "check", w.check_byte(0x1234), True)
        w.settle(1, True)
        w.words[0] = (0x1235, w.words[0][1])
        self.assertEqual(w.read(0), (0x1234, "corrected"))
        w.begin(2, 0, 0xABCD, 255)
        self.assertFalse(w.scrub(0))
        w.physical(2, "data", 0xABCD, True)
        w.physical(2, "check", w.check_byte(0xABCD), True)
        w.settle(2, True)
        self.assertFalse(w.scrub(0))
        w.begin(3, 0, 0xFF, 1)
        w.physical(3, "data", 0xABFF, True)
        w.physical(3, "check", 0, False)
        w.settle(3, False)
        with self.assertRaises(Violation):
            w.read(0)
        w.finish()

    def test_link_faults_mask_dbi_and_split_identity(self):
        data = bytes(range(64))
        wire = integrity.transport(ROWS, 33, data, [True] * 64, write=True, dbi=True)
        output, status = integrity.check_transport(ROWS, wire, write=True, dbi=True)
        self.assertEqual(output, data)
        self.assertTrue(all(s == "clean" for s in status))
        for pos in (0, 31, 32, 63):
            bad = list(wire)
            bad[pos] = replace(bad[pos], dq=bad[pos].dq ^ 1)
            output, status = integrity.check_transport(ROWS, bad, write=True, dbi=True)
            self.assertEqual(output, data)
            self.assertIn("corrected", status)
        with self.assertRaises(Violation):
            link = LinkCapture(ROWS, 33, 32, write=True, dbi=True)
            for x in wire[:32]:
                link.beat(x.tag, x.beat, x.byte, x.dq, x.dmi, x.parity)
            link.finish()
        with self.assertRaises(ValueError):
            integrity.check_transport(ROWS, wire[:32] + wire[:32], write=True, dbi=True)


class ControlTests(unittest.TestCase):
    def test_atomic_publication_and_abort(self):
        p = PublicationMonitor()
        p.prepare(1, 12, drained=True)
        p.acknowledge(0, 1, 12)
        with self.assertRaisesRegex(Violation, "all channels"):
            p.commit(0, 1)
        with self.assertRaises(Violation):
            p.acknowledge(1, 0, 12)
        p.acknowledge(1, 1, 12)
        p.commit(0, 1)
        with self.assertRaisesRegex(Violation, "partial publication"):
            p.traffic(0, 1)
        p.commit(1, 1)
        p.traffic(0, 1)
        p.prepare(2, 13, drained=True)
        p.abort()
        with self.assertRaises(Violation):
            p.traffic(0, 1)
        p.reset(host_settled=True, destructive_done=True)
        with self.assertRaises(Violation):
            p.acknowledge(1, 2, 13)

    def test_recovery_first_diagnostic_and_stale_epochs(self):
        m = RecoveryMonitor()
        m.boot(0, 1, known_state=True, wck_disabled=True)
        m.enable()
        m.fail("retention")
        m.fail("later")
        self.assertEqual(m.diagnostic, "retention")
        with self.assertRaises(Violation):
            m.enable()
        for settled, confirmed, cleared in ((False, True, True), (True, False, True), (True, True, False)):
            with self.assertRaises(Violation):
                m.reset(settled=settled, confirmed=confirmed, volatile_cleared=cleared)
        m.reset(settled=True, confirmed=True, volatile_cleared=True)
        with self.assertRaises(Violation):
            m.boot(0, 1, known_state=True, wck_disabled=True)
        m.boot(1, 2, known_state=True, wck_disabled=True)
        m.enable()
        self.assertEqual(m.diagnostic, "retention")
        m.fail("new episode")
        self.assertEqual(m.diagnostic, "retention")

    def test_scripted_update_boundaries_faults_decline(self):
        for start in (None, 1, 2, 3, 4, 5):
            for end in (None, 5, 30, 31, 32):
                with self.subTest(start=start, end=end):
                    ack = ResponseScript(start, end).samples(32)
                    result = check_update(4, 32, ack)
                    legal = (
                        start is None
                        or end is not None
                        and end <= start
                        or start < 4
                        and end is not None
                        and start < end <= 30
                    )
                    self.assertEqual(result.fault is None, legal)
        self.assertEqual(ResponseScript(2, 10, initial=True).samples(12)[1:3], [True, False])


class MeasurementTests(unittest.TestCase):
    def benchmark(self, divisor=2, lost=0):
        b = Benchmark("read", divisor)
        bursts = 339 if divisor == 4 else 375
        for p in range(100):
            for channel in range(2):
                b.boundary(p * 3000, channel)
            for i in range(bursts - lost):
                for channel in range(2):
                    at = p * 3000 + 100 + i * 7
                    tag = p * 1000 + i
                    b.admission(at, channel, tag)
                    b.completion(at, channel, tag, physical_ok=True, integrity_ok=True)
        for channel in range(2):
            b.boundary(300000, channel)
        if lost:
            b.stall("cdc", lost * 100)
        return b

    def test_exact_windows_all_service_modes(self):
        for divisor in (1, 2, 4):
            for direction in ("read", "write"):
                b = self.benchmark(divisor)
                b.direction = direction
                r = b.report(300000)
                self.assertTrue(r["threshold_passed"])
                self.assertEqual(r["missing_bytes"], 0)
                self.assertAlmostEqual(r["gb_per_second"], 11.5712 if divisor == 4 else 12.8)
                self.assertFalse(r["controller_qualified"])

    def test_tolerance_missing_bytes_and_no_tail_credit(self):
        b = self.benchmark(lost=3)
        self.assertTrue(b.report(300000)["threshold_passed"])
        c = self.benchmark(lost=4)
        self.assertFalse(c.report(300000)["threshold_passed"])
        with self.assertRaises(Violation):
            c.report(299999)
        before = c.report(300000)["completed_bytes"]
        c.admission(300000, 0, 999999)
        c.completion(300000, 0, 999999, physical_ok=True, integrity_ok=True)
        self.assertEqual(c.report(300000)["completed_bytes"], before)

    def test_backlog_overflow_duplicate_missing_stall(self):
        b = self.benchmark()
        b.boundaries[0, 300000] = 1
        with self.assertRaisesRegex(Violation, "growing"):
            b.report(300000)
        b = Benchmark("read", 2)
        for i in range(64):
            b.admission(0, 0, i)
        with self.assertRaisesRegex(Violation, "overflow"):
            b.admission(0, 0, 64)
        b.completion(0, 0, 0, physical_ok=True, integrity_ok=True)
        with self.assertRaisesRegex(Violation, "duplicate"):
            b.completion(0, 0, 0, physical_ok=True, integrity_ok=True)
        c = self.benchmark(lost=1)
        c.stalls.clear()
        with self.assertRaisesRegex(Violation, "stall"):
            c.report(300000)

    def test_caps_bytes_window_edge_and_borrow(self):
        c = Caps(8, (64, 64))
        c.issue(0, 0, 32, True)
        c.issue(1, 0, 32, True)
        with self.assertRaisesRegex(Violation, "byte cap"):
            c.issue(2, 0, 32, True)
        c.issue(8, 0, 64, True)
        with self.assertRaises(Violation):
            Caps(8, (0, 64)).issue(0, 0, 32, False)
        with self.assertRaises(Violation):
            Caps(8, (0, 64), True).issue(0, 0, 64, False)
        b = Caps(8, (32, 96), True)
        b.issue(0, 0, 64, False)
        with self.assertRaises(Violation):
            Caps(8, (32, 96), True).issue(0, 0, 64, True)

    def test_fairness_g_and_final_starved_tail(self):
        f = Fairness(depth=1, age_threshold=0, tick_div=1, issue_gap=2)
        self.assertEqual(Fairness().bound, 4360)
        f.step({0}, 0)
        f.step({0})
        f.step({0})
        with self.assertRaisesRegex(Violation, "issue gap G"):
            f.step({0})
        f = Fairness(depth=1, age_threshold=0, tick_div=1, issue_gap=2)
        for n in range(3):
            f.step({1, 2}, 2 if n % 2 else None)
        with self.assertRaisesRegex(Violation, "starvation"):
            f.step({1, 2}, 2)

    def test_borrowed_capacity_is_not_recreated_by_delayed_opposite_arrival(self):
        for direction in (0, 1):
            with self.subTest(direction=direction):
                c = Caps(8, (64, 64), True)
                c.issue(0, direction, 64, False)
                c.issue(1, direction, 32, False)
                with self.assertRaisesRegex(Violation, "combined byte cap"):
                    c.issue(2, 1 - direction, 64, True)
                c.issue(3, 1 - direction, 32, True)
                self.assertEqual(sum(c.used), 128)
                with self.assertRaisesRegex(Violation, "combined byte cap"):
                    c.issue(4, direction, 32, False)
                c.issue(8, direction, 64, True)
                c.issue(9, 1 - direction, 64, True)
                self.assertEqual(c.used, [64, 64])


class LifetimeTests(unittest.TestCase):
    def test_two_rank_epochs_and_posted_history_after_reuse(self):
        m = LifetimeMonitor()
        keys = []
        for i in range(16):
            for rank in range(2):
                key = Identity(rank * 16 + i, i, 0, 1)
                m.allocate(key, rank, rank, posted=True, link=True, ck=i)
                m.physical(key)
                m.transfer(key, reserved=True)
                m.expose(key, 0, handshaken=True)
                keys.append(key)
        self.assertEqual(len(m.live), 32)
        with self.assertRaisesRegex(Violation, "sixteen"):
            m.allocate(Identity(99, 99, 0, 1), 0, 3, posted=True, link=True, ck=20)
        m.poll(0, 100)
        m.poll(1, 101)
        m.result(0, 108, True, read_clear_ck=120)
        m.result(1, 109, False, read_clear_ck=121)
        self.assertEqual(m.late_errors, {k for k in keys if k.tag < 16})
        with self.assertRaisesRegex(Violation, "stale response"):
            m.expose(keys[0], 0, handshaken=True)
        m.finish()

    def test_fault_queue_insertion_is_not_source_exposure(self):
        for fault_phase in ("before_transfer", "after_transfer", "after_exposure"):
            for stalls in (0, 1, 40):
                with self.subTest(phase=fault_phase, stalls=stalls):
                    m = LifetimeMonitor()
                    key = Identity(1, 7, 0, 1)
                    m.allocate(key, 0, 0, posted=True, link=True, ck=0)
                    m.physical(key)
                    m.mr43(0, 20, False)
                    # Conservative async posted mode can wait for integrity;
                    # an internal queued OKAY still has not exposed BVALID.
                    if fault_phase == "before_transfer":
                        m.ownership_loss()
                    m.transfer(key, reserved=True)
                    if fault_phase == "after_transfer":
                        m.ownership_loss()
                    if fault_phase == "after_exposure":
                        m.expose(key, 0)
                        m.ownership_loss()
                    response = 0 if fault_phase == "after_exposure" else 2
                    with self.assertRaises(Violation):
                        m.expose(key, response ^ 2)
                    for _ in range(stalls):
                        m.expose(key, response)
                    m.expose(key, response, handshaken=True)
                    self.assertEqual(key in m.late_errors, fault_phase == "after_exposure")
                    m.reset(destructive_done=True)
                    with self.assertRaisesRegex(Violation, "stale response"):
                        m.expose(key, response)
                    m.finish()
        # Deliberately treating FIFO insertion as exposure would preserve an
        # unoffered OKAY across fault. Source-only observation rejects it.
        h = HostScoreboard()
        key = write(h)
        h.integrity_done(key)
        h.fail()
        with self.assertRaisesRegex(Violation, "response code"):
            h.offer(key, 0, 0, True, posted=True)
        h.offer(key, 0, 2, True, posted=True)
        h.handshake(key)
        h.finish()

    def test_settled_success_has_no_remaining_late_error_obligation(self):
        h = HostScoreboard()
        key = write(h)
        h.integrity_done(key)
        h.offer(key, 0, 0, True)
        h.handshake(key)
        h.fail()
        self.assertNotIn(key, h.late_errors)
        h.finish()
        h = HostScoreboard()
        key = write(h)
        h.offer(key, 0, 0, True, posted=True)
        h.handshake(key)
        with self.assertRaisesRegex(Violation, "posted integrity"):
            h.finish()
        h.integrity_done(key, True)
        self.assertIn(key, h.late_errors)
        h.finish()

    def test_reserved_transfer_and_failure_before_after_exposure(self):
        m = LifetimeMonitor(host_credits=1)
        key = Identity(1, 0, 0, 1)
        m.allocate(key, 0, 0, posted=True, link=True, ck=0)
        with self.assertRaisesRegex(Violation, "reserved owner"):
            m.transfer(key, reserved=True)
        m.physical(key)
        with self.assertRaises(Violation):
            m.transfer(key, reserved=False)
        m.transfer(key, reserved=True)
        m.mr43(0, 20, True)
        m.expose(key, 2, handshaken=True)
        m.finish()
        m = LifetimeMonitor()
        m.allocate(key, 0, 0, posted=True, link=True, ck=0)
        m.physical(key)
        m.transfer(key, reserved=True)
        m.expose(key, 0)
        m.ownership_loss()
        m.expose(key, 0, handshaken=True)
        self.assertIn(key, m.late_errors)
        with self.assertRaisesRegex(Violation, "trace tail"):
            m.finish()


class TraceTests(unittest.TestCase):
    def test_fail_closed_header_fields_and_tail(self):
        header = {"kind": "header", "version": 1, "bus_bytes": 16, "ports": 1, "capacity": 64, "source": "fixture"}
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "trace.jsonl"
            p.write_text(json.dumps(header) + '\n{"kind":"end","ps":1}\n')
            self.assertEqual(read_trace(p)["events"], 1)
            for bad in (
                [],
                [header],
                [header, {"kind": "unknown", "ps": 1}],
                [header, {"kind": "end", "ps": 1}, {"kind": "end", "ps": 2}],
            ):
                p.write_text("".join(json.dumps(x) + "\n" for x in bad))
                with self.assertRaises(Violation):
                    read_trace(p)


class AdapterTests(unittest.TestCase):
    def test_grant_revoke_race_reset_and_credit_pressure(self):
        m = AdmissionMonitor(credits=2)
        a = Address(0, 0, 0)
        m.grant(1, a, 0)
        m.grant(2, Address(1, 0, 64), 0)
        with self.assertRaisesRegex(Violation, "credit"):
            m.grant(3, Address(1, 0, 128), 0)
        m.revoke(2)
        m.revoked(2, source_accepted=False)
        with self.assertRaisesRegex(Violation, "conflicting"):
            m.grant(3, Address(1, 0, 0), 0)
        m.revoke(1)
        m.accept(1, 0, 0, 1250)
        with self.assertRaisesRegex(Violation, "concurrent"):
            m.revoked(1, source_accepted=False)
        m.revoked(1, source_accepted=True)
        with self.assertRaises(Violation):
            m.reset(source_fenced=False, host_settled=True)
        m.release(1, hazard_settled=True)
        m.reset(source_fenced=True, host_settled=True)
        with self.assertRaisesRegex(Violation, "epoch"):
            m.grant(4, a, 0)
        with self.assertRaisesRegex(Violation, "without grant"):
            m.accept(1, 0, 0, 1300)
        m.grant(4, a, 1)

    def test_forwarding_waits_for_all_write_integrity_dependencies(self):
        for failure in (False, True):
            h = HostScoreboard()
            w = h.admit(Address(0, 0, 0), True)
            r = h.admit(Address(1, 1, 0), False)
            with self.assertRaisesRegex(Violation, "validated W"):
                h.forwarded(r)
            h.w(0, 0x5A, 65535, True)
            h.forwarded(r)
            with self.assertRaisesRegex(Violation, "dependency"):
                h.integrity_done(r)
            h.pin_write(w, 0, (0x5A).to_bytes(16, "little"), 65535, 0)
            h.physical_done(w)
            h.integrity_done(w, failure)
            if failure:
                with self.assertRaisesRegex(Violation, "failed forwarding"):
                    h.integrity_done(r)
            h.integrity_done(r, failure)
            h.offer(r, 0 if failure else 0x5A, 2 if failure else 0, True)
            h.handshake(r)
            h.offer(w, 0, 2 if failure else 0, True)
            h.handshake(w)
            h.finish()

    def test_error_before_physical_and_destructive_lifetime_reset(self):
        m = LifetimeMonitor()
        key = Identity(1, 0, 0, 1)
        m.allocate(key, 0, 0, posted=True, link=True, ck=0)
        m.ownership_loss()
        m.transfer(key, reserved=True)
        with self.assertRaisesRegex(Violation, "host settlement"):
            m.reset(destructive_done=True)
        m.expose(key, 2, handshaken=True)
        m.reset(destructive_done=True)
        m.finish()
        with self.assertRaisesRegex(Violation, "stale response"):
            m.expose(key, 2, handshaken=True)
        with self.assertRaisesRegex(Violation, "stale allocation"):
            m.allocate(key, 0, 0, posted=True, link=True, ck=2)

    def test_canonical_dfi_field_interleaving_and_ratio_boundary(self):
        d = returned_snapshot(4, data=0x44332211, valid=3, dnv=9, link_parity=6)
        self.assertEqual(d["data"][0], [0x3311, 0x4422])
        self.assertEqual(d["dnv"][0], [1, 2])
        self.assertEqual(d["parity"][0], [2, 1])
        self.assertEqual(d["data"][1], [None, None])
        with self.assertRaisesRegex(Violation, "ratio-two"):
            returned_snapshot(2, data=0, valid=16, dnv=0, link_parity=0)

    def test_apb_sampled_setup_wait_payload_and_schema(self):
        schema = json.loads((IP / "config/contracts/registers.json").read_text())
        m = ApbMonitor(schema)
        name = "SCHED_RD_AGE"
        request = (0, name, True, 0x1234, 1, False, True)

        def edge(select, enable, ready=False, req=request, data=0, error=False):
            m.sample(reset=False, select=select, enable=enable, ready=ready, request=req, data=data, error=error)

        edge(True, False)
        edge(True, True)
        with self.assertRaisesRegex(Violation, "stable setup"):
            edge(True, True, req=(0, name, True, 0x99, 1, False, True))
        edge(True, True, True)
        self.assertEqual(m.completed, 1)
        edge(False, False)
        read = (0, name, False, 0, 0, False, True)
        edge(True, False, req=read)
        edge(True, True, True, req=read, data=0x434)
        m.finish()

    def test_long_burst_exceeds_channel_depth_and_response_stalls(self):
        for depth in (16, 32, 64):
            h = HostScoreboard(capacity=depth)
            w = write(h, length=255)
            h.integrity_done(w)
            for _ in range(20):
                h.offer(w, 0, 0, True)
            r = h.admit(Address(1, 1, 0, 255), False)
            h.physical_done(r)
            h.integrity_done(r)
            for beat in range(256):
                for _ in range(3):
                    h.offer(r, 0x1234 + beat, 0, beat == 255)
                h.handshake(r)
            h.handshake(w)
            h.finish()
            self.assertEqual(h.handshaken_bytes, {True: 4096, False: 4096})

    def test_phy_raw_read_link_parity_and_read_dbi(self):
        for p in (next(p for p in PROFILES if p["read_link"]), next(p for p in PROFILES if p["read_dbi"])):
            phy = DigitalPhy(p, ROWS)
            a, b = commands.activate(0, 0)
            phy.command(0, 1, a)
            phy.command(1, 1, b)
            for offset in range(64):
                phy.dram.memory[0, 0, 0, offset] = 255 - offset
            phy.command(99, 1, commands.cas("read"))
            read = phy.command(100, 1, commands.access(False, 32, 0, 0))
            phy.schedule_read(read, skew_ck=(0, 1), dnv={(15, 1)})
            raw = LinkCapture(ROWS, read.index, 32, write=False) if p["read_link"] else None
            observed = bytearray(64)
            for ck in range(100, 160):
                for lanes in phy.returned(ck):
                    for item in lanes:
                        if item is None:
                            continue
                        for half in range(2):
                            value = item.value >> (half * 8) & 255
                            if raw:
                                raw.beat(
                                    item.burst, item.word * 2 + half, item.slice, value, 0, item.parity >> half & 1
                                )
                            else:
                                observed[(item.word * 2 + half) * 2 + item.slice] = value ^ (
                                    255 if item.dmi >> half & 1 else 0
                                )
            if raw:
                observed, statuses = raw.finish()
                self.assertTrue(all(s == "clean" for s in statuses))
            self.assertEqual(bytes(observed), bytes(255 - i for i in range(64)))
            phy.finish()

    def test_hot_timing_and_intervening_bank_do_not_erase_constraints(self):
        p = PROFILES[0]
        m = DramModel(p)
        a, b = commands.activate(0, 0)
        m.command(0, 1, a)
        m.command(1, 1, b)
        m.maintenance.temperature(13)
        with self.assertRaisesRegex(Violation, "ready open"):
            m.command(1 + p["dram_ck"]["trcd"], 1, commands.access(False, 16, 0, 0))
        m = DramModel(p)
        for bank, at in ((0, 0), (4, 10)):
            a, b = commands.activate(0, bank)
            m.command(at, 1, a)
            m.command(at + 1, 1, b)
        m.command(40, 1, commands.access(False, 16, 0, 0))
        m.command(42, 1, commands.access(False, 16, 4, 0))
        with self.assertRaisesRegex(Violation, "spacing"):
            m.command(43, 1, commands.access(False, 16, 0, 0))

    def test_source_trace_sorts_true_ties_not_cdc_arrivals(self):
        from system.trace import source_order

        records = [
            {"kind": "header"},
            {"kind": "ar", "ps": 1250, "port": 1},
            {"kind": "ar", "ps": 1250, "port": 0},
            {"kind": "aw", "ps": 1250, "port": 0},
            {"kind": "w", "ps": 1250, "port": 0},
        ]
        stream = [json.dumps(e) for e in records]
        self.assertEqual(
            [(e["kind"], e.get("port")) for e in source_order(stream)],
            [("header", None), ("aw", 0), ("ar", 0), ("ar", 1), ("w", 0)],
        )


if __name__ == "__main__":
    unittest.main()
