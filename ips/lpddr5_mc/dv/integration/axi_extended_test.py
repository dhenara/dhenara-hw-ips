"""Synthetic source sensitivity and owning pin-correlation boundary checks.

These Python tests do not generate or replay an HDL trace. They establish only
the staged ledger's negative behavior and existing owner support for the selected
256-beat/locked descriptors; actual connected execution remains pending.
"""

import copy
import unittest

from axi_extended import CASES, MARKER, measure_events, payload_byte, word
from physical_ownership import WriteOwnership
from read_ownership import ReadOwnership
from system.host import Address, HostScoreboard, Violation


def source_fixture():
    events = [{"kind": "header", "version": 1, "ports": 2, "channels": 2, "bus_bytes": 16}]
    memory = {}

    def add(**event):
        events.append({"ps": len(events) * 100, **event})

    for index, case in enumerate(CASES):
        identity = index + 80
        add(
            kind="aw" if case.write else "ar",
            port=case.port,
            id=identity,
            address=case.address,
            length=case.beats - 1,
            size=4,
            burst=1,
            lock=case.lock,
        )
        if case.write:
            for beat in range(case.beats):
                last = beat == case.beats - 1
                if case.malformed == 1:
                    last = beat in (0, case.beats - 1)
                elif case.malformed == 2:
                    last = False
                data = word(case.address + beat * 16, case.salt)
                add(kind="w", port=case.port, data=data, strobe=65535, last=int(last))
                if not case.error:
                    memory.update({case.address + beat * 16 + lane: (data >> (8 * lane)) & 255 for lane in range(16)})
            for _ in range(4):
                add(kind="b", port=case.port, id=identity, response=case.error, ready=0)
            add(kind="b", port=case.port, id=identity, response=case.error, ready=1)
        else:
            for beat in range(case.beats):
                data = (
                    0
                    if case.error
                    else sum(memory.get(case.address + beat * 16 + lane, 0) << (8 * lane) for lane in range(16))
                )
                response = dict(
                    kind="r",
                    port=case.port,
                    id=identity,
                    data=data,
                    response=case.error,
                    last=int(beat == case.beats - 1),
                )
                if beat % 64 == 0 or beat == case.beats - 1:
                    for _ in range(4):
                        add(**response, ready=0)
                add(**response, ready=1)
    add(kind="end")
    return events


class ExtendedLedgerTests(unittest.TestCase):
    def setUp(self):
        self.events = source_fixture()

    def test_complete_synthetic_source_contract(self):
        self.assertNotEqual(word(0x20000, 0x11), word(0x20100, 0x11))
        result = measure_events(self.events, MARKER)
        self.assertEqual(result["counts"]["w_beats"], 1036)
        self.assertEqual(result["counts"]["r_handshakes"], 1548)
        self.assertEqual([case["name"] for case in result["cases"]], [case.name for case in CASES])

    def test_missing_or_extra_source_obligations_fail(self):
        variants = []
        for predicate in (
            lambda event: event["kind"] == "w" and event["last"] and event["port"] == 0,
            lambda event: event["kind"] == "r" and event["id"] == 81 and event["last"] and event["ready"],
            lambda event: event["kind"] == "end",
        ):
            variant = copy.deepcopy(self.events)
            del variant[next(i for i, event in enumerate(variant) if predicate(event))]
            variants.append(variant)
        variant = copy.deepcopy(self.events)
        last = variant[-1]["ps"]
        variant.insert(-1, dict(kind="b", port=0, id=80, response=0, ready=1, ps=last))
        variants.append(variant)
        for index, variant in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(Violation):
                measure_events(variant, MARKER)

    def test_exclusive_stability_and_preservation_mutations_fail(self):
        variants = []
        for predicate, field, value in (
            (lambda event: event["kind"] == "aw" and event["id"] == 89, "lock", 0),
            (lambda event: event["kind"] == "b" and event["id"] == 89, "response", 1),
            (lambda event: event["kind"] == "r" and event["id"] == 93, "response", 1),
            (lambda event: event["kind"] == "r" and event["id"] == 83 and event["ready"], "data", 0),
            (lambda event: event["kind"] == "r" and event["id"] == 95 and event["last"] and event["ready"], "id", 94),
        ):
            variant = copy.deepcopy(self.events)
            target = next(event for event in variant if predicate(event))
            target[field] = value
            variants.append(variant)
        for index, variant in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(Violation):
                measure_events(variant, MARKER)

    def test_offered_first_interior_final_stalls_are_required(self):
        for identity, kind in ((80, "b"), (81, "r"), (87, "r"), (93, "r")):
            with self.subTest(identity=identity):
                variant = [
                    event
                    for event in self.events
                    if not (event["kind"] == kind and event["id"] == identity and not event["ready"])
                ]
                with self.assertRaisesRegex(Violation, "missing actual"):
                    measure_events(variant, MARKER)

    def test_forged_completion_marker_fails(self):
        for marker in ("", MARKER + "\n" + MARKER, MARKER.replace("256", "128")):
            with self.subTest(marker=marker), self.assertRaisesRegex(Violation, "marker"):
                measure_events(self.events, marker)


