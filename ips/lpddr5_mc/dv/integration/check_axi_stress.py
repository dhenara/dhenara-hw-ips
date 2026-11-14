"""Replay a retained AXI stress simulation with the current independent checks."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from axi_stress import measure
from connected import check
from workload_costs import measure as costs

REPO = Path(__file__).resolve().parents[4]
IP = REPO / "ips/lpddr5_mc"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    original = json.loads(args.source_report.read_text())
    require_simulation = (
        original["family"] == "datapath"
        and original["configuration"].get("AXI_STRESS") == 1
        and len(original["commands"]) == 3
        and all(c["exit_code"] == 0 for c in original["commands"])
    )
    if not require_simulation or args.report.exists():
        raise SystemExit("Require completed stress simulation commands and a fresh replay report")
    inputs = {
        n: h
        for n, h in original["source_sha256"].items()
        if Path(n).suffix in (".sv", ".svh", ".f", ".json") or Path(n).name in ("vectors.py", "raw_parity.py")
    }
    if not inputs or any(sha(REPO / n) != h for n, h in inputs.items()):
        raise SystemExit("Compilation inputs changed; a new stress simulation is required")
    trace = args.source_report.with_name("observations.jsonl")
    log = Path(original["commands"][2]["log"])
    files = [args.source_report, trace, log, Path(original["commands"][2]["argv"][0])]
    source_files = subprocess.check_output(["git", "ls-files", "-z", "ips/lpddr5_mc"], cwd=REPO).decode().split("\0")
    before = {n: sha(REPO / n) for n in source_files if n}
    result = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO)),
        "source_sha256": before,
        "simulation_commit": original["commit"],
        "compilation_inputs": inputs,
        "files": {str(p.resolve()): sha(p) for p in files},
        "configuration": original["configuration"],
        "seed": original["seed"],
        "status": "running",
        "limits": ["Retained simulation with source-matched HDL/inputs; checker replay does not rebuild the fixture."],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    try:
        if result["dirty"]:
            raise RuntimeError("Clean checker source required")
        cfg = original["configuration"]
        profiles = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"]
        result["external_observations"] = check(
            trace,
            profiles[cfg["PROFILE_NUMBER"]],
            ranks=cfg["RANKS"],
            depth=cfg["DEPTH"],
            mapped_end=(1 << 30) * cfg["CHANNELS"] * cfg["RANKS"] if cfg["RANKS"] == 2 else 1 << 20,
            id_bits=cfg["ID_BITS"],
            address_bits=cfg["ADDRESS_BITS"],
            granule_bytes=cfg["GRANULE_BYTES"],
        )
        result["axi_stress_observations"] = measure(trace, log, original["seed"])
        result["workload_costs"] = costs(trace)
        if any(sha(REPO / n) != h for n, h in before.items()) or any(
            sha(Path(n)) != h for n, h in result["files"].items()
        ):
            raise RuntimeError("Checker source or simulation inputs changed during replay")
        result["status"] = "passed"
    except BaseException as error:
        result.update(status="failed", error=str(error))
        raise
    finally:
        with args.report.open("x") as out:
            out.write(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
