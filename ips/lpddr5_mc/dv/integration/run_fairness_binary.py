"""Replay a fairness matrix using one verified, unchanged RTL executable.

Runtime selection configures the real scheduler before traffic. The observer
checks its active age and service divisor at every admission and ACCESS issue.
Retain the original build report; it proves only its own recorded selection.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from itertools import product
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--modes", type=int, nargs="+", choices=(0, 1, 2), default=[0, 1, 2])
    parser.add_argument("--services", type=int, nargs="+", choices=(1, 2, 4), default=[1, 2, 4])
    parser.add_argument("--traffic", choices=("balanced", "row-churn"))
    args = parser.parse_args()
    checkout = args.checkout.resolve()
    origin = args.source_report.resolve()
    source = json.loads(origin.read_text())
    assert source["status"] == "passed" and not source["dirty"]
    assert source["family"] == "datapath" and source["fairness_mode"] in (0, 1, 2)
    source_traffic = source.get("fairness_traffic", "balanced")
    assert source_traffic in ("balanced", "row-churn")
    traffic = args.traffic if args.traffic is not None else source_traffic
    assert traffic in ("balanced", "row-churn")
    rows = int(traffic == "row-churn")
    assert all(c["exit_code"] == 0 for c in source["commands"])
    assert source["tooling_check"]["exit_code"] == 0 and len(source["source_sha256"]) >= 112
    assert len(set(args.modes)) == len(args.modes)
    assert len(set(args.services)) == len(args.services)
    binary = Path(source["commands"][2]["argv"][0])
    binary_sha = sha(binary)
    origin_sha = sha(origin)

    def verify_source():
        assert (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip() == source["commit"]
        )
        assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=checkout)
        assert all(sha(checkout / n) == h for n, h in source["source_sha256"].items())
        assert sha(binary) == binary_sha and sha(origin) == origin_sha

    verify_source()
    sys.path[:0] = [
        str(checkout / "ips/lpddr5_mc/dv/integration"),
        str(checkout / "ips/lpddr5_mc/dv"),
    ]
    from connected import check
    from fairness import qualify

    profile = json.loads((checkout / "ips/lpddr5_mc/config/contracts/profiles.json").read_text())["profiles"][4]
    root = args.destination.resolve()
    root.mkdir(parents=True, exist_ok=False)
    temporary = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) / "lpddr5-integration" / root.name
    temporary.mkdir(parents=True, exist_ok=False)
    result = {
        "source_commit": source["commit"],
        "source_report": str(origin),
        "source_report_sha256": origin_sha,
        "source_sha256": source["source_sha256"],
        "binary": str(binary),
        "binary_sha256": binary_sha,
        "script_sha256": sha(Path(__file__)),
        "build_configuration": source["configuration"],
        "fairness_traffic": traffic,
        "source_fairness_traffic": source_traffic,
        "services": args.services,
        "status": "running",
        "cases": {},
        "limits": ["Focused combined fairness cases only; unobserved age/suspension witnesses remain pending."],
    }
    matrix = root / "matrix.json"
    try:
        for service, mode in product(args.services, args.modes):
            verify_source()
            key = f"{('read', 'write', 'mixed')[mode]}-ck{service}"
            destination = root / key
            destination.mkdir()
            report = destination / "report.json"
            trace = destination / "observations.jsonl"
            fairness = destination / "fairness-observations.jsonl"
            log = destination / "simulation.log"
            command = [
                str(binary),
                f"+observations={trace}",
                f"+fairness_mode={mode}",
                f"+fairness_observations={fairness}",
                f"+fairness_rows={rows}",
                f"+fairness_service={service}",
            ]
            case = {
                "mode": mode,
                "service_div": service,
                "configuration": source["configuration"] | {"SERVICE_DIV": service},
                "fairness_traffic": traffic,
                "source_commit": source["commit"],
                "source_report_sha256": origin_sha,
                "matched_source_hashes": len(source["source_sha256"]),
                "binary_sha256": binary_sha,
                "argv": command,
                "status": "running",
            }
            report.write_text(json.dumps(case, indent=2) + "\n")
            start = time.monotonic()
            try:
                with log.open("w") as out:
                    process = subprocess.run(
                        command,
                        cwd=checkout,
                        stdout=out,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                case.update(
                    exit_code=process.returncode,
                    simulation_seconds=time.monotonic() - start,
                )
                assert process.returncode == 0 and "DATAPATH_FAIRNESS_PASS" in log.read_text()
                assert "DATAPATH_INTEGRATION_PASS" in log.read_text()
                case["external_observations"] = check(trace, profile, ranks=1, depth=64, id_bits=8, address_bits=40)
                case["fairness_observations"] = qualify(
                    fairness, temporary / "mutant.jsonl", case["external_observations"]
                )
                assert case["fairness_observations"]["divisor"] == service
                assert case["fairness_observations"]["mode"] == mode
                assert case["fairness_observations"]["row_churn"] == bool(rows)
                if "ips/lpddr5_mc/dv/integration/workload_costs.py" in source["source_sha256"]:
                    from workload_costs import measure

                    case["workload_costs"] = measure(trace)
                    for direction in ("read", "write"):
                        assert (
                            case["workload_costs"][direction]["successful_payload_bytes"]
                            == case["external_observations"][direction + "_bytes"]
                        )
                verify_source()
                case["status"] = "passed"
            except BaseException as error:
                case.update(status="failed", error=str(error))
                raise
            finally:
                case["total_seconds"] = time.monotonic() - start
                case["files"] = {str(path): sha(path) for path in (trace, fairness, log) if path.is_file()}
                report.write_text(json.dumps(case, indent=2) + "\n")
                result["cases"][key] = {
                    "report": str(report),
                    "report_sha256": sha(report),
                    "status": case["status"],
                }
                matrix.write_text(json.dumps(result, indent=2) + "\n")
            print(key, case["status"], case["total_seconds"], flush=True)
        result["status"] = "passed"
    except BaseException as error:
        result.update(status="failed", error=str(error))
        raise
    finally:
        matrix.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
