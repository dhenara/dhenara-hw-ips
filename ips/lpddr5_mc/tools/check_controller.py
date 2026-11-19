"""Run owning suites and representative connected checks with retained evidence.

This is integration smoke, not the final configuration/coverage/performance
qualification. Each child keeps its own commands, manifests and original logs.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from process_group import RunInterrupted, run_group

REPO = Path(__file__).resolve().parents[3]
IP = REPO / "ips/lpddr5_mc"


def source() -> dict:
    names = (
        subprocess.check_output(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
                "Makefile",
                "ips/lpddr5_mc",
                ".github/workflows/lpddr5-contracts.yml",
            ],
            cwd=REPO,
        )
        .decode()
        .split("\0")
    )
    return {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO)),
        "sha256": {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in sorted(set(names)) if name},
    }


def plan(evidence: Path, name: str, suite: str) -> list[dict]:
    steps = []

    def add(label: str, runner: str, arguments: list[str], report: Path, timeout: int = 900) -> None:
        steps.append(
            {
                "name": label,
                "argv": [sys.executable, str(IP / runner), *arguments],
                "report": str(report),
                "timeout_seconds": timeout,
            }
        )

    if suite in ("all", "owning"):
        # The host aggregate includes the complete foundation gate once.
        report = evidence / "host-foundation/report.json"
        add(
            "host-foundation",
            "dv/host/check.py",
            ["--report", str(report), "--build-subdir", name + "-host"],
            report,
            1800,
        )
        for label, extra in (("channel", []), ("channel-refpb", ["--family", "channel", "--per-bank-refresh"])):
            report = evidence / label / "report.json"
            add(
                label,
                "dv/channel/check.py",
                ["--report", str(report), "--build-name", name + "-" + label, *extra],
                report,
                1800,
            )
        report = evidence / "system/report.json"
        add(
            "system",
            "tools/rtl/check.py",
            ["--evidence", str(report.parent), "--build-name", name + "-system", "--skip-foundation"],
            report,
            1800,
        )
    if suite in ("all", "connected"):
        report = evidence / "control-example/report.json"
        add(
            "control-example",
            "dv/integration/control_example.py",
            ["--report", str(report), "--build-name", name + "-control-example"],
            report,
        )
        cases = [
            (family, family, [])
            for family in (
                "notification",
                "decoder",
                "publication",
                "apb_publication",
                "coordinator",
                "controller",
                "datapath",
            )
        ]
        cases += [
            ("wrapper-min", "lint", ["--ports", "1", "--channels", "1", "--data-bits", "64"]),
            ("wrapper-max", "lint", ["--ranks", "2", "--data-bits", "256", "--depth", "64"]),
            ("power-down", "controller", ["--power-policy", "1"]),
            ("self-refresh", "controller", ["--power-policy", "2"]),
            ("inline-lifecycle", "controller", ["--inline-lifecycle"]),
            (
                "ratio2-ck4",
                "datapath",
                [
                    "--ports",
                    "1",
                    "--channels",
                    "1",
                    "--ranks",
                    "2",
                    "--data-bits",
                    "64",
                    "--profile-number",
                    "24",
                    "--service-div",
                    "4",
                ],
            ),
            (
                "axi256-ck1",
                "datapath",
                ["--channels", "1", "--data-bits", "256", "--depth", "32", "--service-div", "1"],
            ),
            *[
                (
                    label,
                    "datapath",
                    ["--ports", "1", "--channels", "1", "--integrity-mode", mode, "--profile-number", profile],
                )
                for label, mode, profile in (
                    ("inline-fault", "1", "0"),
                    ("write-link-fault", "2", "8"),
                    ("thermal-rfm", "3", "0"),
                )
            ],
        ]
        for label, family, extra in cases:
            report = evidence / label / "report.json"
            add(
                label,
                "dv/integration/check.py",
                ["--family", family, "--report", str(report), "--build-name", name + "-" + label, *extra],
                report,
                # This family runs four full lifecycle simulations after one
                # build. A single-scenario deadline can kill a healthy matrix.
                3600 if family == "apb_publication" else 1800,
            )
    return steps


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--build-name", required=True)
    parser.add_argument("--suite", choices=("all", "owning", "connected"), default="all")
    parser.add_argument("--step", action="append", help="Run only these named steps; repeat for multiple steps")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    if not args.evidence.is_absolute() or evidence.exists():
        parser.error("evidence must be a new absolute directory")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.build_name):
        parser.error("build name must be a simple unique name")
    steps = plan(evidence, args.build_name, args.suite)
    full_names = [s["name"] for s in steps]
    if args.step:
        unknown = set(args.step) - set(full_names)
        if unknown:
            parser.error("unknown steps for selected suite: " + ", ".join(sorted(unknown)))
        steps = [s for s in steps if s["name"] in args.step]
    selection = {"full_suite_selected": len(steps) == len(full_names), "selected_steps": [s["name"] for s in steps]}
    if args.dry_run:
        print(json.dumps({"status": "not_run", **selection, "steps": steps}, indent=2))
        return
    before = source()
    if args.require_clean and before["dirty"]:
        parser.error("clean committed source required")
    evidence.mkdir(parents=True)
    report = {
        "status": "running",
        "suite": args.suite,
        **selection,
        "source_before": before,
        "python": sys.version,
        "seed": 2095,
        "steps": [{**step, "status": "not_run"} for step in steps],
        "limits": [
            "Representative connected smoke; broad matrix, stress, coverage closure "
            "and actual 100-period throughput/fairness remain pending.",
            "The two-rank datapath sample alternates actual traffic across both ranks; "
            "the full parameter matrix remains pending.",
            "Digital simulation does not establish physical CDC/reset, vendor PHY or silicon qualification.",
        ],
    }
    report_path = evidence / "report.json"
    print(f"Controller regression report: {report_path}", flush=True)
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"])
    env = {**os.environ, "PYTHONPYCACHEPREFIX": str(storage / "lpddr5-controller/pycache")}
    failed = False
    try:
        for record in report["steps"]:
            if failed:
                break
            record["status"] = "running"
            report_path.write_text(json.dumps(report, indent=2) + "\n")
            print("RUN " + record["name"], flush=True)
            start = time.monotonic()
            log = evidence / (record["name"] + ".log")
            with log.open("w") as stream:
                try:
                    code, timed_out = run_group(
                        record["argv"], cwd=REPO, env=env, output=stream, timeout=record["timeout_seconds"]
                    )
                except RunInterrupted as exc:
                    record.update(exit_code=exc.code, seconds=time.monotonic() - start, log=str(log))
                    raise
                record["exit_code"] = 124 if timed_out else code
                if timed_out:
                    record.update(status="timed_out", error="step deadline exceeded")
            record.update(seconds=time.monotonic() - start, log=str(log))
            if record["status"] != "timed_out":
                try:
                    raw = Path(record["report"]).read_bytes()
                    child = json.loads(raw)
                    if not isinstance(child, dict):
                        raise ValueError("expected a JSON object")
                    record["report_sha256"] = hashlib.sha256(raw).hexdigest()
                    if (
                        child.get("status") == "failed"
                        or child.get("result") == "failed"
                        or child.get("passed") is False
                    ):
                        record["status"] = "failed"
                    elif record["exit_code"] == 0 and (
                        child.get("status") == "passed"
                        or child.get("result") == "passed"
                        or child.get("passed") is True
                    ):
                        record["status"] = "passed"
                    else:
                        record.update(
                            status="infrastructure_error",
                            error="child exit code and report do not establish an outcome",
                        )
                except (OSError, ValueError) as exc:
                    record.update(status="infrastructure_error", error=f"child report unavailable: {exc}")
            failed = record["status"] != "passed"
            print(record["name"] + ": " + record["status"], flush=True)
        after = source()
        report["source_after"] = after
        report["source_unchanged"] = before == after
        failed |= before != after
        report["status"] = "failed" if failed else "passed"
    except BaseException as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        # A child can time out before it creates a report, and later steps may never start.
        # Publish the aggregate's complete step census beside any evidence the children retained.
        for record in report["steps"]:
            if record["status"] == "running":
                record.update(status="infrastructure_error", error=report.get("error", "step was interrupted"))
            elif record["status"] == "passed" and not report.get("source_unchanged"):
                record.update(status="infrastructure_error", error="could not verify unchanged source")
            step_dir = evidence / record["name"]
            step_dir.mkdir(exist_ok=True)
            (step_dir / "step.json").write_text(json.dumps(record, indent=2) + "\n")
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
