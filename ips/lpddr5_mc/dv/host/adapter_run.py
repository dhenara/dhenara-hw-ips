"""Verify committed-image and host/channel adapters against independent inputs."""

import argparse
import copy
import hashlib
import json
import os
import platform
import random
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
IP = ROOT / "ips/lpddr5_mc"
sys.path.insert(0, str(IP / "dv"))
from contracts.configuration import decode_address, validate_configuration  # noqa: E402

SEED = 2095


def cases() -> list[dict]:
    original = json.loads((IP / "config/contracts/example.json").read_text())
    result = []

    def add(name: str, channels: int, ranks: int, inline: bool, regions: list[dict]) -> None:
        config = copy.deepcopy(original)
        config.update(channels=channels, ranks=ranks, inline=inline, regions=regions)
        validate_configuration(config)
        result.append({"name": name, "config": config})

    def region(base: int, size: int, swap: tuple[int, int] | None = None) -> dict:
        bits = list(range(size.bit_length() - 1))
        if swap:
            a, b = swap
            bits[a], bits[b] = bits[b], bits[a]
        return {"base": base, "size": size, "map_bits": bits}

    add("default_two_channels", 2, 1, False, original["regions"])
    add(
        "unsorted_regions_and_permutation",
        2,
        1,
        False,
        [
            region(1 << 32, 1 << 20, (6, 9)),
            region(1 << 30, 1 << 20),
        ],
    )
    add("inline_capacity_rank_boundary", 2, 2, True, [region(0, 1 << 31)])
    add("single_channel_two_ranks", 1, 2, False, [region(1 << 32, 1 << 31)])
    add("high_forty_bit_region", 1, 1, False, [region(0xFFFFFFF000, 4096, (6, 11))])
    shuffled_bases = [0x70000000, 0x10000000, 0x60000000, 0x20000000, 0x80000000, 0x30000000, 0x50000000, 0x40000000]
    add(
        "eight_unordered_regions",
        2,
        2,
        False,
        [region(base, 4096, (6, 6 + index % 6)) for index, base in enumerate(shuffled_bases)],
    )
    return result


