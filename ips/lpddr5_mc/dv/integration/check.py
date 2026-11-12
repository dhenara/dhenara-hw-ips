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


def reserve_observations(report: Path, family: str, benchmark: bool, caps: bool, fairness: bool = False) -> list[Path]:
    """Reserve shared trace names before compilation or another writer can start.

    Different report filenames do not isolate observations in one directory.
    Exclusive creation also fences concurrent runs; stale files require a new
    evidence directory, never deletion or an overwrite of the earlier run.
    """
    paths = []
    if family in ("datapath", "controller"):
        paths.append(report.with_name("observations.jsonl.gz" if benchmark else "observations.jsonl"))
    if family == "controller":
        paths.append(report.with_name("recovery-observations.jsonl"))
    if caps:
        paths.append(report.with_name("cap-observations.jsonl"))
    if fairness:
        paths.append(report.with_name("fairness-observations.jsonl"))
    for path in paths:
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite retained observations: {path}; use a fresh report directory")
    for path in paths:
        with path.open("x"):
            pass
    return paths


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
    parser.add_argument("--runtime-reset-matrix", action="store_true")
    parser.add_argument("--runtime-reset-case", type=int, choices=range(10))
    parser.add_argument("--power-policy", type=int, choices=(1, 2))
    parser.add_argument("--integrity-mode", type=int, choices=range(5), default=0)
    parser.add_argument("--cap-mode", type=int, choices=(1, 2))
    parser.add_argument("--cap-granule-bytes", type=int, choices=(32, 64), default=64)
    parser.add_argument("--cap-traffic", choices=("phase", "mixed", "concurrent"), default="phase")
    parser.add_argument("--zero-cap-direction", choices=("read", "write"))
    parser.add_argument("--fairness-mode", type=int, choices=(0, 1, 2))
    parser.add_argument("--fairness-traffic", choices=("balanced", "row-churn"), default="balanced")
    parser.add_argument("--benchmark-direction", choices=("read", "write"))
    parser.add_argument("--benchmark-smoke", action="store_true")
    parser.add_argument("--axi-stress-seed", type=int)
    parser.add_argument("--axi-overlap", action="store_true")
    parser.add_argument("--inline-lifecycle", action="store_true")
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
    if args.inline_lifecycle and (
        args.family != "controller"
        or args.runtime_reset
        or args.runtime_reset_matrix
        or args.runtime_reset_case is not None
        or args.power_policy
        or args.benchmark_direction
        or args.integrity_mode
    ):
        parser.error("inline lifecycle requires the default controller fixture")
    if args.axi_overlap and (
        args.family != "datapath"
        or args.ports != 2
        or args.integrity_mode
        or args.cap_mode is not None
        or args.fairness_mode is not None
        or args.benchmark_direction
        or args.axi_stress_seed is not None
    ):
        parser.error("AXI overlap requires the plain two-port datapath fixture")
    if args.axi_stress_seed is not None and (
        not 0 < args.axi_stress_seed < (1 << 31)
        or args.family != "datapath"
        or args.integrity_mode
        or args.cap_mode is not None
        or args.fairness_mode is not None
        or args.benchmark_direction
    ):
        parser.error("AXI stress requires a positive 31-bit seed and the plain datapath fixture")
    if args.runtime_reset_case is not None:
        args.runtime_reset_matrix = True
    if args.runtime_reset_matrix:
        args.runtime_reset = True
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
    if args.axi_stress_seed is not None:
        configuration["AXI_STRESS"] = 1
    if args.axi_overlap:
        configuration["AXI_OVERLAP"] = 1
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
        parser.error("mixed cap traffic requires strict caps; use concurrent for timing-checked borrowing")
    if args.cap_traffic == "concurrent" and (args.cap_mode is None or args.ranks != 1):
        parser.error("concurrent cap traffic requires one rank and --cap-mode")
    if args.cap_mode is None and (args.cap_granule_bytes != 64 or args.cap_traffic != "phase"):
        parser.error("cap workload selections require --cap-mode")
    if args.zero_cap_direction is not None and (
        args.cap_mode is None or args.cap_traffic != "phase" or args.cap_granule_bytes != 64
    ):
        parser.error("zero-cap checks require --cap-mode and the default phase/64-byte selections")
    if args.fairness_mode is not None and (
        args.family != "datapath"
        or args.cap_mode is not None
        or (args.ports, args.channels, args.ranks, args.data_bits, args.depth, args.profile_number, args.integrity_mode)
        != (2, 2, 1, 128, 64, 4, 0)
        or args.granule_bytes != 64
    ):
        parser.error("fairness requires two plain AXI128 ports/channels, one rank, depth64/profile4/64-byte granules")
    if args.fairness_traffic != "balanced" and args.fairness_mode is None:
        parser.error("fairness traffic selection requires --fairness-mode")
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
    reserved = reserve_observations(
        report, args.family, bool(args.benchmark_direction), args.cap_mode is not None, args.fairness_mode is not None
    )
    sys.pycache_prefix = str(storage / "lpddr5-integration/pycache")
    from vectors import generate

    generate(build)
    top = "lpddr5_mc" if args.family == "lint" else args.family + "_tb"
    tool = storage / "lpddr5-contracts/verilator-5.006-3/usr"
    env = {
        **os.environ,
        "VERILATOR_ROOT": str(tool / "share/verilator"),
        "VERILATOR_BIN": "../../bin/verilator_bin",
        "PYTHONPATH": os.pathsep.join(filter(None, (str(IP / "dv"), os.environ.get("PYTHONPATH")))),
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
        "seed": args.axi_stress_seed if args.axi_stress_seed is not None else 2095,
        "python": platform.python_version(),
        "family": args.family,
        "configuration": configuration,
        "power_policy": args.power_policy,
        "benchmark_direction": args.benchmark_direction,
        "benchmark_smoke": args.benchmark_smoke,
        "runtime_reset": args.runtime_reset,
        "runtime_reset_matrix": args.runtime_reset_matrix,
        "runtime_reset_case": args.runtime_reset_case,
        "inline_lifecycle": args.inline_lifecycle,
        "cap_mode": args.cap_mode,
        "cap_granule_bytes": args.cap_granule_bytes,
        "cap_traffic": args.cap_traffic,
        "zero_cap_direction": args.zero_cap_direction,
        "fairness_mode": args.fairness_mode,
        "fairness_traffic": args.fairness_traffic,
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True)),
        "source_sha256": manifest,
        "commands": [],
        "legacy_counterexample": args.expect_legacy_loss,
        "status": "running",
        "reserved_observations": [str(path) for path in reserved],
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
            "OPT_FAST=-O2 OPT_SLOW=-O1"
            if args.benchmark_direction
            or args.runtime_reset
            or args.axi_stress_seed is not None
            or args.axi_overlap
            or args.inline_lifecycle
            else "OPT_FAST=-O0 OPT_SLOW=-O0",
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
            *(["-GRESET_MATRIX=1'b1"] if args.runtime_reset_matrix else []),
            *(["-GINLINE_LIFECYCLE=1'b1"] if args.inline_lifecycle else []),
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
    if args.axi_stress_seed is not None:
        commands[-1].append(f"+axi_stress_seed={args.axi_stress_seed}")
    if args.family == "controller":
        commands[-1].append("+recovery_observations=" + str(report.with_name("recovery-observations.jsonl")))
        if args.power_policy is not None:
            commands[-1].append(f"+power_policy={args.power_policy}")
        if args.runtime_reset_case is not None:
            commands[-1].append(f"+runtime_reset_case={args.runtime_reset_case}")
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
                f"+cap_mixed={('phase', 'mixed', 'concurrent').index(args.cap_traffic)}",
                "+cap_observations=" + str(report.with_name("cap-observations.jsonl")),
            ]
        )
        if args.zero_cap_direction is not None:
            commands[-1].append(f"+zero_cap_direction={int(args.zero_cap_direction == 'write')}")
    if args.fairness_mode is not None:
        commands[-1].extend(
            [
                f"+fairness_mode={args.fairness_mode}",
                f"+fairness_rows={int(args.fairness_traffic == 'row-churn')}",
                "+fairness_observations=" + str(report.with_name("fairness-observations.jsonl")),
            ]
        )
    if args.family == "apb_publication":
        simulation = commands.pop()
        scenarios = range(4) if args.scenario is None else [args.scenario]
        commands.extend([*simulation, f"+scenario={scenario}"] for scenario in scenarios)
    report.write_text(json.dumps(result, indent=2) + "\n")
    try:
        command = [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(IP / "dv/integration"),
            "-p",
            "*_test.py",
            "-v",
        ]
        log = report.with_name(report.stem + "-tooling.log")
        start = time.monotonic()
        with log.open("w") as stream:
            run = subprocess.run(command, cwd=REPO, env=env, stdout=stream, stderr=subprocess.STDOUT, check=False)
        result["tooling_check"] = {
            "argv": command,
            "exit_code": run.returncode,
            "seconds": time.monotonic() - start,
            "log": str(log),
        }
        if run.returncode:
            raise RuntimeError(f"Integration tooling regression failed; see {log}")
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
                from benchmark import require_full_performance

                result["benchmark_observations"] = check_benchmark(
                    trace_path,
                    report.with_name(f"{report.stem}-2.log"),
                    args.benchmark_direction,
                    args.service_div,
                    smoke=args.benchmark_smoke,
                )
                if not args.benchmark_smoke:
                    require_full_performance(result["benchmark_observations"])
            elif args.inline_lifecycle:
                from inline_lifecycle import check as check_inline_lifecycle

                result["inline_lifecycle_observations"] = check_inline_lifecycle(
                    trace_path,
                    report.with_name("recovery-observations.jsonl"),
                    report.with_name(f"{report.stem}-2.log"),
                    profiles[0],
                )
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
                    observe_axi=args.axi_overlap,
                    fault_ps=(
                        int(
                            re.search(r"ZERO_CAP_FAULT ps=(\d+)", report.with_name(f"{report.stem}-2.log").read_text())[
                                1
                            ]
                        )
                        if args.zero_cap_direction is not None
                        else None
                    ),
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
            if (
                args.family == "controller"
                and args.power_policy is None
                and not args.benchmark_direction
                and not args.inline_lifecycle
            ):
                result["recovered_external_observations"] = check(
                    report.with_name("recovery-observations.jsonl"),
                    profiles[0],
                    ranks=1,
                    depth=16,
                )
            if args.cap_mode is not None:
                if args.zero_cap_direction is not None:
                    from zero_caps import qualify as qualify_caps
                else:
                    from caps import qualify as qualify_caps

                eligibility_inputs = (
                    {
                        "raw": report.with_name("observations.jsonl"),
                        "profile": profiles[args.profile_number] | {"ranks": 1},
                    }
                    if args.cap_traffic == "concurrent"
                    else {}
                )
                result["cap_observations"] = qualify_caps(
                    report.with_name("cap-observations.jsonl"),
                    build / "cap-mutant.jsonl",
                    result["external_observations"],
                    **eligibility_inputs,
                )
            if args.fairness_mode is not None:
                from fairness import qualify as qualify_fairness

                result["fairness_observations"] = qualify_fairness(
                    report.with_name("fairness-observations.jsonl"),
                    build / "fairness-mutant.jsonl",
                    result["external_observations"],
                )
            if not args.benchmark_direction:
                from workload_costs import measure

                if args.axi_stress_seed is not None:
                    from axi_stress import measure as measure_stress

                    result["axi_stress_observations"] = measure_stress(
                        report.with_name("observations.jsonl"),
                        report.with_name(f"{report.stem}-2.log"),
                        args.axi_stress_seed,
                    )
                if args.axi_overlap:
                    from axi_overlap import measure as measure_overlap

                    result["axi_overlap_observations"] = measure_overlap(
                        report.with_name("observations.jsonl"), report.with_name(f"{report.stem}-2.log"), args.depth
                    )
                result["workload_costs"] = measure(report.with_name("observations.jsonl"))
                if args.integrity_mode == 3:
                    text = report.with_name(f"{report.stem}-2.log").read_text()
                    start = int(re.search(r"HOT_WORKLOAD_START ps=(\d+)", text)[1])
                    end = int(re.search(r"HOT_WORKLOAD_END ps=(\d+)", text)[1])
                    temperatures = result["external_observations"]["temperature_returns_by_channel"][0]
                    before = [t for t in temperatures if t["completed_ps"] <= start]
                    if (
                        not before
                        or before[-1]["mr4"] != 13
                        or any(t["mr4"] != 13 for t in temperatures if start < t["completed_ps"] <= end)
                    ):
                        raise RuntimeError("Hot segment lacks independently completed temperature13")
                    result["hot_workload_costs"] = measure(
                        report.with_name("observations.jsonl"), start_ps=start, end_ps=end
                    )
                    for direction in ("read", "write"):
                        stats = result["hot_workload_costs"][direction]
                        if stats["completed_transactions"] != 32 or stats["successful_payload_bytes"] != 2048:
                            raise RuntimeError("Hot segment incomplete payload")
                if "recovered_external_observations" in result:
                    result["recovered_workload_costs"] = measure(report.with_name("recovery-observations.jsonl"))
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
