"""Check the real host assembly across independent source clocks and resets."""

import argparse
import hashlib
import json
import os
import resource
import subprocess
import time
from pathlib import Path


def main() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--build-subdir", default="host-assembly")
    args = parser.parse_args()
    if args.report.exists():
        parser.error("preserve existing evidence; choose a new report")
    if not args.build_subdir.replace("-", "").replace("_", "").isalnum():
        parser.error("build subdirectory must be a simple name")
    repo = Path(__file__).resolve().parents[4]
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]).resolve()
    build = storage / args.build_subdir
    tool = storage / "lpddr5-contracts/verilator-5.006-3"
    env = dict(
        os.environ,
        VERILATOR_ROOT=str(tool / "usr/share/verilator"),
        VERILATOR_BIN="../../bin/verilator_bin",
    )
    paths = []
    for subdir in ("rtl/host", "rtl/cdc", "rtl/contracts"):
        paths.extend((repo / "ips/lpddr5_mc" / subdir).glob("*.sv"))
        paths.extend((repo / "ips/lpddr5_mc" / subdir).glob("*.f"))
    paths.extend(
        repo / path
        for path in (
            "ips/lpddr5_mc/rtl/control/lpddr5_mc_apb_metadata_pkg.sv",
            "ips/lpddr5_mc/dv/host/assembly/lpddr5_mc_host_assembly_tb.sv",
            "ips/lpddr5_mc/dv/host/assembly_run.py",
        )
    )
    manifest = {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    commands = [
        [
            str(tool / "usr/bin/verilator"),
            "--binary",
            "--timing",
            "--public-flat-rw",
            "--assert",
            "--output-split",
            "12000",
            "--output-split-cfuncs",
            "500",
            "-MAKEFLAGS",
            "OPT_FAST=-O0 OPT_SLOW=-O0",
            "-Wall",
            "--timescale",
            "1ns/1ps",
            "--top-module",
            "lpddr5_mc_host_assembly_tb",
            "--Mdir",
            str(build),
            "-j",
            "2",
            "-f",
            "ips/lpddr5_mc/rtl/host/files.f",
            "ips/lpddr5_mc/dv/host/assembly/lpddr5_mc_host_assembly_tb.sv",
        ],
        [str(build / "Vlpddr5_mc_host_assembly_tb")],
    ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    records = []
    passed = True
    for i, command in enumerate(commands):
        start = time.monotonic()
        log = args.report.with_name(args.report.stem + f"-{i}.log")
        with log.open("w") as output:
            result = subprocess.run(command, cwd=repo, env=env, stdout=output, stderr=subprocess.STDOUT, check=False)
        observed = log.read_text()
        records.append(
            {
                "argv": command,
                "elapsed_seconds": time.monotonic() - start,
                "exit_code": result.returncode,
                "log": str(log),
            }
        )
        print(f"assembly phase {i}: exit {result.returncode}", flush=True)
        if result.returncode or i == 1:
            print(observed, flush=True)
        if result.returncode or (i == 1 and "HOST_ASSEMBLY_PASS tests=7" not in observed):
            passed = False
            break
    source_changed = any(
        hashlib.sha256((repo / p).read_bytes()).hexdigest() != digest for p, digest in manifest.items()
    )
    passed = passed and not source_changed
    report = {
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=repo)),
        "source_files": manifest,
        "source_changed_during_run": source_changed,
        "commands": records,
        "parameters": {"ports": 2, "channels": 2, "data_bits": 128, "depth": 16},
        "clocks_ns": {"host": 1.25, "axi0": 2.5, "axi1": 3.5, "axi1_phase": 0.375},
        "result": "passed" if passed else "failed",
        "limitations": [
            "Byte memory completion witnesses do not qualify the channel, DFI, ECC or combined throughput."
        ],
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