def vectors(build: Path) -> list[dict]:
    rng = random.Random(SEED)
    configurations = cases()
    lines = [str(len(configurations))]
    descriptions = []
    registers = json.loads((IP / "config/contracts/registers.json").read_text())["registers"]
    indices = {register["name"]: index for index, register in enumerate(registers)}
    for case in configurations:
        config = case["config"]
        image = [0] * len(registers)
        mode = 0x200 | int(config["burst_bytes"] == 64) | (int(config["inline"]) << 1)
        image[indices["MODE"]] = mode
        for index, region in enumerate(config["regions"]):
            base = region["base"]
            image[indices[f"REGION_{index}_BASE_LO"]] = base & 0xFFFFFFFF
            image[indices[f"REGION_{index}_BASE_HI"]] = base >> 32
            image[indices[f"REGION_{index}_SIZE_4K"]] = region["size"] // 4096
            image[indices[f"REGION_{index}_ATTR"]] = 1
            for bit, source in enumerate(region["map_bits"]):
                image[indices[f"REGION_{index}_BIT_{bit}"]] = source
        addresses = {0, (1 << 40) - 1}
        for region in config["regions"]:
            base, size = region["base"], region["size"]
            addresses.update(base + offset for offset in (0, 1, 31, 32, 63, 64, 127, 128, 2047, 2048, size - 1))
            addresses.update((max(0, base - 1), min((1 << 40) - 1, base + size)))
            addresses.update(base + rng.randrange(size) for _ in range(32))
        if config["inline"]:
            boundary = ((1 << 30) // 576) * 512 * config["channels"]
            addresses.update(boundary + offset for offset in (-65, -64, -1, 0, 1, 63, 64, 65))
        if case["name"] == "single_channel_two_ranks":
            addresses.update((1 << 32) + (1 << 30) + offset for offset in (-1, 0, 1, 63, 64))
        lines.append(f"{config['channels']} {config['ranks']} {len(addresses)}")
        lines.extend(f"{word:08x}" for word in image)
        counts = {"valid": 0, "invalid": 0}
        for address in sorted(addresses):
            try:
                expected = decode_address(config, address)
            except ValueError:
                expected = None
            if expected is None:
                lines.append(f"{address:010x} 0 0 0 0 0 0 0")
                counts["invalid"] += 1
            else:
                physical = (
                    (expected["row"] << 15)
                    | (expected["bank"] << 11)
                    | (expected["column_32b"] << 5)
                    | expected["byte"]
                )
                lines.append(
                    f"{address:010x} 1 {expected['channel']} {expected['rank']} {expected['bank']:x} "
                    f"{expected['row']:x} {expected['column_32b']:x} {physical:010x}"
                )
                counts["valid"] += 1
        descriptions.append({"name": case["name"], "configuration": config, "vectors": counts})
    (build / "adapter_vectors.txt").write_text("\n".join(lines) + "\n")
    return descriptions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--build-subdir", default="host-adapters")
    args = parser.parse_args()
    if args.report.exists():
        raise SystemExit("Report already exists")
    if Path(args.build_subdir).name != args.build_subdir or args.build_subdir in (".", ".."):
        raise SystemExit("Build subdirectory must be one plain directory name")
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"])
    build = storage / "lpddr5-host" / args.build_subdir
    build.mkdir(parents=True, exist_ok=True)
    descriptions = vectors(build)
    tool = storage / "lpddr5-contracts/verilator-5.006-3"
    verilator = tool / "usr/bin/verilator"
    env = {
        **os.environ,
        "VERILATOR_ROOT": str(tool / "usr/share/verilator"),
        "VERILATOR_BIN": "../../bin/verilator_bin",
        "PYTHONPATH": str(storage / "lpddr5-contracts/python-tools"),
    }
    source_paths = [
        IP / "rtl/generated/lpddr5_mc_register_pkg.sv",
        IP / "rtl/contracts/lpddr5_mc_contract_pkg.sv",
        *(IP / "rtl/cdc").glob("*.sv"),
        IP / "rtl/cdc/files.f",
        IP / "rtl/control/lpddr5_mc_apb_metadata_pkg.sv",
        IP / "rtl/host/lpddr5_mc_host_pkg.sv",
        IP / "rtl/host/lpddr5_mc_host_register_decode.sv",
        IP / "rtl/host/lpddr5_mc_host_channel_link.sv",
        IP / "dv/contracts/configuration.py",
        IP / "config/contracts/example.json",
        IP / "config/contracts/registers.json",
        *(IP / "dv/host").glob("adapter*"),
    ]
    manifest = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(source_paths)
        if p.is_file()
    }
    common = [str(verilator), "--timescale-override", "1ns/1ps", "--timing", "--assert", "-Wall"]
    packages = [
        "-f",
        str(IP / "rtl/contracts/files.f"),
        "-f",
        str(IP / "rtl/cdc/files.f"),
        str(IP / "rtl/control/lpddr5_mc_apb_metadata_pkg.sv"),
        str(IP / "rtl/host/lpddr5_mc_host_pkg.sv"),
    ]
    compile_flags = [
        "--binary",
        "--output-split",
        "10000",
        "--output-split-cfuncs",
        "500",
        "-MAKEFLAGS",
        "OPT_FAST=-O0 OPT_SLOW=-O0",
        "-j",
        "2",
    ]
    commands = [
        [sys.executable, "-m", "ruff", "check", "--config", str(IP / "tools/ruff.toml"), str(Path(__file__))],
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--check",
            "--config",
            str(IP / "tools/ruff.toml"),
            str(Path(__file__)),
        ],
        [
            *common,
            *compile_flags,
            "--top-module",
            "adapter_register_tb",
            "--Mdir",
            str(build / "register-obj"),
            "-I" + str(build),
            *packages,
            str(IP / "rtl/host/lpddr5_mc_host_register_decode.sv"),
            str(IP / "dv/host/adapter_register_tb.sv"),
        ],
        [str(build / "register-obj/Vadapter_register_tb"), "+vectors=" + str(build / "adapter_vectors.txt")],
    ]
    for depth in (16, 32, 64):
        commands.extend(
            [
                [
                    *common,
                    *compile_flags,
                    "--top-module",
                    "adapter_link_tb",
                    f"-GDEPTH={depth}",
                    "--Mdir",
                    str(build / f"link-{depth}-obj"),
                    *packages,
                    str(IP / "rtl/host/lpddr5_mc_host_channel_link.sv"),
                    str(IP / "dv/host/adapter_link_tb.sv"),
                ],
                [str(build / f"link-{depth}-obj/Vadapter_link_tb")],
            ]
        )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    results = []
    log = args.report.with_suffix(".log")
    with log.open("w") as stream:
        for command in commands:
            start = time.monotonic()
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
            results.append({"argv": command, "exit_code": result.returncode, "seconds": time.monotonic() - start})
            stream.flush()
            if result.returncode:
                raise SystemExit(f"Adapter check failed: see {log}")
    after = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(source_paths)
        if p.is_file()
    }
    if manifest != after:
        raise SystemExit("Adapter dependencies changed during checks; report withheld")
    output = log.read_text()
    markers = [line for line in output.splitlines() if re.match(r"ADAPTER_(REGISTER|LINK)_PASS", line)]
    if len(markers) != 4:
        raise SystemExit("Missing adapter completion markers")
    args.report.write_text(
        json.dumps(
            {
                "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)),
                "python": platform.python_version(),
                "verilator": subprocess.check_output([str(verilator), "--version"], env=env, text=True).strip(),
                "seed": SEED,
                "source_sha256": manifest,
                "commands": results,
                "canonical_oracle_cases": descriptions,
                "pass_markers": markers,
                "limitations": [
                    "Adapter and transport checks; real channel lifecycle/profile/pin behavior remains downstream.",
                    "Reset transport abortion is distinct from host acceptance-ledger settlement.",
                    "Coherent committed-image input is required; combinational decode is not a CDC mechanism.",
                ],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Adapter checks passed: {args.report}")


if __name__ == "__main__":
    main()
