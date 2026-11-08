"""Check combined RTL boundaries with retained commands and source manifests."""

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
    parser.add_argument("--power-policy", type=int, choices=(1, 2))
    parser.add_argument("--integrity-mode", type=int, choices=range(5), default=0)
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
    if args.family not in ("datapath", "lint") and list(configuration.values()) != [
        2,
        2,
        1,
        128,
        16,
        0,
        2,
        0,
    ]:
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
        *sorted((IP / "dv/system").rglob("*.py")),
        *sorted((IP / "dv/contracts").rglob("*.py")),
        *sorted((IP / "config/contracts").glob("*.json")),
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
    commands = [
        [str(tool / "bin/verilator"), "--version"],
        [
            str(tool / "bin/verilator"),
            "--lint-only" if args.family == "lint" else "--binary",
            "--timing",
            "--timescale-override",
            "1ns/1ps",
            "--assert",
            "--public-flat-rw",
            "-Wall",
            "--output-split",
            "10000",
            "--output-split-cfuncs",
            "500",
            "-MAKEFLAGS",
            "OPT_FAST=-O0 OPT_SLOW=-O0",
            "--top-module",
            top,
            "--Mdir",
            str(build / (args.family + "-obj")),
            "-I" + str(build),
            *(
                [
                    f"-G{key}={value}"
                    for key, value in configuration.items()
                    if key not in ("PROFILE_NUMBER", "SERVICE_DIV", "INTEGRITY_MODE")
                ]
                if args.family == "lint"
                else [f"-G{key}={value}" for key, value in configuration.items()]
                if args.family == "datapath"
                else []
            ),
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
            *(
                ["+observations=" + str(report.with_name("observations.jsonl"))]
                if args.family in ("datapath", "controller")
                else []
            ),
        ],
    ]
    if args.family == "lint":
        commands.pop()
    if args.family == "controller":
        commands[-1].append("+recovery_observations=" + str(report.with_name("recovery-observations.jsonl")))
        if args.power_policy is not None:
            commands[-1].append(f"+power_policy={args.power_policy}")
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
            if args.integrity_mode in (1, 2, 4):
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
            if args.family == "controller" and args.power_policy is None:
                result["recovered_external_observations"] = check(
                    report.with_name("recovery-observations.jsonl"),
                    profiles[0],
                    ranks=1,
                    depth=16,
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
        report.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Integration checks passed: {report}")


if __name__ == "__main__":
    main()
