"""Reproduce common-domain host behavior with the pinned simulator."""

import argparse
import hashlib
import json
import os
import re
import resource
import shutil
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]


def main() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--build-name", default="host-core")
    parser.add_argument("--data-bits", type=int, choices=(64, 128, 256), default=128)
    parser.add_argument("--queue-depth", type=int, choices=(16, 32, 64), default=16)
    parser.add_argument("--early-posted", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--compose-only",
        action="store_true",
        help="run focused cross-transaction forwarding/merge cases",
    )
    args = parser.parse_args()
    if args.report.exists():
        parser.error("report exists; preserve evidence and choose a new path")
    if not args.build_name.replace("-", "").replace("_", "").isalnum():
        parser.error("build name must contain only letters, digits, hyphens and underscores")
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]).resolve()
    build = storage / args.build_name
    build.mkdir(parents=True, exist_ok=True)
    tool = storage / "lpddr5-contracts/verilator-5.006-3"
    env = dict(os.environ)
    env["VERILATOR_ROOT"] = str(tool / "usr/share/verilator")
    env["VERILATOR_BIN"] = "../../bin/verilator_bin"
    sources = [
        "ips/lpddr5_mc/rtl/contracts/lpddr5_mc_contract_pkg.sv",
        "ips/lpddr5_mc/rtl/host/lpddr5_mc_host_pkg.sv",
        "ips/lpddr5_mc/rtl/host/lpddr5_mc_host_core.sv",
        "ips/lpddr5_mc/dv/host/core/lpddr5_mc_host_core_tb.sv",
    ]
    captured = {}
    captured_paths = []
    oracle_sources = (
        [
            "ips/lpddr5_mc/dv/host/compose_oracle.py",
            "ips/lpddr5_mc/dv/system/host.py",
            "ips/lpddr5_mc/dv/system/merge.py",
        ]
        if args.compose_only
        else []
    )
    for name in [*sources, "ips/lpddr5_mc/dv/host/core_run.py", *oracle_sources]:
        target = build / "captured-source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / name, target)
        captured[name] = hashlib.sha256(target.read_bytes()).hexdigest()
        if name in sources:
            captured_paths.append(str(target))
    commands = [
        [
            str(tool / "usr/bin/verilator"),
            "--binary",
            "--timing",
            "--timescale",
            "1ns/1ps",
            "--assert",
            # The pinned 5.006 release otherwise leaves task-driven packed
            # payload derivatives in initialization-only code. Keeping signal
            # writes visible preserves timed testbench sensitivity.
            "--public-flat-rw",
            "--output-split",
            "12000",
            "--output-split-cfuncs",
            "500",
            # Command-line make assignments override the generated -Os default.
            "-MAKEFLAGS",
            "OPT_FAST=-O0 OPT_SLOW=-O0",
            "-Wall",
            "--top-module",
            "lpddr5_mc_host_core_tb",
            f"-GDATA_BITS={args.data_bits}",
            f"-GQUEUE_DEPTH={args.queue_depth}",
            f"-GEARLY_POSTED=1'b{int(args.early_posted)}",
            f"-GCOMPOSE_ONLY=1'b{int(args.compose_only)}",
            "--Mdir",
            str(build),
            "-j",
            "2",
            *captured_paths,
        ],
        [str(build / "Vlpddr5_mc_host_core_tb"), *(["+debug"] if args.debug else [])],
    ]
    elaboration_cases = [
        {
            "PORTS": 1,
            "CHANNELS": 1,
            "RANKS": 1,
            "DATA_BITS": 64,
            "ID_BITS": 4,
            "ADDRESS_BITS": 32,
            "DEPTH": 16,
        },
        {
            "PORTS": 2,
            "CHANNELS": 2,
            "RANKS": 2,
            "DATA_BITS": 256,
            "ID_BITS": 16,
            "ADDRESS_BITS": 40,
            "DEPTH": 64,
        },
    ]
    if args.compose_only:
        commands.append(
            [
                os.sys.executable,
                str(build / "captured-source" / oracle_sources[0]),
                "--log",
                str(args.report.with_name(args.report.stem + "-1.log")),
                "--report",
                str(args.report.with_name(args.report.stem + "-oracle.json")),
                "--bus-bytes",
                str(args.data_bits // 8),
            ]
        )
    elaboration_offset = len(commands)
    for index, parameters in enumerate(elaboration_cases):
        commands.append(
            [
                str(tool / "usr/bin/verilator"),
                "--lint-only",
                "--assert",
                "-Wall",
                "--top-module",
                "lpddr5_mc_host_core",
                "--Mdir",
                str(build / f"elaboration-{index}"),
                *(f"-G{name}={value}" for name, value in parameters.items()),
                *captured_paths[:3],
            ]
        )
    records = []
    args.report.parent.mkdir(parents=True, exist_ok=True)
    passed = True
    test_count = 0
    for index, command in enumerate(commands):
        started = time.monotonic()
        result = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True, check=False)
        log = args.report.with_name(args.report.stem + f"-{index}.log")
        log.write_text(result.stdout + result.stderr)
        print(result.stdout + result.stderr, flush=True)
        records.append(
            {
                "argv": command,
                "elapsed_seconds": time.monotonic() - started,
                "exit_code": result.returncode,
                "log": str(log),
            }
        )
        if index == 1:
            match = re.search(r"(?:COMPOSE_RESULT|CORE_RESULT) tests=(\d+)", result.stdout)
            test_count = int(match.group(1)) if match else 0
            if test_count < (10 if args.compose_only else 24):
                passed = False
        if result.returncode:
            passed = False
            break
    source_changed = any(
        hashlib.sha256((REPO / name).read_bytes()).hexdigest() != digest for name, digest in captured.items()
    )
    passed = passed and not source_changed
    report = {
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO)),
        "source_files": captured,
        "source_changed_during_run": source_changed,
        "verilator": subprocess.check_output(
            [str(tool / "usr/bin/verilator"), "--version"], env=env, text=True
        ).strip(),
        "commands": records,
        "seed": 2095,
        "parameters": {
            "data_bits": args.data_bits,
            "queue_depth": args.queue_depth,
            "early_posted": args.early_posted,
            "compose_only": args.compose_only,
        },
        "test_families": test_count,
        "elaboration_cases": [
            {
                "parameters": parameters,
                "exit_code": records[index + elaboration_offset]["exit_code"]
                if len(records) > index + elaboration_offset
                else None,
            }
            for index, parameters in enumerate(elaboration_cases)
        ],
        "result": "passed" if passed else "failed",
        "scope": "common-domain host core with independent byte memory responder",
        "limitations": [
            "No asynchronous admission-order claim",
            "No DFI/protocol/ECC or throughput qualification",
        ],
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
