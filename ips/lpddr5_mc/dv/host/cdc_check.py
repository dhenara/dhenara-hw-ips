"""Compile and exercise actual asynchronous transports with independent clocks."""

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
    parser.add_argument("--build-subdir", default="host-cdc")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.build_subdir):
        parser.error("build subdirectory must be a simple name")
    if args.report.exists():
        parser.error("report already exists; choose a fresh path to retain earlier evidence")
    repo = Path(__file__).resolve().parents[4]
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"])
    tools = storage / "lpddr5-contracts" / "verilator-5.006-3"
    verilator = tools / "usr/bin/verilator"
    env = dict(os.environ, VERILATOR_ROOT=str(tools / "usr/share/verilator"), VERILATOR_BIN="../../bin/verilator_bin")
    root = storage / args.build_subdir
    root.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    records = []

    def run(command: list[str], label: str) -> str:
        started = time.monotonic()
        result = subprocess.run(command, cwd=repo, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log = args.report.parent / f"{args.report.stem}-{label}.log"
        log.write_text(result.stdout)
        records.append(
            {"argv": command, "seconds": time.monotonic() - started, "exit_code": result.returncode, "log": str(log)}
        )
        print(result.stdout, end="", flush=True)
        result.check_returncode()
        return result.stdout

    version = run([str(verilator), "--version"], "version").strip()
    if not version.startswith("Verilator 5.006 "):
        raise SystemExit("Expected qualified Verilator 5.006")
    sources = (repo / "ips/lpddr5_mc/rtl/cdc/files.f").read_text().splitlines()
    for top in ("lpddr5_mc_async_fifo", "lpddr5_mc_mailbox"):
        run([str(verilator), "--lint-only", "--assert", "-Wall", "--top-module", top, *sources], f"lint-{top}")
    run(
        [
            str(verilator),
            "--lint-only",
            "--assert",
            "-Wall",
            "--top-module",
            "lpddr5_mc_mailbox",
            "-GWIDTH=23525",
            *sources,
        ],
        "lint-large-config-mailbox",
    )
    variants = [(2, 1, 3, 5, 2), (16, 80, 3, 5, 2), (64, 1024, 5, 2, 1)]
    observed = []
    for depth, width, wh, rh, phase in variants:
        name = f"d{depth}-w{width}-c{wh}-{rh}-p{phase}"
        command = [
            str(verilator),
            "--binary",
            "--timing",
            "--assert",
            "-Wall",
            "--top-module",
            "lpddr5_mc_cdc_tb",
            "-j",
            "2",
            "--Mdir",
            str(root / name),
            f"-GDEPTH={depth}",
            f"-GWIDTH={width}",
            f"-GW_HALF={wh}",
            f"-GR_HALF={rh}",
            f"-GR_PHASE={phase}",
            *sources,
            "ips/lpddr5_mc/dv/host/cdc/lpddr5_mc_cdc_tb.sv",
        ]
        run(command, "compile-" + name)
        output = run([str(root / name / "Vlpddr5_mc_cdc_tb")], "run-" + name)
        if "CDC_PASS" not in output:
            raise SystemExit("Missing CDC behavior completion witness")
        observed.append(output)
    paths = [
        *sources,
        "ips/lpddr5_mc/rtl/cdc/files.f",
        "ips/lpddr5_mc/dv/host/cdc/lpddr5_mc_cdc_tb.sv",
        "ips/lpddr5_mc/dv/host/cdc_check.py",
    ]
    report = {
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True)),
        "source_sha256": {p: hashlib.sha256((repo / p).read_bytes()).hexdigest() for p in paths},
        "simulator": version,
        "seed": 2095,
        "commands": records,
        "observed": observed,
        "limitations": [
            "Digital simulation does not establish metastability MTBF or physical CDC sign-off.",
            "Endpoint reset aborts transport; the owner must settle accepted AXI work "
            "or coordinate host reset before explicit rearm.",
        ],
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
