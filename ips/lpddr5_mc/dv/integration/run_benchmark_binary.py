"""Run another development mode using an unchanged, previously built fixture.

Compilation inputs must match the retained build report. This records a new
simulation only; replay and final requirement qualification remain separate.
"""

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--direction", choices=("read", "write"), required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--service", type=int, choices=(1, 2, 4), required=True)
    args = parser.parse_args()
    source = json.loads(args.source_report.read_text())
    if source["family"] != "controller" or not source.get("benchmark_direction") or source["commands"][1]["exit_code"]:
        raise SystemExit("A completed controller benchmark build is required")
    # Python replay changes do not change this executable. Check all HDL,
    # generated-input authors and canonical profile/register inputs instead.
    inputs = {
        path: digest
        for path, digest in source["source_sha256"].items()
        if Path(path).suffix in (".sv", ".svh", ".f", ".json") or Path(path).name in ("vectors.py", "raw_parity.py")
    }
    if not inputs or any(sha(REPO / path) != digest for path, digest in inputs.items()):
        raise SystemExit("Compilation inputs changed; build a new fixture")
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) / "lpddr5-integration" / destination.name
    storage.mkdir(parents=True, exist_ok=False)
    trace = storage / "observations.jsonl"
    original = source["commands"][2]["argv"]
    command = [
        arg
        for arg in original
        if not arg.startswith(
            (
                "+observations=",
                "+recovery_observations=",
                "+benchmark_direction=",
                "+benchmark_service=",
                "+benchmark_smoke=",
            )
        )
    ]
    command += [
        f"+observations={trace}",
        f"+benchmark_direction={int(args.direction == 'write')}",
        f"+benchmark_service={args.service}",
        f"+benchmark_smoke={int(args.smoke)}",
    ]
    binary = Path(command[0])
    images = Path(next(arg.removeprefix("+images=") for arg in command if arg.startswith("+images=")))
    artifacts = [binary, images, images.with_name("expected_profiles.svh"), images.with_name("read_link_encoder.svh")]
    result = {
        "source_report": str(args.source_report.resolve()),
        "source_report_sha256": sha(args.source_report),
        "compilation_inputs": inputs,
        "runtime_inputs": {str(path): sha(path) for path in artifacts},
        "command": command,
        "direction": args.direction,
        "service": args.service,
        "smoke_only": args.smoke,
        "status": "running",
        "limits": ["Development simulation only; independent replay and final qualification remain pending."],
    }
    report = destination / "run.json"
    report.write_text(json.dumps(result, indent=2) + "\n")
    start = time.monotonic()
    try:
        with (destination / "simulation.log").open("w") as log:
            run = subprocess.run(command, cwd=REPO, stdout=log, stderr=subprocess.STDOUT, check=False)
        result["exit_code"] = run.returncode
        result["seconds"] = time.monotonic() - start
        if (
            run.returncode
            or "CONTROLLER_INTEGRATION_PASS benchmark" not in (destination / "simulation.log").read_text()
        ):
            raise RuntimeError("Benchmark simulation failed; inspect retained log")
        if any(sha(REPO / path) != digest for path, digest in inputs.items()):
            raise RuntimeError("Compilation inputs changed during simulation")
        result["status"] = "simulation_passed"
    except BaseException as error:
        result["status"] = "failed"
        result["error"] = str(error)
        raise
    finally:
        if trace.exists():
            with trace.open("rb") as raw, gzip.open(destination / "observations.jsonl.gz", "wb") as compressed:
                shutil.copyfileobj(raw, compressed)
        report.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
