"""The runner must bind real physical ownership to the cap-selected granule.

Only external processes and later cap/cost qualification are substituted. The
CLI, runner dispatch, source scoreboard, decoded PHY/WCK/return checks and
read/write ownership all execute on model-generated synthetic observations.
"""

import contextlib
import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from connected_scaling_test import fixture
from contracts import commands
from system.host import Violation
from system.phy import DigitalPhy
from wck_observations_test import fixture as wck_fixture

SPEC = importlib.util.spec_from_file_location("cap_integration_runner", Path(__file__).with_name("check.py"))
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class PhysicalComplete(Exception):
    """Stop after the real connected replay, before separate policy/cost checks."""


def transfer(f, address, write, value):
    """Reuse the independent bus generator for BL16 or BL32 at either offset."""
    f.value = value
    f.emit()
    rows, _, _ = wck_fixture(f.p, write, beats=len(value) // 2)
    bank, row, column = address >> 11 & 15, address >> 15, address >> 5 & 63
    act = commands.activate(row, bank)
    rows = [[i, 0, 0, 0] for i in range(25)] + [[i + 25, cs, ca, w] for i, cs, ca, w in rows]
    rows[0][1:3], rows[1][1:3] = [1, act[0]], [1, act[1]]
    rows[31][2] = commands.access(write, len(value) // 2, bank, column)
    for _, cs, ca, wck in rows:
        f.emit(cs=cs, ca=ca, wck=wck)
    for _ in range(70):
        f.emit()
    f.emit(cs=1, ca=commands.maintenance("PRE"))
    for _ in range(26):
        f.emit()


def trace(granule, offset=0):
    """Two source ports/channels complete one nonzero write/read each."""
    events = [dict(kind="header", version=1, ports=2, channels=2, bus_bytes=16)]
    for channel in (0, 1):
        f = fixture()
        f.p = copy.deepcopy(json.loads((runner.IP / "config/contracts/profiles.json").read_text())["profiles"][0])
        f.p["ranks"] = 1
        f.gen = DigitalPhy(f.p)
        value = bytes((byte + 17 * channel) % 256 for byte in range(granule))
        transfer(f, offset, True, value)
        write_end = len(f.frames) - 2
        transfer(f, offset, False, value)
        logical = channel * 64 + offset
        events.append(
            dict(
                kind="aw", ps=10, port=channel, id=1, address=logical, length=granule // 16 - 1, size=4, burst=1, lock=0
            )
        )
        for word in range(granule // 16):
            events.append(
                dict(
                    kind="w",
                    ps=20 + word,
                    port=channel,
                    data=int.from_bytes(value[word * 16 : (word + 1) * 16], "little"),
                    strobe=65535,
                    last=int(word == granule // 16 - 1),
                )
            )
        for frame, _, _ in f.frames[1:]:
            ck = frame["tick"] - 1
            events.append(
                dict(
                    kind="phy",
                    ps=625 + ck * 1250,
                    ck=ck,
                    channel=channel,
                    **{key: frame[key] for key in ("cs", "ca", "wck", "write", "read", "return")},
                )
            )
        write_ps, end_ps = 625 + write_end * 1250 + 1, 625 + (len(f.frames) - 2) * 1250 + 1
        events.extend(
            [
                dict(kind="b", ps=write_ps, port=channel, id=1, response=0, ready=1),
                dict(
                    kind="ar",
                    ps=write_ps + 1,
                    port=channel,
                    id=2,
                    address=logical,
                    length=granule // 16 - 1,
                    size=4,
                    burst=1,
                    lock=0,
                ),
            ]
        )
        for word in range(granule // 16):
            events.append(
                dict(
                    kind="r",
                    ps=end_ps + word,
                    port=channel,
                    id=2,
                    data=int.from_bytes(value[word * 16 : (word + 1) * 16], "little"),
                    response=0,
                    last=int(word == granule // 16 - 1),
                    ready=1,
                )
            )
    events.append(dict(kind="end", ps=end_ps + 10))
    return events


class CapGranuleRunnerTests(unittest.TestCase):
    def invoke(self, granule, *, mode=None, offset=0, mutate=None):
        with tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) as temporary:
            root = Path(temporary)
            report = root / "proof/report.json"
            events = trace(granule, offset)
            if mutate:
                mutate(events)
            argv = ["check.py", "--family", "datapath", "--report", str(report), "--build-name", "synthetic-cap"]
            argv += (
                ["--granule-bytes", str(granule)]
                if mode is None
                else ["--cap-mode", str(mode), "--cap-granule-bytes", str(granule)]
            )
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=runner.REPO, text=True)

            def source_observation(command, **kwargs):
                if command == ["git", "rev-parse", "HEAD"]:
                    return commit
                if command == ["git", "status", "--porcelain"]:
                    return ""
                raise AssertionError(f"Unexpected source observation: {command}")

            def process(command, **kwargs):
                if Path(command[0]).name == "Vdatapath_tb":
                    report.with_name("observations.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
                    kwargs["stdout"].write("DATAPATH_INTEGRATION_PASS\n")
                elif Path(command[0]).name != "verilator" and command[1:3] != ["-m", "unittest"]:
                    raise AssertionError(f"Unexpected external command: {command}")
                return subprocess.CompletedProcess(command, 0)

            with (
                patch.object(sys, "argv", argv),
                patch.object(sys, "pycache_prefix", sys.pycache_prefix),
                patch.dict(os.environ, DHENARA_AGENT_TOOL_STORAGE_ROOT=temporary),
                patch.object(subprocess, "check_output", side_effect=source_observation),
                patch.object(subprocess, "run", side_effect=process),
                patch("caps.qualify", side_effect=PhysicalComplete),
                patch("workload_costs.measure", side_effect=PhysicalComplete),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(PhysicalComplete):
                    runner.main()
            return json.loads(report.read_text())

    def assert_complete(self, report, granule):
        physical = report["external_observations"]
        self.assertEqual((physical["write_bytes"], physical["read_bytes"]), (2 * granule, 2 * granule))
        self.assertEqual((physical["physical_write_words"], physical["returned_slices"]), (granule // 2, granule))
        self.assertEqual(len(physical["physical_return_timing"]), 2)
        self.assertTrue(all(channel["disabled_epochs"] == 2 for channel in physical["wck_by_channel"]))

    def test_cap32_in_both_modes_completes_both_half_offsets(self):
        for mode in (1, 2):
            for offset in (0, 32):
                with self.subTest(mode=mode, offset=offset):
                    report = self.invoke(32, mode=mode, offset=offset)
                    self.assert_complete(report, 32)
                    self.assertEqual(report["cap_granule_bytes"], 32)
                    self.assertEqual(report["configuration"]["GRANULE_BYTES"], 64)

    def test_cap64_in_both_modes_preserves_complete_ownership(self):
        for mode in (1, 2):
            with self.subTest(mode=mode):
                self.assert_complete(self.invoke(64, mode=mode), 64)

    def test_plain32_and64_keep_their_ordinary_granule_selection(self):
        for granule in (32, 64):
            with self.subTest(granule=granule):
                report = self.invoke(granule)
                self.assert_complete(report, granule)
                self.assertEqual(report["configuration"]["GRANULE_BYTES"], granule)

    def test_cap32_does_not_waive_physical_payload_or_return_readiness(self):
        def early_write_response(events):
            next(e for e in events if e["kind"] == "b")["ps"] = 50

        def missing_return(events):
            next(e for e in events if e["kind"] == "phy" and e["return"] >> 32 & 255)["return"] = 0

        def changed_write_data(events):
            next(e for e in events if e["kind"] == "phy" and e["write"] >> 48)["write"] ^= 1 << 48

        for mutate, message in (
            (early_write_response, "complete physical payload and pin tail"),
            (missing_return, "missing|rotation|return|deadline"),
            (changed_write_data, "unexpected physical write byte"),
        ):
            with self.subTest(mutation=mutate.__name__), self.assertRaisesRegex((Violation, ValueError), message):
                self.invoke(32, mode=1, mutate=mutate)


if __name__ == "__main__":
    unittest.main()
