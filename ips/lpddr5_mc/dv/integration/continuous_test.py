"""Independent sensitive fixtures; these are observer qualification, not RTL runs."""

import copy
import json
import unittest
from pathlib import Path

from continuous import DATA_BYTES, Channel, InlineDrain, check
from system.ecc import InlineWitness
from system.host import Violation

IP = Path(__file__).resolve().parents[2]
PROFILE = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"][0]


def sample(ck, **changes):
    value = dict(
        kind="transition",
        ck=ck,
        ps=ck * PROFILE["ck_ps"],
        channel=0,
        reset_n=1,
        memory_reset=0,
        destructive=0,
        confirmed=1,
        settled=1,
        enabled=0,
        host_fault=0,
        fault=0,
        trusted=0,
        owner=1,
        boot=0,
        config=1,
        config_valid=1,
        trained=0,
        training_epoch=0,
        drained=1,
        pin_drained=1,
        ecc_drained=1,
        inline_idle=1,
        inline_dirty=0,
        command_valid=0,
        command=0,
        update=0,
        phy_update=0,
        fsp=0,
        power=0,
        master=0,
        refresh_request=0,
        refresh_done=0,
        rank_valid=1,
        rank_sr=0,
        thermal=9,
        ratio=PROFILE["ratio"],
        cs=0,
        ca=0,
        wck=0,
        write=0,
        read=0,
        **{"return": 0},
    )
    value.update(changes)
    return value


def trace():
    # Preserve a real fault interval, host settlement, destructive confirmation,
    # a changed boot/config epoch and fresh training. No refresh retention is
    # asserted in the untrusted interval, although every CA/WCK sample remains.
    return [
        {"kind": "header", "version": 1, "channels": 1, "ranks": 1},
        sample(0, reset_n=0, config=0, config_valid=0),
        sample(1),
        sample(2, trusted=1, trained=1, owner=2),
        sample(3, trusted=1, trained=1, owner=2, update=1),
        sample(4, fault=1, host_fault=1, owner=4),
        sample(5, fault=1, host_fault=1, owner=4),
        sample(6, destructive=1, fault=1, host_fault=1, owner=4),
        sample(7, boot=1, config=0, config_valid=0, training_epoch=1),
        sample(8, boot=1, config=2, training_epoch=1),
        sample(9, boot=1, config=2, training_epoch=1, trained=1, trusted=1, owner=2),
        {"kind": "end", "ck": 10},
    ]


def granule(kind, *, at=0, tag=10, write=1, mask=(1 << 64) - 1, data=0):
    return {
        "kind": kind,
        "channel": 0,
        "tag": tag,
        "generation": 0 if kind == "inline_physical" else 2,
        "boot": 0,
        "config": 1,
        "rank": 0,
        "row": at >> 15,
        "bank": (at >> 11) & 15,
        "column": (at >> 5) & 63,
        "bytes": 64,
        "write": write,
        "mask": mask,
        "data": data,
    }


def result(q, *, failed=False, data=0):
    return {
        **{k: q[k] for k in ("tag", "generation", "boot", "config", "write")},
        "kind": "inline_result",
        "physical": 1,
        "integrity": 1,
        "failed": int(failed),
        "response": 2 if failed else 0,
        "data": data,
    }


