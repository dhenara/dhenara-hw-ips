"""Independent timing probes must distinguish ready work from live admissions."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from cap_eligibility import Eligibility, probe
from caps import check
from contracts.commands import access, activate, cas
from system.dram import DramModel
from system.host import Violation
from system.measurement import Caps


class OppositeEligibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads((Path(__file__).resolve().parents[2] / "config/contracts/profiles.json").read_text())[
            "profiles"
        ][4] | {"ranks": 1}

    @staticmethod
    def request(write=0, bank=4, ck=10):
        return {
            "kind": "admit",
            "channel": 0,
            "ck": ck,
            "tag": 1,
            "generation": 1,
            "write": write,
            "bytes": 64,
            "rank": 0,
            "bank": bank,
            "row": 0,
            "column": 0,
        }

    def prefix(self):
        model = DramModel(self.profile)
        for at, ca in (
            (0, activate(0, 0)[0]),
            (1, activate(0, 0)[1]),
            (12, activate(0, 4)[0]),
            (13, activate(0, 4)[1]),
        ):
            model.command(at, 1, ca)
        model.command(32, 1, access(True, 32, 0, 0))
        return model

    def test_live_read_is_blocked_then_becomes_ready(self):
        model, request = self.prefix(), self.request()
        before = copy.deepcopy(model.__dict__)
        ready, reason = probe(model, 40, request)
        self.assertFalse(ready)
        self.assertEqual(reason, "write/read turnaround")
        self.assertEqual(probe(model, 100, request), (True, "RD"))
        self.assertEqual(model.__dict__, before, "probe changed actual replay")
        caps = Caps(512, (64, 64), True)
        caps.issue(32, 1, 64, False)
        caps.issue(40, 1, 64, ready)
        illegal = Caps(512, (64, 64), True)
        illegal.issue(32, 1, 64, False)
        with self.assertRaisesRegex(Violation, "exceeds byte cap"):
            illegal.issue(100, 1, 64, probe(model, 100, request)[0])
        strict = Caps(512, (64, 64), False)
        strict.issue(32, 1, 64, False)
        with self.assertRaisesRegex(Violation, "exceeds byte cap"):
            strict.issue(40, 1, 32, False)
        # A new fixed window replenishes exactly the declared allowance.
        strict.issue(512, 1, 64, False)

    def test_concurrent_header_cannot_bypass_required_oracle(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            trace = Path(temporary) / "caps.jsonl"
            header = {
                "kind": "header",
                "channels": 2,
                "mode": 2,
                "window": 512,
                "read_cap": 64,
                "write_cap": 64,
                "granule_bytes": 64,
                "mixed": 2,
            }
            for version in (2, 4):
                trace.write_text(json.dumps(header | {"version": version}) + "\n")
                with self.assertRaisesRegex(Violation, "eligibility inputs"):
                    check(trace, {})

    def test_pre_and_activate_pairs_are_probed(self):
        model = self.prefix()
        request = self.request(bank=0)
        request["row"] = 1
        self.assertEqual(probe(model, 40, request), (False, "PRE/REF recovery or open bank"))
        self.assertEqual(probe(model, 100, request), (True, "PRE"))
        request["bank"] = 8
        self.assertEqual(probe(model, 100, request), (True, "ACT1"))

    def test_sync_reservation_does_not_hide_ready_opposite(self):
        model = self.prefix()
        model.command(99, 1, cas("write"))
        self.assertEqual(probe(model, 100, self.request()), (True, "RD"))
        with self.assertRaisesRegex(Violation, "CAS/access adjacency/direction/rank"):
            model.command(100, 1, access(False, 32, 4, 0))

    def raw(self):
        commands = {
            0: activate(0, 0)[0],
            1: activate(0, 0)[1],
            12: activate(0, 4)[0],
            13: activate(0, 4)[1],
            32: access(True, 32, 0, 0),
            40: access(True, 32, 0, 0),
        }
        events = [{"kind": "header", "channels": 2}]
        for ck in range(110):
            for channel in range(2):
                events.append(
                    {"kind": "phy", "ck": ck, "channel": channel, "cs": int(ck in commands), "ca": commands.get(ck, 0)}
                )
        events.append({"kind": "end"})
        return events

    def test_binding_uses_raw_commands_and_prior_admissions(self):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            raw = Path(temporary) / "raw.jsonl"
            events = self.raw()
            raw.write_text("".join(json.dumps(e) + "\n" for e in events))
            oracle = Eligibility(raw, self.profile)
            issue = self.request(write=1, bank=0, ck=40) | {"kind": "issue"}
            pending = self.request()
            self.assertFalse(oracle.issue(issue, {1: pending}, True))
            self.assertEqual(oracle.borrowed_with_opposite, [1, 0])
            # Same-edge admission cannot affect the preceding arbitration.
            pending["ck"] = 40
            oracle = Eligibility(raw, self.profile)
            self.assertFalse(oracle.issue(issue, {1: pending}, True))
            self.assertEqual(oracle.concurrent_issues, [0, 0])
            with self.assertRaisesRegex(Violation, "disagrees with decoded"):
                oracle.issue(issue | {"bank": 4}, {}, False)
            with self.assertRaisesRegex(Violation, "physical command bijection"):
                oracle.finish(False)
            events.pop(8)
            raw.write_text("".join(json.dumps(e) + "\n" for e in events))
            with self.assertRaisesRegex(Violation, "missing/repeated CK"):
                Eligibility(raw, self.profile)


if __name__ == "__main__":
    unittest.main()
