"""Check lease admission against actual source events and a serial byte oracle."""

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


def main() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--build-subdir", default="host-lease")
    parser.add_argument("--compose-only", action="store_true")
    args = parser.parse_args()
    if args.report.exists():
        parser.error("preserve existing evidence; choose a new report")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.build_subdir):
        parser.error("build subdirectory must be a simple name")
    repo = Path(__file__).resolve().parents[4]
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]).resolve()
    build = storage / args.build_subdir
    captured_repo = build / "captured-source"
    tool = storage / "lpddr5-contracts/verilator-5.006-3"
    env = dict(
        os.environ,
        VERILATOR_ROOT=str(tool / "usr/share/verilator"),
        VERILATOR_BIN="../../bin/verilator_bin",
    )
    source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    source_dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=repo))
    paths: set[Path] = set()

    def include_list(relative: str) -> None:
        path = repo / relative
        if path in paths:
            return
        paths.add(path)
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("-f "):
                include_list(line[3:].strip())
            elif line.endswith(".sv"):
                paths.add(repo / line)
            else:
                raise ValueError(f"Unsupported source-list entry: {relative}: {line}")

    include_list("ips/lpddr5_mc/rtl/host/files.f")
    paths.update(
        repo / path
        for path in (
            "ips/lpddr5_mc/dv/host/lease/lpddr5_mc_host_lease_tb.sv",
            "ips/lpddr5_mc/dv/host/lease_run.py",
        )
    )
    manifest = {}
    for path in sorted(paths):
        relative = path.relative_to(repo)
        target = captured_repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        manifest[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
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
            "lpddr5_mc_host_lease_tb",
            f"-GCOMPOSE_ONLY=1'b{int(args.compose_only)}",
            "--Mdir",
            str(build),
            "-j",
            "2",
            "-f",
            "ips/lpddr5_mc/rtl/host/files.f",
            "ips/lpddr5_mc/dv/host/lease/lpddr5_mc_host_lease_tb.sv",
        ],
        [str(build / "Vlpddr5_mc_host_lease_tb")],
    ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    records = []
    passed = True
    families = 0
    sensitivity = []
    for i, command in enumerate(commands):
        start = time.monotonic()
        log = args.report.with_name(args.report.stem + f"-{i}.log")
        with log.open("w") as stream:
            result = subprocess.run(
                command,
                cwd=captured_repo,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        output = log.read_text()
        records.append(
            {
                "argv": command,
                "elapsed_seconds": time.monotonic() - start,
                "exit_code": result.returncode,
                "log": str(log),
            }
        )
        print(f"lease phase {i}: exit {result.returncode}", flush=True)
        if result.returncode or i == 1:
            print(output, flush=True)
        if i == 1:
            match = re.search(r"LEASE_RESULT tests=(\d+) .*reversal=1", output)
            families = int(match.group(1)) if match else 0
            if families < (1 if args.compose_only else 18):
                passed = False
        if result.returncode:
            passed = False
            break
    if passed:
        for label, marker in (
            ("READ", "serial source oracle read mismatch"),
            ("MEMORY", "unexpected byte effect"),
        ):
            command = [
                str(build / "Vlpddr5_mc_host_lease_tb"),
                "+LEASE_CORRUPT_" + label,
            ]
            log = args.report.with_name(args.report.stem + "-sensitivity-" + label.lower() + ".log")
            start = time.monotonic()
            with log.open("w") as stream:
                result = subprocess.run(
                    command,
                    cwd=captured_repo,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                )
            caught = result.returncode != 0 and marker in log.read_text()
            passed = passed and caught
            sensitivity.append(
                {
                    "argv": command,
                    "elapsed_seconds": time.monotonic() - start,
                    "exit_code": result.returncode,
                    "log": str(log),
                    "expected_oracle_failure": marker,
                    "caught": caught,
                }
            )
            print(f"lease sensitivity {label}: caught={caught}", flush=True)
    source_changed = any(
        hashlib.sha256((repo / path).read_bytes()).hexdigest() != digest for path, digest in manifest.items()
    )
    passed = passed and not source_changed
    report = {
        "source_commit": source_commit,
        "source_dirty": source_dirty,
        "source_files": manifest,
        "source_changed_during_run": source_changed,
        "verilator": subprocess.check_output(
            [str(tool / "usr/bin/verilator"), "--version"], env=env, text=True
        ).strip(),
        "commands": records,
        "oracle_sensitivity": sensitivity,
        "parameters": {
            "ports": 2,
            "channels": 2,
            "data_bits": 128,
            "depth": 16,
            "compose_only": args.compose_only,
        },
        "clocks_ns": {"host": 1.25, "axi0": 2.5, "axi1": 2.5, "axi1_phase": 0.375},
        "test_families": families,
        "result": "passed" if passed else "failed",
        "limitations": [
            "Behavioral physical/integrity witnesses do not qualify channel timing, DFI, ECC or combined throughput.",
            "Representative parameters and directed schedules are not exhaustive formal CDC/reset proof.",
        ],
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