def inline_fixture(*, failed=False, correction=False):
    # Nonzero neighboring check bytes must survive a single protected granule.
    data = bytes(range(64))
    old_checks = bytearray((i * 7) % 256 for i in range(64))
    for w in range(8):
        old_checks[w] = InlineWitness.check_byte(int.from_bytes(data[w * 8 : w * 8 + 8], "little"))
    new = bytes(100 + i for i in range(64))
    mask = sum(1 << i for i in range(64) if i % 3)
    q = granule(
        "inline_admit",
        data=int.from_bytes(new, "little"),
        mask=mask,
        write=int(not correction),
    )
    events = [q]
    physical = []
    checks = bytearray(old_checks)
    merged = data if correction else bytes(new[i] if mask >> i & 1 else data[i] for i in range(64))
    for w in range(8):
        checks[w] = InlineWitness.check_byte(int.from_bytes(merged[w * 8 : w * 8 + 8], "little"))
    for n, (at, wr, value) in enumerate(
        (
            (DATA_BYTES, 0, old_checks),
            (0, 0, data),
            (0, 1, merged),
            (DATA_BYTES, 1, checks),
        )
    ):
        p = granule(
            "inline_physical",
            at=at,
            tag=n + 1,
            write=wr,
            data=int.from_bytes(value, "little"),
        )
        physical.append(p)
        returned = int.from_bytes(value, "little") ^ (int(correction and n == 1) << 2)
        events += [p, result(p, data=returned, failed=failed and n == 3)]
    events.append({**result(q, failed=failed), "kind": "inline_complete"})
    return events