class ExistingPhysicalOwnerBoundaryTests(unittest.TestCase):
    def build_write(self, *, malformed=False):
        host = HostScoreboard(bus_bytes=16, ports=2, capacity=64, mapped_end=1 << 20)
        tag = host.admit(Address(0, 80, 0x20000, 255, 4, 1), True)
        for beat in range(256):
            host.w(0, word(0x20000 + beat * 16, 0x11), 65535, beat in (0, 255) if malformed else beat == 255)
        return host, tag

    def physical_write(self):
        host, tag = self.build_write()
        owner = WriteOwnership(host)
        for granule in range(64):
            at = 0x20000 + granule * 64
            owner.begin(granule, range(at, at + 64), 100 + granule)
            for address in range(at, at + 64):
                owner.pin(granule, address, payload_byte(address, 0x11))
        self.assertFalse(owner.ready(tag, 162))
        owner.complete_before(tag, 163)
        host.physical_done(tag)
        host.integrity_done(tag)
        host.offer(tag, 0, 0, True)
        host.handshake(tag)
        return host

    def test_full_256_beat_pin_and_locked_read_owner_support(self):
        host = self.physical_write()
        tag = host.admit(Address(1, 90, 0x20000, 255, 4, 1, True), False)
        owner = ReadOwnership(host)
        for granule in range(64):
            at = 0x20000 + granule * 64
            owner.begin(granule, range(at, at + 64))
            owner.observe(granule, range(at, at + 64), 400 + granule)
        self.assertFalse(owner.ready(tag, 462))
        owner.complete_before(tag, 463)
        host.physical_done(tag)
        host.integrity_done(tag)
        with self.assertRaisesRegex(Violation, "EXOKAY"):
            host.offer(tag, word(0x20000, 0x11), 1, False)
        for beat in range(256):
            host.offer(tag, word(0x20000 + beat * 16, 0x11), 0, beat == 255)
            host.handshake(tag)
        host.finish()
        self.assertEqual(host.handshaken_bytes, {False: 4096, True: 4096})

    def test_missing_pin_byte_or_return_tail_cannot_retire(self):
        host, tag = self.build_write()
        owner = WriteOwnership(host)
        for granule in range(64):
            at = 0x20000 + granule * 64
            owner.begin(granule, range(at, at + 64), 100 + granule)
            for address in range(at, at + 64 - (granule == 63)):
                owner.pin(granule, address, payload_byte(address, 0x11))
        with self.assertRaisesRegex(Violation, "complete physical payload"):
            owner.complete_before(tag, 1000)
        host = self.physical_write()
        tag = host.admit(Address(1, 90, 0x20000, 255, 4, 1, True), False)
        owner = ReadOwnership(host)
        for granule in range(64):
            at = 0x20000 + granule * 64
            owner.begin(granule, range(at, at + 64))
            owner.observe(granule, range(at, at + 64 - (granule == 63)), 400 + granule)
        with self.assertRaisesRegex(Violation, "complete physical payload"):
            owner.complete_before(tag, 1000)

    def test_malformed_maximum_write_has_no_legal_physical_owner(self):
        host, tag = self.build_write(malformed=True)
        self.assertEqual(host.transactions[tag].error, 2)
        with self.assertRaisesRegex(Violation, "no admitted owner"):
            WriteOwnership(host).begin(0, range(0x20000, 0x20040), 100)
        host.offer(tag, 0, 2, True)
        host.handshake(tag)
        host.finish()
        self.assertEqual(host.memory, {})


if __name__ == "__main__":
    unittest.main()
