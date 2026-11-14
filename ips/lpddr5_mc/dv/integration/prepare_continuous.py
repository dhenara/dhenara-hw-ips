"""Qualify observer fixtures and lint connected stimuli without running RTL.

Compilation checks source connectivity only. Real continuous replay and dirty
management simulation still require an explicitly selected acceptance run.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

IP = Path(__file__).resolve().parents[2]
REPO = IP.parents[1]


def manifest():
    return {
        str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
        for prefix in ("rtl", "dv", "config")
        for p in sorted((IP / prefix).rglob("*"))
        if p.is_file() and p.suffix in (".sv", ".svh", ".f", ".py", ".json") and "__pycache__" not in p.parts
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--build-name", default="continuous-prepare")
    args = parser.parse_args()
    if not args.build_name.replace("-", "").replace("_", "").isalnum():
        parser.error("build name must be a simple name")
    if args.report.exists():
        parser.error("refuse to overwrite evidence")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"])
    build = storage / "lpddr5-integration" / args.build_name
    build.mkdir(parents=True, exist_ok=True)
    sys.pycache_prefix = str(build / "pycache")
    from vectors import generate

    before = manifest()
    generate(build)
    tool = storage / "lpddr5-contracts/verilator-5.006-3/usr"
    env = {
        **os.environ,
        "VERILATOR_ROOT": str(tool / "share/verilator"),
        "VERILATOR_BIN": "../../bin/verilator_bin",
        "PYTHONPATH": os.pathsep.join((str(IP / "dv"), str(IP / "dv/integration"))),
        "PYTHONPYCACHEPREFIX": str(build / "pycache"),
    }
    records = []
    commands = [[sys.executable, "-m", "unittest", "continuous_test", "continuous_physical_test", "-v"]]
    for top in ("controller_tb", "lpddr5_mc_channel_tb"):
        commands.append(
            [
                str(tool / "bin/verilator"),
                "--lint-only",
                "--timing",
                "--timescale-override",
                "1ns/1ps",
                "--assert",
                "--public-flat-rw",
                "-Wall",
                "--top-module",
                top,
                "--Mdir",
                str(build / top),
                "-I" + str(build),
                "-I" + str(IP / "dv/integration"),
                "-I" + str(IP / "dv/channel"),
                *(["-GINLINE_LIFECYCLE=1'b1"] if top == "controller_tb" else []),
                "-f",
                str(IP / "rtl/files.f"),
                str(IP / "dv/channel/lpddr5_mc_channel_memory.sv"),
                *([str(IP / "dv/integration/lpddr5_mc_observer.sv")] if top == "controller_tb" else []),
                str(
                    IP
                    / (
                        "dv/integration/controller_tb.sv"
                        if top == "controller_tb"
                        else "dv/channel/lpddr5_mc_channel_tb.sv"
                    )
                ),
            ]
        )
    passed = True
    for i, argv in enumerate(commands):
        log = args.report.with_name(f"prepare-{i}.log")
        started = time.monotonic()
        with log.open("x") as stream:
            result = subprocess.run(argv, cwd=REPO, env=env, stdout=stream, stderr=subprocess.STDOUT, check=False)
        records.append(dict(argv=argv, exit_code=result.returncode, seconds=time.monotonic() - started, log=str(log)))
        passed &= result.returncode == 0
    changed = before != manifest()
    result = dict(
        status="passed" if passed and not changed else "failed",
        source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        source_files=before,
        source_changed_during_run=changed,
        commands=records,
        generated_inputs={
            str(p.relative_to(build)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in build.glob("*")
            if p.is_file()
        },
        limitations=["Observer unit fixtures and lint only; no connected RTL simulation or continuous pin replay."],
    )
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
