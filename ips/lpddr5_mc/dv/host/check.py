"""Run the owning host/control checks with retained, bounded phase results."""

import argparse
import hashlib
import json
import os
import platform
import re
import resource
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
IP = REPO / "ips/lpddr5_mc"


@dataclass(frozen=True)
class Phase:
    name: str
    argv: list[str]
    timeout_seconds: float
    report: Path | None = None


def phases(report: Path, build_name: str, storage: Path) -> list[Phase]:
    evidence = report.parent / (report.stem + "-phases")
    result = [
        Phase(
            "runner",
            [sys.executable, "-m", "unittest", "discover", "-s", "ips/lpddr5_mc/dv/host", "-p", "check_test.py", "-v"],
            30,
        ),
        Phase(
            "python-lint",
            [
                str(storage / "lpddr5-contracts/python-tools/bin/ruff"),
                "check",
                "--config",
                str(IP / "tools/ruff.toml"),
                str(IP / "dv/host"),
                str(IP / "rtl/control/generate_apb_metadata.py"),
            ],
            30,
        ),
        Phase(
            "python-format",
            [
                str(storage / "lpddr5-contracts/python-tools/bin/ruff"),
                "format",
                "--check",
                "--config",
                str(IP / "tools/ruff.toml"),
                str(IP / "dv/host"),
                str(IP / "rtl/control/generate_apb_metadata.py"),
            ],
            30,
        ),
        Phase(
            "foundation",
            [
                "make",
                "-f",
                "ips/lpddr5_mc/Makefile",
                "check",
                "REPORT=" + str(evidence / "foundation.json"),
                "BUILD_SUBDIR=" + build_name + "-foundation",
            ],
            300,
            evidence / "foundation.json",
        ),
    ]
    parameter_report = evidence / "parameters.json"
    result.append(
        Phase(
            "parameters",
            [
                sys.executable,
                str(IP / "dv/host/parameter_run.py"),
                "--report",
                str(parameter_report),
                "--build-name",
                build_name + "-parameters",
            ],
            600,
            parameter_report,
        )
    )
    for name, runner in (("cdc", "cdc_check.py"), ("apb", "apb_run.py"), ("adapters", "adapter_run.py")):
        child_report = evidence / (name + ".json")
        result.append(
            Phase(
                name,
                [
                    sys.executable,
                    str(IP / "dv/host" / runner),
                    "--report",
                    str(child_report),
                    "--build-subdir",
                    build_name + "-" + name,
                ],
                900,
                child_report,
            )
        )
    for bits, depth, early in ((64, 16, False), (128, 16, False), (256, 64, False), (128, 16, True)):
        name = f"core-{bits}-{depth}-" + ("early" if early else "strict")
        child_report = evidence / (name + ".json")
        result.append(
            Phase(
                name,
                [
                    sys.executable,
                    str(IP / "dv/host/core_run.py"),
                    "--report",
                    str(child_report),
                    "--build-name",
                    build_name + "-" + name,
                    "--data-bits",
                    str(bits),
                    "--queue-depth",
                    str(depth),
                    *(["--early-posted"] if early else []),
                ],
                900,
                child_report,
            )
        )
    result.append(
        Phase(
            "axi",
            [
                sys.executable,
                str(IP / "dv/host/axi_run.py"),
                "--report",
                str(evidence / "axi.json"),
                "--build-subdir",
                build_name + "-axi",
            ],
            600,
            evidence / "axi.json",
        )
    )
    result.append(
        Phase(
            "assembly",
            [
                sys.executable,
                str(IP / "dv/host/assembly_run.py"),
                "--report",
                str(evidence / "assembly.json"),
                "--build-subdir",
                build_name + "-assembly",
            ],
            900,
            evidence / "assembly.json",
        )
    )
    result.append(
        Phase(
            "lease",
            [
                sys.executable,
                str(IP / "dv/host/lease_run.py"),
                "--report",
                str(evidence / "lease.json"),
                "--build-subdir",
                build_name + "-lease",
            ],
            900,
            evidence / "lease.json",
        )
    )
    for name, parameters in (
        (
            "minimum",
            {"PORTS": 1, "CHANNELS": 1, "RANKS": 1, "DATA_BITS": 64, "ID_BITS": 4, "ADDRESS_BITS": 32, "DEPTH": 16},
        ),
        ("default", {}),
        (
            "maximum",
            {"PORTS": 2, "CHANNELS": 2, "RANKS": 2, "DATA_BITS": 256, "ID_BITS": 16, "ADDRESS_BITS": 40, "DEPTH": 64},
        ),
    ):
        result.append(
            Phase(
                "host-lint-" + name,
                [
                    str(storage / "lpddr5-contracts/verilator-5.006-3/usr/bin/verilator"),
                    "--lint-only",
                    "--timing",
                    "--timescale-override",
                    "1ns/1ps",
                    "--assert",
                    "-Wall",
                    "--top-module",
                    "lpddr5_mc_host",
                    "--Mdir",
                    str(storage / "lpddr5-host" / (build_name + "-lint-" + name)),
                    *(f"-G{key}={value}" for key, value in parameters.items()),
                    "-f",
                    "ips/lpddr5_mc/rtl/host/files.f",
                ],
                120,
            )
        )
    return result


