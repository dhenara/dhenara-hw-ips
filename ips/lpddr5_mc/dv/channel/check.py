"""Run owning channel checks with disposable builds and retained provenance."""

import argparse
import hashlib
import json
import os
import platform
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

sys.pycache_prefix = str(Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) / "lpddr5-channel/pycache")

from make_vectors import generate, validate_fsp  # noqa: E402

IP = Path(__file__).resolve().parents[2]
REPO = IP.parents[1]


def source_manifest(ip: Path = IP) -> list[dict[str, str]]:
    """Hash owned source independently of the checkout's parent directories."""
    paths = set()
    for prefix in ("rtl", "dv/contracts", "dv/channel", "dv/system", "dv/integration", "config", "constraints/channel"):
        for path in (ip / prefix).rglob("*"):
            relative = path.relative_to(ip)
            if (
                path.is_file()
                and not {"evidence", "__pycache__"}.intersection(relative.parts)
                and path.suffix in (".sv", ".svh", ".f", ".py", ".json", ".sdc")
            ):
                paths.add(path)
    return [
        {"path": str(path.relative_to(ip.parents[1])), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(paths)
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--build-name", default="owning")
    parser.add_argument(
        "--family",
        action="append",
        choices=(
            "notifications",
            "ecc",
            "data",
            "wck",
            "config",
            "scheduler",
            "fairness",
            "epochs",
            "inline",
            "update",
            "maintenance",
            "fsp",
            "lifecycle",
            "register_io",
            "granule_adapter",
            "channel",
        ),
    )
    parser.add_argument("--trace-epochs", action="store_true")
    parser.add_argument("--per-bank-refresh", action="store_true")
    parser.add_argument("--channel-case", type=int, choices=range(10), default=0)
    parser.add_argument("--channel-observer", action="store_true")
    parser.add_argument(
        "--prepare-observer", action="store_true", help="focused units and channel TB lint only; never simulate"
    )
    args = parser.parse_args()
    if args.prepare_observer:
        args.channel_observer = True
    if args.channel_observer and (args.family != ["channel"] or args.channel_case != 9):
        parser.error("channel observer requires --family channel --channel-case 9")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.build_name):
        parser.error("build name must be a simple name")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    report = args.report.resolve()
    if report.exists():
        raise SystemExit("Refusing to replace existing evidence")
    report.parent.mkdir(parents=True, exist_ok=True)
    raw = report.with_name(report.stem + "-channel.jsonl")
    if args.channel_observer and raw.exists():
        parser.error("Refusing to replace existing channel trace")
    root = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]).resolve()
    build = root / "lpddr5-channel" / args.build_name
    build.mkdir(parents=True, exist_ok=True)
    temporary = build / "tmp"
    temporary.mkdir(exist_ok=True)
    metadata = generate(build / "vectors")
    executable = root / "lpddr5-contracts/verilator-5.006-3/usr/bin/verilator"
    sources = [
        IP / p
        for p in (
            "rtl/contracts/lpddr5_mc_contract_pkg.sv",
            "rtl/ecc/lpddr5_mc_ecc_pkg.sv",
            "rtl/channel/lpddr5_mc_channel_pkg.sv",
            "rtl/ecc/lpddr5_mc_inline.sv",
            "rtl/channel/lpddr5_mc_data.sv",
            "rtl/channel/lpddr5_mc_wck.sv",
            "rtl/channel/lpddr5_mc_scheduler.sv",
            "rtl/channel/lpddr5_mc_channel_config.sv",
            "rtl/channel/lpddr5_mc_write_epochs.sv",
            "rtl/channel/lpddr5_mc_update.sv",
            "rtl/channel/lpddr5_mc_maintenance.sv",
            "rtl/channel/lpddr5_mc_fsp.sv",
            "rtl/channel/lpddr5_mc_lifecycle.sv",
            "rtl/channel/lpddr5_mc_register_io.sv",
            "rtl/channel/lpddr5_mc_granule_adapter.sv",
            "rtl/channel/lpddr5_mc_backend.sv",
            "rtl/channel/lpddr5_mc_notifications.sv",
            "rtl/channel/lpddr5_mc_channel.sv",
            "dv/channel/lpddr5_mc_channel_memory.sv",
        )
    ]
    tool_environment = {
        **os.environ,
        "VERILATOR_ROOT": str(executable.parents[1] / "share/verilator"),
        "VERILATOR_BIN": "../../bin/verilator_bin",
        "TMPDIR": str(temporary),
        "PYTHONPYCACHEPREFIX": str(root / "lpddr5-channel/pycache"),
    }
    if args.channel_observer:
        sys.path[:0] = [str(IP / "dv"), str(IP / "dv/integration")]
        from observations import binding_digest, bindings
        from observations import check as check_channel

        profiles = bindings()
        profile_sha256 = binding_digest(profiles)
        report.with_name(report.stem + "-profiles.json").write_text(json.dumps(profiles, indent=2) + "\n")
    commands = []
    result = {
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True).strip()),
        "python": sys.version,
        "platform": platform.platform(),
        "verilator": subprocess.check_output([str(executable), "--version"], env=tool_environment, text=True).strip(),
        "compiler": subprocess.check_output(["g++", "--version"], text=True).splitlines()[0],
        "make": subprocess.check_output(["make", "--version"], text=True).splitlines()[0],
        "vectors": metadata,
        "channel_case": args.channel_case,
        "preparation_only": args.prepare_observer,
        "channel_observer": args.channel_observer,
        "per_bank_refresh": args.per_bank_refresh,
        "commands": commands,
        "status": "running",
        "limits": [
            "Component and connected CK-channel checks; combined host/CDC and final qualification remain pending."
        ],
    }

    result["source_manifest_start"] = source_manifest()

    def run(argv: list[str]) -> None:
        started = time.monotonic()
        with (report.parent / (report.stem + ".log")).open("a") as log:
            log.write(json.dumps(argv) + "\n")
            log.flush()
            outcome = subprocess.run(
                argv,
                cwd=REPO,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                env=tool_environment,
            )
        commands.append(
            {
                "argv": argv,
                "exit_code": outcome.returncode,
                "seconds": time.monotonic() - started,
            }
        )
        if outcome.returncode:
            raise RuntimeError(f"Command failed: {argv[0]} ({outcome.returncode})")

    try:
        if args.channel_observer:
            tool_environment["PYTHONPATH"] = os.pathsep.join(
                (str(IP / "dv/channel"), str(IP / "dv"), str(IP / "dv/integration"))
            )
            run(
                [
                    sys.executable,
                    str(IP / "dv/channel/observations_test.py"),
                    "--report",
                    str(report.with_name(report.stem + "-units.json")),
                ]
            )
            result["profile_sha256"] = profile_sha256
            if args.prepare_observer:
                run(
                    [
                        str(executable),
                        "--lint-only",
                        "--timing",
                        "--assert",
                        "-Wall",
                        "--timescale-override",
                        "1ns/1ps",
                        "--top-module",
                        "lpddr5_mc_channel_tb",
                        "--Mdir",
                        str(build / "prepare"),
                        "-I" + str(build / "vectors"),
                        "-I" + str(IP / "dv/channel"),
                        *map(str, sources),
                        str(IP / "dv/channel/lpddr5_mc_channel_tb.sv"),
                    ]
                )
                result["status"] = "passed"
                return
        if not args.channel_observer:
            run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    str(IP / "dv/channel"),
                    "-p",
                    "check_test.py",
                    "-v",
                ]
            )
        run([str(executable), "--version"])
        for top in (
            ()
            if args.channel_observer
            else (
                "lpddr5_mc_data",
                "lpddr5_mc_wck",
                "lpddr5_mc_scheduler",
                "lpddr5_mc_channel_config",
                "lpddr5_mc_write_epochs",
                "lpddr5_mc_inline",
                "lpddr5_mc_update",
                "lpddr5_mc_maintenance",
                "lpddr5_mc_fsp",
                "lpddr5_mc_lifecycle",
                "lpddr5_mc_register_io",
                "lpddr5_mc_granule_adapter",
                "lpddr5_mc_backend",
                "lpddr5_mc_notifications",
                "lpddr5_mc_channel",
            )
        ):
            run(
                [
                    str(executable),
                    "--lint-only",
                    "-Wall",
                    "-I" + str(build / "vectors"),
                    "-I" + str(IP / "dv/channel"),
                    "--top-module",
                    top,
                    *map(str, sources),
                ]
            )
        # Default lint above covers depth 64. Exercise the other admitted
        # storage sizes and channel-one elaboration without claiming the
        # full behavioral parameter matrix from lint alone.
        for depth in () if args.channel_observer else (16, 32):
            run(
                [
                    str(executable),
                    "--lint-only",
                    "-Wall",
                    "--top-module",
                    "lpddr5_mc_channel",
                    f"-GDEPTH={depth}",
                    "-GCHANNEL_INDEX=1",
                    "-I" + str(build / "vectors"),
                    "-I" + str(IP / "dv/channel"),
                    *map(str, sources),
                ]
            )
        for family in args.family or (
            "notifications",
            "ecc",
            "data",
            "wck",
            "config",
            "scheduler",
            "fairness",
            "epochs",
            "inline",
            "update",
            "maintenance",
            "fsp",
            "lifecycle",
            "register_io",
            "granule_adapter",
            "channel",
        ):
            top = f"lpddr5_mc_{family}_tb"
            folder = build / family
            run(
                [
                    str(executable),
                    "--binary",
                    *(["--timescale-override", "1ns/1ps"] if args.channel_observer else []),
                    "--timing",
                    "--assert",
                    "-Wall",
                    "-j",
                    "4",
                    "--output-split",
                    "10000",
                    "--output-split-cfuncs",
                    "100",
                    *(["-GPER_BANK_REFRESH=1'b1"] if family == "channel" and args.per_bank_refresh else []),
                    "--Mdir",
                    str(folder),
                    "-I" + str(build / "vectors"),
                    "-I" + str(IP / "dv/channel"),
                    "--top-module",
                    top,
                    *map(str, sources),
                    str(IP / f"dv/channel/{top}.sv"),
                ]
            )
            simulation = [str(folder / ("V" + top)), "+vectors=" + str(build / "vectors")]
            if family == "channel":
                simulation.append(f"+channel_case={args.channel_case}")
                if args.channel_observer:
                    simulation.extend(
                        ["+channel_observations=" + str(raw), "+channel_profile_sha256=" + profile_sha256]
                    )
                if args.trace_epochs:
                    simulation.append("+trace_epochs")
            if family == "fairness":
                simulation.append("+fairness_trace=" + str(report.with_name("fairness.jsonl")))
            run(simulation)
            if args.channel_observer:
                with raw.open() as stream:
                    result["channel_observations"] = check_channel((json.loads(line) for line in stream), profiles)
                result["raw_trace_sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
            if family == "fairness":
                from fairness_trace import qualify as check_fairness

                result["fairness_observations"] = check_fairness(
                    report.with_name("fairness.jsonl"), build / "fairness-mutation.jsonl"
                )
            if family == "fsp":
                result["fsp_oracle"] = validate_fsp(build / "vectors")
        result["status"] = "passed"
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        raise
    finally:
        result["source_manifest"] = source_manifest()
        result["source_unchanged"] = result["source_manifest"] == result["source_manifest_start"]
        if not result["source_unchanged"]:
            result["status"] = "failed"
            result["error"] = "Source changed while checks ran"
        report.write_text(json.dumps(result, indent=2) + "\n")
        if not result["source_unchanged"]:
            raise RuntimeError(result["error"])


if __name__ == "__main__":
    main()
