"""Timed regression of the actual wrapper recovery-phase helper."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    dv = Path(__file__).resolve().parent
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"])
    build = args.build.resolve()
    if not build.is_relative_to(storage) or build.exists() or args.report.exists():
        raise RuntimeError("Fresh isolated build and report required")
    build.mkdir(parents=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    ip = root / "ips/lpddr5_mc"
    inputs = [
        Path(__file__),
        dv / "wrapper_recovery.py",
        dv / "wrapper_recovery_tb.sv",
        dv / "wrapper_management_recovery_test.py",
        ip / "dv/system/host.py",
        ip / "rtl/contracts/lpddr5_mc_contract_pkg.sv",
        ip / "rtl/channel/lpddr5_mc_channel_pkg.sv",
        ip / "rtl/channel/lpddr5_mc_lifecycle.sv",
        ip / "rtl/channel/lpddr5_mc_channel_config.sv",
    ]
    snapshot = args.report.parent / "executed-inputs"
    snapshot.mkdir()
    for p in inputs:
        shutil.copyfile(p, snapshot / p.name)
    tool = storage / "lpddr5-contracts/verilator-5.006-3/usr"
    env = dict(os.environ, VERILATOR_ROOT=str(tool / "share/verilator"), VERILATOR_BIN="../../bin/verilator_bin")
    report = dict(
        status="running",
        commands=[],
        source_sha256={str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
    )
    commands = [
        [
            str(tool / "bin/verilator"),
            "--binary",
            "--timing",
            "--timescale-override",
            "1ns/1ps",
            "--assert",
            "-Wall",
            "--top-module",
            "wrapper_recovery_tb",
            "--Mdir",
            str(build),
            "-I" + str(dv),
            *[str(p) for p in inputs if p.suffix == ".sv" and p.name != "wrapper_recovery_tb.sv"],
            str(dv / "wrapper_recovery_tb.sv"),
            "-j",
            "2",
        ],
        [str(build / "Vwrapper_recovery_tb"), "+observations=" + str(args.report.parent / "observations.jsonl")],
    ]
    try:
        for index, argv in enumerate(commands):
            log = args.report.parent / f"command-{index}.log"
            start = time.monotonic()
            with log.open("w") as stream:
                result = subprocess.run(
                    argv, cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=60, check=False
                )
            report["commands"].append(
                dict(argv=argv, exit_code=result.returncode, seconds=time.monotonic() - start, log=str(log))
            )
            if result.returncode:
                raise RuntimeError(f"Timed recovery command failed; see {log}")
        assert "WRAPPER_RECOVERY_PASS channels=2 cycles=2" in log.read_text()
        rows = [json.loads(line) for line in (args.report.parent / "observations.jsonl").read_text().splitlines()]
        import sys

        sys.path.insert(0, str(ip / "dv"))
        from wrapper_management_recovery_test import replay, sensitivity

        history = replay(rows)
        probes = sensitivity(rows)
        assert len(history) == 4 and len(probes) == 58
        assert all(h["invalidation"]["active_zero"] and h["invalidation"]["prepared_zero"] for h in history)
        (args.report.parent / "recovery-verdicts.json").write_text(
            json.dumps(dict(history=history, sensitivity=probes), indent=2) + "\n"
        )
        assert all(
            hashlib.sha256(p.read_bytes()).hexdigest() == report["source_sha256"][str(p.relative_to(root))]
            for p in inputs
        )
        report.update(status="passed", observations=len(rows), recoveries=len(history), sensitivity=len(probes))
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
