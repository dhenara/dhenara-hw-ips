"""Prepared FSP/atomic-publication adapter sensitivity; no RTL execution."""

import copy
import unittest

from contracts import commands
from contracts.dvfs import Sample
from system.host import Violation
from wrapper_fsp_profiles import ProfilePublication, decoded_program, expected_program
from wrapper_management import IP, PhysicalStream


def raw(tick, ca=0, cs=0):
    return dict(tick=tick, trusted=1, cs=cs, ca=ca, wck=0, write=0, read=0, **{"return": 0, "mr4": 9, "mr43": 0})


def profiles():
    import json

    result = [copy.deepcopy(v) for v in json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"]]
    for p in result:
        p["ranks"] = 1
        p["device"]["mr57"] = 3
    return result[0], result[24]


def transfer(current, target, *, accepted=True):
    p = PhysicalStream(copy.deepcopy(current))
    program = {}
    if accepted:
        at = 4
        expected = expected_program(current, target, 0, 1)
        for n, (address, value) in enumerate(expected):
            first, second = commands.mode_write(address, value)
            program[at], program[at + 1] = first, second
            at += 202 if n == 18 else 14
        release = max(program) + 84
        end = release + 2
    else:
        release, end = 4, 6
    raw_samples, trace = [], []
    for t in range(end):
        e = raw(t, program.get(t, 0), int(t in program))
        raw_samples.append(e)
        p.sample(e)
        trace.append(
            Sample(
                t < release,
                not accepted or t == 0 or t > release,
                0,
                2,
                1,
                accepted and t >= release - 1,
                True,
            )
        )
    return p, trace, raw_samples


class ProfileTests(unittest.TestCase):
    def test_source_empty_cannot_waive_decoded_open_rows(self):
        old, target = profiles()
        a, b = PhysicalStream(old), PhysicalStream(old)
        for tick, ca in enumerate(commands.activate(0, 0)):
            a.sample(raw(tick, ca, 1))
            b.sample(raw(tick))
        publication = ProfilePublication([old, old], [a, b], boot=0, epoch=3)
        with self.assertRaisesRegex(Violation, "independent drain"):
            publication.prepare(sequence=4, boot=0, target_fsp=1, targets=[target, target], source_settled=True)

    def test_exact_twenty_pairs_and_phase_failures(self):
        old, target = profiles()
        p, trace, pins = transfer(old, target)
        self.assertEqual(len(decoded_program(pins)), 20)
        self.assertEqual(len(trace), len(pins))
        for mutation in ("gap", "wrong_value", "missing_pair", "selected_nop"):
            altered = copy.deepcopy(pins)
            if mutation == "gap":
                altered.pop(0)
                altered[1]["tick"] += 1
            elif mutation == "wrong_value":
                altered[5]["ca"] ^= 128
            elif mutation == "missing_pair":
                altered[4]["cs"] = 0
            else:
                altered[2]["cs"] = 1
            if mutation == "wrong_value":
                self.assertNotEqual(decoded_program(altered), expected_program(old, target, 0, 1))
            else:
                with self.subTest(mutation=mutation), self.assertRaises(Violation):
                    decoded_program(altered)
        self.assertTrue(p.drained())

    def test_actual_ratio_handover_retains_memory_and_maintenance(self):
        old, target = profiles()
        a, ta, pa = transfer(old, target)
        b, tb, pb = transfer(old, target)
        a.phy.dram.memory[0, 2, 3, 4] = 0xAD  # Seeded checker-unit memory, not observed hardware data.
        a.phy.dram.maintenance.raa[0][2] = 7
        a.phy.dram.maintenance.extra[0] = True
        refresh = list(a.phy.dram.maintenance.last_refresh)
        ready = list(a.phy.dram.rank_ready)
        publication = ProfilePublication([old, old], [a, b], boot=0, epoch=3)
        publication.prepare(
            sequence=4,
            boot=0,
            target_fsp=1,
            targets=[target, target],
            source_settled=True,
        )
        publication.returned(0, ta, pa, sequence=4, boot=0)
        publication.returned(1, tb, pb, sequence=4, boot=0)
        publication.commit(0, sequence=4, boot=0)
        with self.assertRaisesRegex(Violation, "common publication"):
            publication.service(host_epoch=4, channel_epochs=[4, 3])
        publication.commit(1, sequence=4, boot=0)
        publication.published(sequence=4, boot=0, host_epoch=4, channel_epochs=[4, 4])
        publication.service(host_epoch=4, channel_epochs=[4, 4])
        self.assertEqual(a.p["ratio"], 2)
        self.assertEqual(a.phy.dram.memory[0, 2, 3, 4], 0xAD)
        self.assertEqual(a.phy.dram.maintenance.raa[0][2], 7)
        self.assertTrue(a.phy.dram.maintenance.extra[0])
        self.assertEqual(a.phy.dram.maintenance.last_refresh, refresh)
        self.assertEqual(a.phy.dram.rank_ready, ready)
        self.assertEqual(a.phy.dram.mr[0][16], 5)
        self.assertEqual(a.phy.dram.mr[0][1], target["mr"]["1"])

    def test_unanimous_decline_keeps_old_profile_epoch(self):
        old, target = profiles()
        a, ta, pa = transfer(old, target, accepted=False)
        b, tb, pb = transfer(old, target, accepted=False)
        publication = ProfilePublication([old, old], [a, b], boot=0, epoch=3)
        publication.prepare(
            sequence=4,
            boot=0,
            target_fsp=1,
            targets=[target, target],
            source_settled=True,
        )
        publication.returned(0, ta, pa, sequence=4, boot=0)
        publication.returned(1, tb, pb, sequence=4, boot=0)
        publication.declined(published_epoch=3, channel_epochs=[3, 3], prepared_valid=[False, False])
        publication.service(host_epoch=3, channel_epochs=[3, 3])
        self.assertEqual(a.p["ratio"], 4)

    def test_stale_high_is_decline_and_early_return_is_failure(self):
        old, target = profiles()
        a, ta, pa = transfer(old, target)
        b, _, _ = transfer(old, target)
        publication = ProfilePublication([old, old], [a, b], boot=0, epoch=3)
        publication.prepare(
            sequence=4,
            boot=0,
            target_fsp=1,
            targets=[target, target],
            source_settled=True,
        )
        altered = list(ta)
        altered[2] = Sample(True, True, 0, 2, 1, False, True)
        with self.assertRaisesRegex(Violation, "FSP handshake failed"):
            publication.returned(0, altered, pa, sequence=4, boot=0)

    def test_stale_partial_or_duplicate_publication_cannot_enable_service(self):
        old, target = profiles()
        a, ta, pa = transfer(old, target)
        b, tb, pb = transfer(old, target)
        publication = ProfilePublication([old, old], [a, b], boot=0, epoch=3)
        with self.assertRaisesRegex(Violation, "independent drain"):
            publication.prepare(
                sequence=4,
                boot=0,
                target_fsp=1,
                targets=[target, target],
                source_settled=False,
            )
        publication.prepare(
            sequence=4,
            boot=0,
            target_fsp=1,
            targets=[target, target],
            source_settled=True,
        )
        publication.returned(0, ta, pa, sequence=4, boot=0)
        with self.assertRaisesRegex(Violation, "partial/declined"):
            publication.commit(0, sequence=4, boot=0)
        publication.returned(1, tb, pb, sequence=4, boot=0)
        with self.assertRaisesRegex(Violation, "stale FSP commit"):
            publication.commit(0, sequence=4, boot=1)
        publication.commit(0, sequence=4, boot=0)
        with self.assertRaisesRegex(Violation, "duplicate FSP commit"):
            publication.commit(0, sequence=4, boot=0)
        with self.assertRaisesRegex(Violation, "atomic all-channel"):
            publication.published(sequence=4, boot=0, host_epoch=4, channel_epochs=[4, 3])

    def test_return_identity_and_declined_sequence_cannot_be_reused(self):
        old, target = profiles()
        a, ta, pa = transfer(old, target, accepted=False)
        b, tb, pb = transfer(old, target, accepted=False)
        publication = ProfilePublication([old, old], [a, b], boot=0, epoch=3)
        publication.prepare(sequence=4, boot=0, target_fsp=1, targets=[target, target], source_settled=True)
        with self.assertRaisesRegex(Violation, "stale FSP return identity"):
            publication.returned(0, ta, pa, sequence=3, boot=0)
        publication.returned(0, ta, pa, sequence=4, boot=0)
        publication.returned(1, tb, pb, sequence=4, boot=0)
        publication.declined(published_epoch=3, channel_epochs=[3, 3], prepared_valid=[False, False])
        with self.assertRaisesRegex(Violation, "stale/overlapping FSP prepare"):
            publication.prepare(sequence=4, boot=0, target_fsp=1, targets=[target, target], source_settled=True)

    def test_mixed_accept_decline_requires_fault(self):
        old, target = profiles()
        a, ta, pa = transfer(old, target)
        b, tb, pb = transfer(old, target, accepted=False)
        publication = ProfilePublication([old, old], [a, b], boot=0, epoch=3)
        publication.prepare(
            sequence=4,
            boot=0,
            target_fsp=1,
            targets=[target, target],
            source_settled=True,
        )
        publication.returned(0, ta, pa, sequence=4, boot=0)
        publication.returned(1, tb, pb, sequence=4, boot=0)
        with self.assertRaisesRegex(Violation, "mixed FSP outcome"):
            publication.declined(published_epoch=3, channel_epochs=[3, 3], prepared_valid=[False, False])
        with self.assertRaisesRegex(Violation, "common publication"):
            publication.service(host_epoch=3, channel_epochs=[3, 3])


if __name__ == "__main__":
    unittest.main()