class ContinuousTests(unittest.TestCase):
    def test_full_fault_reset_epochs(self):
        report = check(trace(), PROFILE)["channels"][0]
        self.assertEqual(report["entries"]["update"], 1)
        self.assertEqual(
            report["refresh_suspended"],
            [
                {"start": 0, "end": 2, "reason": "untrusted memory"},
                {"start": 4, "end": 9, "reason": "untrusted memory"},
            ],
        )

    def test_sensitive_boundaries(self):
        for name, mutate, reason in (
            ("missing_fault_ck", lambda t: t.pop(6), "missing/repeated continuous CK"),
            (
                "stale_training",
                lambda t: t[10].update(training_epoch=0),
                "trust without fresh",
            ),
            (
                "reset_before_settlement",
                lambda t: t[7].update(settled=0),
                "destructive reset",
            ),
            (
                "reset_without_confirmation",
                lambda t: t[7].update(confirmed=0),
                "destructive reset",
            ),
            (
                "dirty_management",
                lambda t: t[4].update(ecc_drained=0),
                "management entry",
            ),
            ("untrusted_enable", lambda t: t[6].update(enabled=1), "enabled untrusted"),
            (
                "unknown_ca_during_fault",
                lambda t: t[6].update(cs=1, ca=16),
                "unknown/unsupported CA",
            ),
            ("wck_during_fault", lambda t: t[6].update(wck=1), "WCK activity"),
            (
                "reserved_pair_hole",
                lambda t: t[4].update(cs=1, ca=7),
                "reserved CA pair",
            ),
            ("phy_owner_ca", lambda t: t[4].update(owner=1, cs=1), "while PHY owns"),
        ):
            with self.subTest(name=name):
                t = copy.deepcopy(trace())
                mutate(t)
                with self.assertRaisesRegex(ValueError, reason):
                    check(t, PROFILE)

    def test_refresh_cannot_restart_at_ownership_change(self):
        t = trace()[:4]
        for ck in range(3, 3130):
            t.append(sample(ck, trusted=1, trained=1, owner=2))
        t.append({"kind": "end", "ck": 3130})
        with self.assertRaisesRegex(ValueError, "missed periodic refresh"):
            check(t, PROFILE)

    def test_inline_partial_and_shared_check(self):
        m = InlineDrain()
        for e in inline_fixture():
            m.event(e)
        self.assertEqual(m.completed, 1)
        self.assertFalse(m.dirty)
        self.assertEqual(set(m.versions.values()), {1})

    def test_dirty_management_composition(self):
        for kind, command in (("update", 3), ("fsp", 7), ("power", 4), ("master", 0)):
            for failed in (False, True):
                with self.subTest(kind=kind, failed=failed):
                    c = Channel({**PROFILE, "ranks": 1})
                    c.sample(sample(0, reset_n=0))
                    c.sample(sample(1, trained=1, trusted=1, owner=2))
                    t = inline_fixture(failed=failed)
                    # Reach the interval after actual data-result acceptance
                    # and before check issue/result. Entry must reject even
                    # if an aggregate drain signal is incorrectly asserted.
                    for e in t[:7]:
                        c.inline.event(e)
                    self.assertTrue(c.inline.dirty)
                    with self.assertRaisesRegex(Violation, "management entry"):
                        copy.deepcopy(c).sample(sample(2, trained=1, trusted=1, owner=2, **{kind: 1}))
                    c.sample(
                        sample(
                            2,
                            trained=1,
                            trusted=1,
                            owner=2,
                            drained=0,
                            pin_drained=0,
                            ecc_drained=0,
                            inline_idle=0,
                            command_valid=bool(command),
                            command=command,
                        )
                    )
                    for e in t[7:]:
                        c.inline.event(e)
                    c.sample(sample(3, trained=1, trusted=1, owner=2, **{kind: 1}))
                    self.assertEqual(c.entries[kind], 1)
                    self.assertEqual(len(c.inline.poison), 8 if failed else 0)

    def test_fsp_owned_mrw_and_broadcast(self):
        t = [
            {"kind": "header", "version": 1, "channels": 1, "ranks": 2},
            sample(0, reset_n=0),
            sample(1, trusted=1, trained=1, owner=2),
            sample(2, trusted=1, trained=1, owner=1, fsp=1, fsp_busy=1, cs=3, ca=88),
            sample(3, trusted=1, trained=1, owner=1, fsp=1, fsp_busy=1, cs=3, ca=8),
            {"kind": "end", "ck": 4},
        ]
        self.assertEqual(check(t, PROFILE)["channels"][0]["entries"]["fsp"], 1)
        for change in ({"fsp_busy": 0}, {"read": 1 << 16}, {"write": 1 << 24}):
            mutant = copy.deepcopy(t)
            mutant[3].update(change)
            with self.assertRaisesRegex(ValueError, "while PHY owns"):
                check(mutant, PROFILE)

    def test_explicit_profile_change(self):
        target = next(
            p
            for p in json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"]
            if p["ratio"] != PROFILE["ratio"] and not p["read_link"] and not p["write_link"]
        )
        t = trace()[:4]
        t += [
            sample(3, config=2, trusted=1, trained=1, owner=2, ratio=target["ratio"]),
            {"kind": "end", "ck": 4},
        ]
        with self.assertRaisesRegex(ValueError, "matching profile"):
            check(t, PROFILE)
        report = check(t, PROFILE, {2: target})
        self.assertEqual(len(report["channels"][0]["prior_wck_profiles"]), 1)

    def test_inline_correction_scrubs_under_same_lock(self):
        m = InlineDrain()
        for e in inline_fixture(correction=True):
            m.event(e)
        self.assertEqual(m.scrubs, 1)

    def test_inline_failed_check_poison_and_full_rewrite(self):
        m = InlineDrain()
        for e in inline_fixture(failed=True):
            m.event(e)
        self.assertEqual(len(m.poison), 8)
        q = granule("inline_admit", tag=20, data=0)
        m.event(q)
        # Full rewrites bypass old poisoned data; all untouched check bytes stay.
        fixture = inline_fixture()
        for e in fixture[1:5]:
            m.event(e)
        self.assertFalse(m.live["failed"])

    def test_inline_mutants(self):
        for name, change, reason in (
            ("second_lock", lambda t: t.insert(1, dict(t[0])), "before inline lock"),
            ("missing_check_result", lambda t: t.pop(-2), "completion identity"),
            (
                "wrong_data",
                lambda t: t[5].update(data=t[5]["data"] ^ 1),
                "shared check-line",
            ),
            (
                "neighbor_check_corruption",
                lambda t: t[7].update(data=t[7]["data"] ^ (1 << 200)),
                "shared check-line",
            ),
            ("stale_result", lambda t: t[2].update(config=0), "stale inline"),
            (
                "failed_result_then_success",
                lambda t: t[-2].update(failed=1, response=2),
                "success before",
            ),
        ):
            with self.subTest(name=name):
                t = inline_fixture()
                change(t)
                m = InlineDrain()
                with self.assertRaisesRegex(Violation, reason):
                    for e in t:
                        m.event(e)


if __name__ == "__main__":
    unittest.main(verbosity=2)
