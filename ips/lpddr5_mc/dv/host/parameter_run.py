"""Check static contract admission and actual host-core parameter assertions."""

import argparse
import hashlib
import json
import os
import platform
import resource
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SOURCES = [
    "ips/lpddr5_mc/rtl/contracts/lpddr5_mc_contract_pkg.sv",
    "ips/lpddr5_mc/rtl/host/lpddr5_mc_host_pkg.sv",
    "ips/lpddr5_mc/rtl/host/lpddr5_mc_host_core.sv",
    "ips/lpddr5_mc/dv/host/core/parameter_tb.sv",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--build-name", default="host-parameters")
    args = parser.parse_args()
    if not args.build_name.replace("-", "").replace("_", "").isalnum():
        parser.error("build name must be a simple name")
    report = args.report.resolve()
    # Reserve all logs as a directory, rather than allowing different report
    # filenames to overwrite evidence from another run.
    report.parent.mkdir(parents=True, exist_ok=True)
    evidence = report.with_name(report.stem + "-logs")
    if report.exists() or evidence.exists():
        parser.error("report or logs exist; use a fresh path")
    evidence.mkdir()
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]).resolve()
    build = storage / "lpddr5-host-parameters" / args.build_name
    if build.exists():
        parser.error("build directory exists; use a fresh build name")
    build.mkdir(parents=True)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    tool = storage / "lpddr5-contracts/verilator-5.006-3/usr"
    env = {**os.environ, "VERILATOR_ROOT": str(tool / "share/verilator"), "VERILATOR_BIN": "../../bin/verilator_bin"}
    manifest = {
        name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
        for name in [*SOURCES, str(Path(__file__).relative_to(REPO))]
    }
    result = {
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO)),
        "source_sha256": manifest,
        "python": platform.python_version(),
        "commands": [],
        "expected_assertion_commands": [],
        "cases": [],
        "result": "running",
        "limitations": [
            "Static reset-held host-core assertions and canonical descriptor validation; "
            "no traffic, CDC or final release pass.",
            "Zero structural dimensions are checked as descriptors, not elaborated as negative-width RTL arrays.",
        ],
    }

    def run(argv: list[str], label: str, *, expected_assertion: bool = False) -> subprocess.CompletedProcess:
        started = time.monotonic()
        observed = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, check=False)
        log = evidence / (label + ".log")
        log.write_text(observed.stdout + observed.stderr)
        result["expected_assertion_commands" if expected_assertion else "commands"].append(
            {"argv": argv, "exit_code": observed.returncode, "seconds": time.monotonic() - started, "log": str(log)}
        )
        return observed

    try:
        version = run([str(tool / "bin/verilator"), "--version"], "version")
        version.check_returncode()
        result["verilator"] = version.stdout.strip()
        if not version.stdout.startswith("Verilator 5.006 "):
            raise ValueError("Unqualified simulator version")
        cases = [
            ("minimum-contract", {"CHECK_CONTRACT": "1'b1"}, True),
            (
                "maximum",
                {
                    "PORTS": 2,
                    "CHANNELS": 2,
                    "RANKS": 2,
                    "DATA_BITS": 256,
                    "ID_BITS": 16,
                    "ADDRESS_BITS": 40,
                    "DEPTH": 64,
                },
                True,
            ),
            *[
                (name.lower() + "-" + str(value), {name: value}, False)
                for name, values in (
                    ("PORTS", (3,)),
                    ("CHANNELS", (3,)),
                    ("RANKS", (3,)),
                    ("DATA_BITS", (32, 192)),
                    ("ID_BITS", (3, 17)),
                    ("ADDRESS_BITS", (31, 41)),
                    ("DEPTH", (8, 24, 65)),
                )
                for value in values
            ],
        ]
        for label, parameters, admitted in cases:
            directory = build / label
            compiled = run(
                [
                    str(tool / "bin/verilator"),
                    "--binary",
                    "--timing",
                    "--timescale",
                    "1ns/1ps",
                    "--assert",
                    "--public-flat-rw",
                    "-Wall",
                    "--top-module",
                    "parameter_tb",
                    "--Mdir",
                    str(directory),
                    "-j",
                    "2",
                    *(f"-G{name}={value}" for name, value in parameters.items()),
                    *SOURCES,
                ],
                label + "-compile",
            )
            compiled.check_returncode()
            simulation = run([str(directory / "Vparameter_tb")], label + "-run", expected_assertion=not admitted)
            output = simulation.stdout + simulation.stderr
            marker = "PARAMETER_CORE_ADMITTED" in output
            # An arbitrary crash/compiler failure cannot establish rejection:
            # require the actual production core's parameter assertion.
            passed = (
                (simulation.returncode == 0 and marker)
                if admitted
                else (
                    simulation.returncode != 0
                    and not marker
                    and "lpddr5_mc_host_core.sv" in output
                    and "Assertion failed" in output
                )
            )
            if label == "minimum-contract":
                passed = passed and "PARAMETER_CONTRACT_PASS accepted=176904 rejected=227448 boundaries=30" in output
            result["cases"].append(
                {"name": label, "parameters": parameters, "expected_admitted": admitted, "passed": passed}
            )
            print(label, "passed" if passed else "failed", flush=True)
            if not passed:
                raise ValueError("Parameter admission mismatch: " + label)
        result["result"] = "passed"
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        result["result"] = "failed"
        result["error"] = str(error)
    finally:
        result["source_changed_during_run"] = any(
            hashlib.sha256((REPO / name).read_bytes()).hexdigest() != digest for name, digest in manifest.items()
        )
        if result["source_changed_during_run"]:
            result["result"] = "failed"
        report.write_text(json.dumps(result, indent=2) + "\n")
    if result["result"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
