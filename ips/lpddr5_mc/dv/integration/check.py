"""Check combined RTL boundaries with retained commands and source manifests."""

import argparse
import gzip
import hashlib
import json
import os
import platform
import re
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
IP = REPO / "ips/lpddr5_mc"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--build-name", default="integration")
    parser.add_argument("--expect-legacy-loss", action="store_true")
    parser.add_argument(
        "--family",
        choices=(
            "notification",
            "decoder",
            "datapath",
            "publication",
            "apb_publication",
            "coordinator",
            "controller",
            "lint",
        ),
        default="notification",
    )
    parser.add_argument("--scenario", type=int, choices=range(4))
    parser.add_argument("--runtime-reset", action="store_true")
    parser.add_argument("--power-policy", type=int, choices=(1, 2))
    parser.add_argument("--integrity-mode", type=int, choices=range(5), default=0)
    parser.add_argument("--cap-mode", type=int, choices=(1, 2))
    parser.add_argument("--cap-granule-bytes", type=int, choices=(32, 64), default=64)
    parser.add_argument("--cap-traffic", choices=("phase", "mixed"), default="phase")
    parser.add_argument("--benchmark-direction", choices=("read", "write"))
    parser.add_argument("--benchmark-smoke", action="store_true")
    parser.add_argument("--id-bits", type=int, choices=range(4, 17))
    parser.add_argument("--address-bits", type=int, choices=range(32, 41), default=40)
    parser.add_argument("--granule-bytes", type=int, choices=(32, 64), default=64)
    for flag, values, default in (
        ("ports", (1, 2), 2),
        ("channels", (1, 2), 2),
        ("ranks", (1, 2), 1),
        ("data-bits", (64, 128, 256), 128),
        ("depth", (16, 32, 64), 16),
        ("profile-number", range(32), 0),
        ("service-div", (1, 2, 4), 2),
    ):
        parser.add_argument("--" + flag, type=int, choices=values, default=default)
    args = parser.parse_args()
    configuration = {
        "PORTS": args.ports,
        "CHANNELS": args.channels,
        "RANKS": args.ranks,
        "DATA_BITS": args.data_bits,
        "DEPTH": args.depth,
        "PROFILE_NUMBER": args.profile_number,
        "SERVICE_DIV": args.service_div,
        "INTEGRITY_MODE": args.integrity_mode,
    }
    if args.family in ("datapath", "lint"):
        configuration.update(
            ID_BITS=args.id_bits if args.id_bits is not None else 16 if args.integrity_mode == 4 else 8,
            ADDRESS_BITS=args.address_bits,
            GRANULE_BYTES=args.granule_bytes,
        )
    elif args.id_bits is not None or args.address_bits != 40 or args.granule_bytes != 64:
        parser.error("ID/address/granule selection applies only to datapath and wrapper lint")
    if args.family == "lint" and args.granule_bytes != 64:
        parser.error("granule size is a runtime configuration; use datapath for its behavior check")
    if args.cap_mode is not None and args.granule_bytes != 64:
        parser.error("cap workloads select their granule with --cap-granule-bytes")
    if args.integrity_mode and (args.id_bits is not None or args.address_bits != 40 or args.granule_bytes != 64):
        parser.error("focused integrity workloads use their fixed ID/address/granule configuration")
    if args.runtime_reset and (args.family != "controller" or args.power_policy or args.benchmark_direction):
        parser.error("runtime reset requires the normal controller fixture")
    if args.cap_mode is not None and (
        args.family != "datapath" or (args.ports, args.channels, args.integrity_mode) != (2, 2, 0)
    ):
        parser.error("cap modes require the plain two-port/two-channel datapath")
    if args.cap_traffic == "mixed" and args.cap_mode != 1:
        parser.error("mixed cap traffic currently requires strict caps; opposite-eligibility replay remains pending")
    if args.cap_mode is None and (args.cap_granule_bytes != 64 or args.cap_traffic != "phase"):
        parser.error("cap workload selections require --cap-mode")
    if args.benchmark_smoke and not args.benchmark_direction:
        parser.error("benchmark smoke requires an explicit benchmark direction")
    if args.benchmark_direction and (
        args.family != "controller"
        or (args.ports, args.channels, args.ranks, args.data_bits, args.depth, args.profile_number, args.integrity_mode)
        != (2, 2, 1, 128, 64, 4, 0)
    ):
        parser.error("benchmark requires controller, two AXI128 ports/channels, one rank, depth 64 and profile 4")
    if (
        args.family not in ("datapath", "lint")
        and not args.benchmark_direction
        and list(configuration.values())
        != [
            2,
            2,
            1,
            128,
            16,
            0,
            2,
            0,
        ]
    ):
        parser.error("parameter selection applies only to datapath and wrapper lint")
    if args.scenario is not None and args.family != "apb_publication":
        parser.error("scenario selection applies only to APB publication")
    if args.power_policy is not None and args.family != "controller":
        parser.error("power policy selection applies only to the controller")
    if args.expect_legacy_loss and args.family != "notification":
        parser.error("legacy counterexample applies only to notifications")
    if args.integrity_mode and (
        args.family != "datapath"
        or (args.ports, args.channels, args.ranks) != (1, 1, 1)
        or args.profile_number != ({2: 8, 4: 16}.get(args.integrity_mode, 0))
    ):
        parser.error(
            "focused modes require datapath, one port/channel/rank and profile 0 (8 for write link, 16 for read link)"
        )
    report = args.report.resolve()
    if report.exists():
        raise SystemExit("Refusing to overwrite evidence")
    if not args.build_name.replace("-", "").replace("_", "").isalnum():
        parser.error("build name must be a simple name")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]).resolve()
    build = storage / "lpddr5-integration" / args.build_name
    build.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    sys.pycache_prefix = str(storage / "lpddr5-integration/pycache")
    from vectors import generate

    generate(build)
    top = "lpddr5_mc" if args.family == "lint" else args.family + "_tb"
    tool = storage / "lpddr5-contracts/verilator-5.006-3/usr"
    env = {
        **os.environ,
        "VERILATOR_ROOT": str(tool / "share/verilator"),
        "VERILATOR_BIN": "../../bin/verilator_bin",
    }
    sources = [REPO / line for line in (IP / "rtl/files.f").read_text().splitlines() if line]
    if args.family in ("datapath", "publication", "apb_publication", "controller"):
        sources += [IP / "dv/channel/lpddr5_mc_channel_memory.sv"]
    if args.family in ("datapath", "controller"):
        sources += [IP / "dv/integration/lpddr5_mc_observer.sv"]
    if args.family != "lint":
        sources += [IP / "dv/integration" / (top + ".sv")]
    dependencies = [
        *sorted((IP / "dv/integration").glob("*.py")),
        *sorted((IP / "dv/integration").glob("*.svh")),
        *sorted((IP / "dv/system").rglob("*.py")),
        *sorted((IP / "dv/contracts").rglob("*.py")),
        *sorted((IP / "config/contracts").glob("*.json")),
        *sorted((IP / "config/reference").glob("*.json")),
    ]
    manifest = {
        str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [*sources, IP / "rtl/files.f", *dependencies]
    }
    result = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "seed": 2095,
        "python": platform.python_version(),
        "family": args.family,
        "configuration": configuration,
        "power_policy": args.power_policy,
        "benchmark_direction": args.benchmark_direction,
        "benchmark_smoke": args.benchmark_smoke,
        "runtime_reset": args.runtime_reset,
        "cap_mode": args.cap_mode,
        "cap_granule_bytes": args.cap_granule_bytes,
        "cap_traffic": args.cap_traffic,
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True)),
        "source_sha256": manifest,
        "commands": [],
        "legacy_counterexample": args.expect_legacy_loss,
        "status": "running",
        "limitations": ["Boundary regression only; complete controller and final qualification remain pending."],
    }
    if args.expect_legacy_loss:
        old = build / "lpddr5_mc_apb.sv"
        old.write_bytes(
            subprocess.check_output(
                [
                    "git",
                    "show",
                    "fd2f6f567366c422968f88245f16bb62ce594914:ips/lpddr5_mc/rtl/control/lpddr5_mc_apb.sv",
                ],
                cwd=REPO,
            )
        )
        sources = [old if p.name == old.name else p for p in sources]
        result["legacy_apb_sha256"] = hashlib.sha256(old.read_bytes()).hexdigest()
    binary = build / (args.family + "-obj") / ("V" + top)
    trace_path = (
        build / "benchmark-observations.jsonl" if args.benchmark_direction else report.with_name("observations.jsonl")
    )
    commands = [
        [str(tool / "bin/verilator"), "--version"],
        [
            str(tool / "bin/verilator"),
            "--lint-only" if args.family == "lint" else "--binary",
            "--timing",
            "--timescale-override",
            "1ns/1ps",
            "--assert",
            *([] if args.benchmark_direction else ["--public-flat-rw"]),
            "-Wall",
            "--output-split",
            "10000",
            "--output-split-cfuncs",
            "500",
            "-MAKEFLAGS",
            "OPT_FAST=-O2 OPT_SLOW=-O1" if args.benchmark_direction else "OPT_FAST=-O0 OPT_SLOW=-O0",
            "--top-module",
            top,
            "--Mdir",
            str(build / (args.family + "-obj")),
            "-I" + str(build),
            "-I" + str(IP / "dv/integration"),
            *(
                [
                    f"-G{key}={value}"
                    for key, value in configuration.items()
                    if key not in ("PROFILE_NUMBER", "SERVICE_DIV", "INTEGRITY_MODE", "GRANULE_BYTES")
                ]
                if args.family == "lint"
                else [f"-G{key}={value}" for key, value in configuration.items()]
                if args.family == "datapath"
                else [f"-G{key}={value}" for key, value in configuration.items() if key != "INTEGRITY_MODE"]
                + ["-GBENCHMARK=1'b1"]
                if args.benchmark_direction
                else []
            ),
            *(["-GRUNTIME_RESET=1'b1"] if args.runtime_reset else []),
            *map(str, sources),
            "-j",
            "2",
        ],
        [
            str(binary),
            *(
                ["+images=" + str(build / "images.txt")]
                if args.family in ("decoder", "publication", "apb_publication", "controller")
                else []
            ),
            *(["+observations=" + str(trace_path)] if args.family in ("datapath", "controller") else []),
        ],
    ]
    if args.family == "lint":
        commands.pop()
    if args.family == "controller":
        commands[-1].append("+recovery_observations=" + str(report.with_name("recovery-observations.jsonl")))
        if args.power_policy is not None:
            commands[-1].append(f"+power_policy={args.power_policy}")
        if args.benchmark_direction:
            commands[-1].extend(
                [
                    f"+benchmark_direction={int(args.benchmark_direction == 'write')}",
                    f"+benchmark_smoke={int(args.benchmark_smoke)}",
                    f"+benchmark_service={args.service_div}",
                ]
            )
    if args.cap_mode is not None:
        commands[-1].extend(
            [
                f"+cap_mode={args.cap_mode}",
                f"+cap_granule_bytes={args.cap_granule_bytes}",
                f"+cap_mixed={int(args.cap_traffic == 'mixed')}",
                "+cap_observations=" + str(report.with_name("cap-observations.jsonl")),
            ]
        )
    if args.family == "apb_publication":
        simulation = commands.pop()
        scenarios = range(4) if args.scenario is None else [args.scenario]
        commands.extend([*simulation, f"+scenario={scenario}"] for scenario in scenarios)
    report.write_text(json.dumps(result, indent=2) + "\n")
    try:
        for index, command in enumerate(commands):
            start = time.monotonic()
            log = report.with_name(f"{report.stem}-{index}.log")
            with log.open("w") as stream:
                run = subprocess.run(
                    command,
                    cwd=REPO,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            result["commands"].append(
                {
                    "argv": command,
                    "exit_code": run.returncode,
                    "seconds": time.monotonic() - start,
                    "log": str(log),
                }
            )
            report.write_text(json.dumps(result, indent=2) + "\n")
            output = log.read_text()
            if index == 2 and args.expect_legacy_loss:
                if run.returncode == 0 or "retained CE not delivered once" not in output:
                    raise RuntimeError("Legacy source did not reproduce the retained-notification loss")
                result["expected_loss_reproduced"] = True
            elif run.returncode:
                raise RuntimeError(f"Command failed; see {log}")
            elif index >= 2 and (args.family.upper() + "_INTEGRATION_PASS") not in output:
                raise RuntimeError("Missing behavior completion marker")
        if args.family in ("datapath", "controller"):
            from connected import check

            profiles = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"]
            if args.benchmark_direction:
                from benchmark import check as check_benchmark

                result["benchmark_observations"] = check_benchmark(
                    trace_path,
                    report.with_name(f"{report.stem}-2.log"),
                    args.benchmark_direction,
                    args.service_div,
                    smoke=args.benchmark_smoke,
                )
                if not args.benchmark_smoke and not result["benchmark_observations"]["threshold_passed"]:
                    raise RuntimeError("Actual controller payload is below the agreed 99% threshold")
            elif args.integrity_mode in (1, 2, 4):
                from integrity import check as check_integrity

                result["integrity_observations"] = check_integrity(
                    report.with_name("observations.jsonl"),
                    report.with_name(f"{report.stem}-2.log"),
                    profiles[args.profile_number],
                    args.integrity_mode,
                )
            else:
                result["external_observations"] = check(
                    report.with_name("observations.jsonl"),
                    profiles[args.profile_number],
                    ranks=args.ranks,
                    depth=args.depth,
                    mapped_end=(1 << 30) * args.channels * args.ranks if args.ranks == 2 else 1 << 20,
                    id_bits=configuration.get("ID_BITS", 8),
                    address_bits=args.address_bits,
                    mr4_change_ck=(
                        int(
                            re.search(
                                r"THERMAL_CHANGE ck=(\d+)",
                                report.with_name(f"{report.stem}-2.log").read_text(),
                            )[1]
                        )
                        if args.integrity_mode == 3
                        else None
                    ),
                )
            if args.family == "controller" and args.power_policy is None and not args.benchmark_direction:
                result["recovered_external_observations"] = check(
                    report.with_name("recovery-observations.jsonl"),
                    profiles[0],
                    ranks=1,
                    depth=16,
                )
            if args.cap_mode is not None:
                from caps import qualify as qualify_caps

                result["cap_observations"] = qualify_caps(
                    report.with_name("cap-observations.jsonl"),
                    build / "cap-mutant.jsonl",
                    result["external_observations"],
                )
        after = {
            str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [REPO / p for p in manifest]
        }
        if after != manifest:
            raise RuntimeError("Source changed during checks")
        result["status"] = "passed"
    except BaseException as error:
        result["status"] = "failed"
        result["error"] = str(error)
        raise
    finally:
        if args.benchmark_direction and trace_path.exists():
            with (
                trace_path.open("rb") as source,
                gzip.open(report.with_name("observations.jsonl.gz"), "wb") as destination,
            ):
                shutil.copyfileobj(source, destination)
        report.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Integration checks passed: {report}")


if __name__ == "__main__":
    main()
