"""Actual RTL asynchronous AXI admission, reset settlement and transport measurement."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--build-subdir", default="host-axi")
    parser.add_argument("--default-only", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.build_subdir):
        parser.error("build subdirectory must be a simple name")
    if args.report.exists():
        parser.error("report exists; choose a new path")
    repo = Path(__file__).resolve().parents[4]
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"])
    tool = storage / "lpddr5-contracts/verilator-5.006-3"
    verilator = str(tool / "usr/bin/verilator")
    env = dict(os.environ, VERILATOR_ROOT=str(tool / "usr/share/verilator"), VERILATOR_BIN="../../bin/verilator_bin")
    out = storage / args.build_subdir
    out.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    records = []

    def run(argv: list[str], label: str) -> str:
        start = time.monotonic()
        result = subprocess.run(argv, cwd=repo, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log = args.report.parent / (args.report.stem + "-" + label + ".log")
        log.write_text(result.stdout)
        records.append(
            {"argv": argv, "seconds": time.monotonic() - start, "exit_code": result.returncode, "log": str(log)}
        )
        print(label, result.returncode, flush=True)
        if result.returncode or label.startswith("run"):
            print(result.stdout, end="", flush=True)
        result.check_returncode()
        return result.stdout

    version = run([verilator, "--version"], "version").strip()
    if not version.startswith("Verilator 5.006 "):
        raise SystemExit("Unqualified simulator version")
    common = [
        verilator,
        "--assert",
        "-Wall",
        "--public-flat-rw",
        "--timescale",
        "1ns/1ps",
        "-f",
        "ips/lpddr5_mc/rtl/host/axi-files.f",
    ]
    variants = (
        [(128, 16, 8, 40)]
        if args.default_only
        else [
            (128, 16, 8, 40),
            (64, 16, 4, 32),
            (128, 32, 8, 36),
            (256, 64, 16, 40),
        ]
    )
    observed = []
    for bits, depth, ids, addresses in variants:
        name = f"b{bits}-d{depth}-i{ids}-a{addresses}"
        build = out / name
        run(
            [
                *common,
                "--binary",
                "--timing",
                "--top-module",
                "lpddr5_mc_axi_bridge_tb",
                "-j",
                "2",
                "--Mdir",
                str(build),
                f"-GDATA_BITS={bits}",
                f"-GDEPTH={depth}",
                f"-GID_BITS={ids}",
                f"-GADDRESS_BITS={addresses}",
                "ips/lpddr5_mc/dv/host/axi/lpddr5_mc_axi_bridge_tb.sv",
            ],
            "compile-" + name,
        )
        output = run([str(build / "Vlpddr5_mc_axi_bridge_tb")], "run-" + name)
        if "AXI_BRIDGE_PASS" not in output:
            raise SystemExit("Missing actual RTL completion witness")
        observed.append(output)
    paths = list((repo / "ips/lpddr5_mc/rtl/host").glob("lpddr5_mc_axi*.sv"))
    paths += [
        repo / "ips/lpddr5_mc/rtl/host/axi-files.f",
        Path(__file__).resolve(),
        repo / "ips/lpddr5_mc/dv/host/axi/lpddr5_mc_axi_bridge_tb.sv",
    ]
    paths += list((repo / "ips/lpddr5_mc/rtl/cdc").glob("*.sv"))
    paths += [
        repo / "ips/lpddr5_mc/rtl/host/lpddr5_mc_host_pkg.sv",
        repo / "ips/lpddr5_mc/rtl/contracts/lpddr5_mc_contract_pkg.sv",
    ]
    report = {
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True)),
        "source_sha256": {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "simulator": version,
        "commands": records,
        "observed": observed,
        "limitations": [
            "The memory-facing fixture is an independent transport consumer, not controller throughput.",
            "Serialized preaccept admission is measured at AXI400/core800; final memory benchmark remains pending.",
            "Digital simulation does not establish physical CDC/metastability qualification.",
        ],
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