def snapshot() -> dict:
    names = (
        subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=REPO)
        .decode()
        .split("\0")
    )
    sources = {}
    for name in sorted(set(names) - {""}):
        path = REPO / name
        if path.is_symlink():
            sources[name] = {"symlink": os.readlink(path)}
        elif path.is_file():
            sources[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        else:
            sources[name] = {"missing": True}
    status = subprocess.check_output(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=REPO, text=True)
    return {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "source_dirty": bool(status),
        "git_status": status,
        "source_files": sources,
    }


def run_phase(phase: Phase, log: Path, env: dict[str, str]) -> dict:
    started = time.monotonic()
    record = {
        "name": phase.name,
        "argv": phase.argv,
        "timeout_seconds": phase.timeout_seconds,
        "log": str(log),
        "report": str(phase.report) if phase.report else None,
        "exit_code": None,
        "timed_out": False,
    }
    with log.open("w") as output:
        try:
            process = subprocess.Popen(
                phase.argv, cwd=REPO, env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True
            )
            try:
                record["exit_code"] = process.wait(timeout=phase.timeout_seconds)
            except subprocess.TimeoutExpired:
                record["timed_out"] = True
                # Kill the phase's entire compiler/simulator process group;
                # terminating only its Python wrapper leaves children alive.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                record["exit_code"] = process.wait()
        except OSError as error:
            record["launch_error"] = str(error)
    record["seconds"] = time.monotonic() - started
    if record["exit_code"] == 0 and phase.report:
        try:
            contents = phase.report.read_bytes()
            child = json.loads(contents)
            commands = child.get("commands") if isinstance(child, dict) else None
            if (
                not isinstance(child, dict)
                or child.get("result") not in (None, "passed")
                or not isinstance(commands, list)
                or not commands
                or any(not isinstance(command, dict) or command.get("exit_code") != 0 for command in commands)
            ):
                record["evidence_error"] = "Child report does not describe a passing result"
            record["report_sha256"] = hashlib.sha256(contents).hexdigest()
        except (OSError, ValueError) as error:
            record["evidence_error"] = str(error)
    record["status"] = (
        "passed"
        if record["exit_code"] == 0 and not record["timed_out"] and "evidence_error" not in record
        else "failed"
    )
    return record


def exit_status(record: dict) -> int:
    if record["timed_out"]:
        return 124
    code = record["exit_code"]
    return (128 - code if code < 0 else code or 1) if code is not None else 2


def main() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--build-subdir", default="host-check")
    parser.add_argument(
        "--dry-run", action="store_true", help="print commands without executing checks or writing reports"
    )
    args = parser.parse_args()
    report = args.report.resolve()
    if not report.is_relative_to(IP / "dv/host/out") or report.suffix != ".json":
        parser.error("report must be a JSON path below ips/lpddr5_mc/dv/host/out")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.build_subdir):
        parser.error("build subdirectory must be a simple name")
    if report.exists() or (report.parent / (report.stem + "-phases")).exists():
        parser.error("report or phase directory already exists; preserve it and choose a fresh report path")
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]).resolve()
    plan = phases(report, args.build_subdir, storage)
    if args.dry_run:
        print(json.dumps({"result": "not_run", "phases": [phase.__dict__ for phase in plan]}, default=str, indent=2))
        return
    evidence = report.parent / (report.stem + "-phases")
    evidence.mkdir(parents=True)
    before = snapshot()
    tool = storage / "lpddr5-contracts/verilator-5.006-3"
    env = dict(
        os.environ,
        VERILATOR_ROOT=str(tool / "usr/share/verilator"),
        VERILATOR_BIN="../../bin/verilator_bin",
        RUFF_CACHE_DIR=str(storage / "lpddr5-contracts/ruff-cache"),
        PYTHONPYCACHEPREFIX=str(storage / "lpddr5-contracts/pycache"),
    )
    records = []
    code = 0
    for phase in plan:
        if code:
            records.append({"name": phase.name, "status": "not_run", "reason": "earlier phase failed"})
            continue
        print("RUN " + phase.name, flush=True)
        record = run_phase(phase, evidence / (phase.name + ".log"), env)
        records.append(record)
        print(phase.name + ": " + record["status"], flush=True)
        if record["status"] != "passed":
            code = exit_status(record)
    after = snapshot()
    changed = sorted(
        name
        for name in before["source_files"].keys() | after["source_files"].keys()
        if before["source_files"].get(name) != after["source_files"].get(name)
    )
    source_changed = bool(changed) or before["commit"] != after["commit"]
    if source_changed:
        code = code or 1
    report.write_text(
        json.dumps(
            {
                "result": "passed" if code == 0 else "failed",
                "exit_code": code,
                "python": platform.python_version(),
                "platform": platform.platform(),
                "seed": 2095,
                "source_commit": before["commit"],
                "source_dirty": before["source_dirty"],
                "source_before": before,
                "source_after": after,
                "source_changed_during_run": source_changed,
                "changed_paths": changed,
                "phases": records,
                "limitations": [
                    "Owning host/control evidence; combined channel, DFI, ECC, lifecycle "
                    "and throughput qualification are downstream.",
                    "Representative parameter variants and boundary elaboration "
                    "are not the full Cartesian parameter matrix.",
                    "Digital CDC simulation does not establish physical CDC/reset or metastability sign-off.",
                ],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Host/control checks {'passed' if code == 0 else 'failed'}: {report}", flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
