"""Compile and exercise the delivered programming example with ordered MMIO."""

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
IP = REPO / "ips/lpddr5_mc"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--build-name", required=True)
    args = parser.parse_args()
    if not args.report.is_absolute() or args.report.exists():
        parser.error("report must be a new absolute path")
    if not args.build_name.replace("-", "").replace("_", "").isalnum():
        parser.error("build name must be a simple name")
    build = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) / "lpddr5-integration" / args.build_name
    build.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    files = [
        Path(__file__),
        IP / "dv/integration/control_example_test.c",
        IP / "sw/examples/lpddr5_mc_control.c",
        IP / "sw/generated/lpddr5_mc_registers.h",
    ]
    hashes = {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    report = {
        "status": "running",
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO)),
        "source_sha256": hashes,
        "commands": [],
        "limits": ["MMIO callback model; real APB admission is exercised separately by the RTL suites."],
    }
    try:
        for index, argv in enumerate(
            [
                ["gcc", "--version"],
                ["gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", str(files[1]), "-o", str(build / "control")],
                [str(build / "control")],
            ]
        ):
            start = time.monotonic()
            run = subprocess.run(
                argv, cwd=REPO, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
            )
            log = args.report.with_name(f"{args.report.stem}-{index}.log")
            log.write_text(run.stdout)
            report["commands"].append(
                {"argv": argv, "exit_code": run.returncode, "seconds": time.monotonic() - start, "log": str(log)}
            )
            if run.returncode or (index == 2 and "CONTROL_EXAMPLE_PASS" not in run.stdout):
                raise RuntimeError(f"Example behavior failed; see {log}")
        if hashes != {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}:
            raise RuntimeError("Source changed during checks")
        report["status"] = "passed"
    except BaseException as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Control example passed: {args.report}")


if __name__ == "__main__":
    main()
