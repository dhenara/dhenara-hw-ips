"""Qualify system verification fixtures and preserve exact-source evidence.

This is deliberately separate from combined-controller qualification. All
compiler products use the supplied tool-storage root. Evidence is exclusive
create: a failed or earlier run is never overwritten to imply a later pass.
"""

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
IP = REPO / "ips/lpddr5_mc"
FOUNDATION = "8fefc83619f66eb89a6522727adeed797a4cc4c2"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--build-name", default="system-fixture")
    p.add_argument("--require-clean", action="store_true")
    p.add_argument("--skip-foundation", action="store_true", help="iteration only; not a complete source check")
    args = p.parse_args()
    if not args.evidence.is_absolute() or args.evidence.exists():
        p.error("evidence must be a new absolute directory")
    if not args.build_name.replace("-", "").replace("_", "").isalnum():
        p.error("build name must be a simple name")
    args.evidence.mkdir(parents=True)
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]).resolve()
    build = storage / "lpddr5-system" / args.build_name
    build.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        PYTHONPATH=str(IP / "dv"),
        PYTHONPYCACHEPREFIX=str(storage / "lpddr5-system/pycache"),
        RUFF_CACHE_DIR=str(storage / "lpddr5-system/ruff-cache"),
    )
    base = storage / "lpddr5-contracts"
    tool = base / "verilator-5.006-3"
    env.update(VERILATOR_ROOT=str(tool / "usr/share/verilator"), VERILATOR_BIN="../../bin/verilator_bin")
    verilator = str(tool / "usr/bin/verilator")
    ruff = str(base / "python-tools/bin/ruff")
    commands = []
    report = {
        "kind": "environment_fixture_qualification",
        "passed": False,
        "controller_qualified": False,
        "seed": 2095,
        "commands": commands,
        "limits": [
            "Only independent environment and fixtures are exercised; no combined controller is in this baseline.",
            "No formal proof, metastability, vendor PHY, fresh-host installation, remote CI or physical sign-off.",
            "Sampled trace adapters require independently observed source handshakes and physical-memory effects.",
        ],
    }
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    def run(argv, name, expected_failure=False):
        start = time.monotonic()
        result = subprocess.run(
            argv, cwd=REPO, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300
        )
        (args.evidence / name).write_text(result.stdout)
        commands.append(
            {
                "argv": argv,
                "log": name,
                "exit_code": result.returncode,
                "elapsed_seconds": time.monotonic() - start,
                "expected_failure": expected_failure,
            }
        )
        print(f"{name}: exit={result.returncode}", flush=True)
        if (result.returncode == 0) == expected_failure:
            raise RuntimeError(f"Unexpected command result: {name}; inspect retained log")
        return result.stdout

    try:
        report["source_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
        report["source_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO))
        if args.require_clean and report["source_dirty"]:
            raise ValueError("clean committed source required")
        files = subprocess.check_output(["git", "ls-files", "ips/lpddr5_mc"], cwd=REPO, text=True).splitlines()
        report["source_sha256"] = {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in files}
        report["python"] = sys.version
        report["verilator"] = subprocess.check_output([verilator, "--version"], env=env, text=True).strip()
        report["ruff"] = subprocess.check_output([ruff, "--version"], env=env, text=True).strip()
        report["compiler"] = subprocess.check_output(["g++", "--version"], text=True).splitlines()[0]
        if sys.version_info[:3] != (3, 13, 18) or not report["verilator"].startswith("Verilator 5.006 "):
            raise ValueError("unqualified Python/simulator version")
        if report["ruff"] != "ruff 0.17.4":
            raise ValueError("unqualified Ruff version")
        owned = ["ips/lpddr5_mc/dv/system", "ips/lpddr5_mc/tools/rtl"]
        config = ["--config", "ips/lpddr5_mc/tools/ruff.toml"]
        run([ruff, "check", *config, *owned], "lint.log")
        run([ruff, "format", "--check", *config, *owned], "format.log")
        run([sys.executable, "-m", "system.qualify", "--report", str(args.evidence / "tests.json")], "tests.log")
        # Only fixture-specific unused metadata and imported package timescales
        # are waived. Width, lifetime, latch and assertion failures stay fatal.
        run(
            [
                verilator,
                "--binary",
                "--timing",
                "--assert",
                "-Wall",
                "-Wno-UNUSEDSIGNAL",
                "-Wno-TIMESCALEMOD",
                "--top-module",
                "lpddr5_mc_system_tb",
                "--Mdir",
                str(build),
                "-j",
                "2",
                "-f",
                "ips/lpddr5_mc/dv/system/rtl/files.f",
            ],
            "compile.log",
        )
        binary = str(build / "Vlpddr5_mc_system_tb")
        good = run([binary], "rtl.log")
        if "SYSTEM_FIXTURE_PASS" not in good:
            raise ValueError("fixture completion marker missing")
        trace = args.evidence / "rtl.jsonl"
        trace.write_text("\n".join(line for line in good.splitlines() if line.startswith("{")) + "\n")
        run(
            [sys.executable, "-m", "system.trace", str(trace), "--report", str(args.evidence / "trace.json")],
            "trace.log",
        )
        bad = run([binary, "+bad_stability"], "sensitivity.log", expected_failure=True)
        if "ready/valid payload changed while stalled" not in bad:
            raise ValueError("negative fixture failed for an unexpected reason")
        stream_build = build / "stream"
        run(
            [
                verilator,
                "--binary",
                "--timing",
                "--assert",
                "-Wall",
                "-Wno-UNUSEDSIGNAL",
                "-Wno-TIMESCALEMOD",
                "--top-module",
                "lpddr5_mc_stream_tb",
                "--Mdir",
                str(stream_build),
                "-j",
                "2",
                "-f",
                "ips/lpddr5_mc/dv/system/rtl/stream.f",
            ],
            "stream-compile.log",
        )
        stream_binary = str(stream_build / "Vlpddr5_mc_stream_tb")
        for direction in ("read", "write"):
            log = "stream-" + direction + ".log"
            output = run([stream_binary, *(["+read_run"] if direction == "read" else [])], log)
            if "STREAM_FIXTURE_PASS" not in output:
                raise ValueError("stream fixture completion marker missing")
            for divisor in (1, 2, 4):
                run(
                    [
                        sys.executable,
                        "-m",
                        "system.benchmark_trace",
                        str(args.evidence / log),
                        "--direction",
                        direction,
                        "--service",
                        str(divisor),
                        "--report",
                        str(args.evidence / f"benchmark-{direction}-{divisor}.json"),
                    ],
                    f"benchmark-{direction}-{divisor}.log",
                )
        truncated = args.evidence / "stream-truncated.log"
        truncated.write_text(
            "\n".join(
                line
                for line in (args.evidence / "stream-read.log").read_text().splitlines()
                if not line.startswith('{"kind":"end"')
            )
            + "\n"
        )
        rejected = run(
            [
                sys.executable,
                "-m",
                "system.benchmark_trace",
                str(truncated),
                "--direction",
                "read",
                "--service",
                "2",
                "--report",
                str(args.evidence / "truncated.json"),
            ],
            "benchmark-sensitivity.log",
            expected_failure=True,
        )
        if "missing/truncated 100-period observation" not in rejected:
            raise ValueError("benchmark negative fixture failed for an unexpected reason")
        varied = run([stream_binary, "+read_run", "+half1=1375", "+phase=911", "+reset_ps=9999"], "stream-varied.log")
        if "STREAM_FIXTURE_PASS" not in varied:
            raise ValueError("varied-clock fixture completion marker missing")
        run(["git", "merge-base", "--is-ancestor", FOUNDATION, "HEAD"], "foundation-ancestry.log")
        run(["git", "diff", "--check", FOUNDATION], "whitespace.log")
        if not args.skip_foundation:
            run(
                [
                    sys.executable,
                    "ips/lpddr5_mc/tools/check.py",
                    "--report",
                    str(args.evidence / "foundation.json"),
                    "--build-subdir",
                    args.build_name + "-foundation",
                ],
                "foundation.log",
            )
        report["foundation_executed"] = not args.skip_foundation
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        (args.evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
