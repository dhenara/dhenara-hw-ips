"""Run focused APB RTL checks with pinned tools and isolated compiler output."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
IP = ROOT / "ips/lpddr5_mc"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--build-subdir", default="apb-rtl")
    args = parser.parse_args()
    if args.report.exists():
        raise SystemExit("Report exists")
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"])
    build = storage / "lpddr5-host" / args.build_subdir
    build.mkdir(parents=True, exist_ok=True)
    schema_path = IP / "config/contracts/registers.json"
    schema = json.loads(schema_path.read_text())
    image = json.loads((IP / "config/generated/example-register-image.json").read_text())
    lines = ["begin", "  lpddr5_mc_register_image_t fixture;"]
    for index, reg in enumerate(schema["registers"]):
        lines.append(f"  fixture[{index}] = 32'h{image[reg['name']]:08x};")
    lines.extend(
        [
            "  for (int r = 0; r < REG_COUNT; r++) begin",
            "    if (REG_RW[r] != 0) begin",
            "      for (int c = 0; c < (REG_SHARED[r] ? 1 : 2); c++)",
            "        write_reg(18'(REG_OFFSET[r]) + (REG_SHARED[r] ? 18'd0 : 18'h10000 * 18'(c + 1)), fixture[r]);",
            "    end",
            "  end",
            "end",
        ]
    )
    (build / "apb_fixture.svh").write_text("\n".join(lines) + "\n")
    tool_root = storage / "lpddr5-contracts/verilator-5.006-3"
    verilator = tool_root / "usr/bin/verilator"
    env = {
        **os.environ,
        "VERILATOR_ROOT": str(tool_root / "usr/share/verilator"),
        "VERILATOR_BIN": "../../bin/verilator_bin",
        "PYTHONPATH": str(storage / "lpddr5-contracts/python-tools"),
    }
    source_paths = sorted(
        [
            *(IP / "rtl/control").glob("*"),
            *(IP / "rtl/cdc").glob("*.sv"),
            IP / "rtl/cdc/files.f",
            IP / "rtl/contracts/files.f",
            IP / "rtl/contracts/lpddr5_mc_contract_pkg.sv",
            IP / "rtl/generated/lpddr5_mc_register_pkg.sv",
            schema_path,
            IP / "config/generated/example-register-image.json",
            IP / "dv/host/apb_run.py",
            IP / "dv/host/apb_tb.sv",
            IP / "dv/host/apb_cdc_tb.sv",
            IP / "sw/examples/lpddr5_mc_control.c",
            IP / "sw/generated/lpddr5_mc_registers.h",
            IP / "docs/programming/host-control.md",
        ]
    )
    source_hashes = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths if p.is_file()
    }
    commands = [
        [sys.executable, str(IP / "rtl/control/generate_apb_metadata.py"), "--check"],
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            str(IP / "tools/ruff.toml"),
            str(IP / "rtl/control/generate_apb_metadata.py"),
            str(IP / "dv/host/apb_run.py"),
        ],
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--check",
            "--config",
            str(IP / "tools/ruff.toml"),
            str(IP / "rtl/control/generate_apb_metadata.py"),
            str(IP / "dv/host/apb_run.py"),
        ],
        [
            str(verilator),
            "--lint-only",
            "--timing",
            "--assert",
            "-Wall",
            "--top-module",
            "lpddr5_mc_apb",
            "-f",
            str(IP / "rtl/contracts/files.f"),
            "-f",
            str(IP / "rtl/control/files.f"),
        ],
        [
            str(verilator),
            "--lint-only",
            "--timescale-override",
            "1ns/1ps",
            "--timing",
            "--assert",
            "-Wall",
            "--top-module",
            "lpddr5_mc_control_link",
            "-f",
            str(IP / "rtl/contracts/files.f"),
            "-f",
            str(IP / "rtl/cdc/files.f"),
            "-f",
            str(IP / "rtl/control/files.f"),
        ],
        [
            str(verilator),
            "--binary",
            "--output-split",
            "10000",
            "--output-split-cfuncs",
            "500",
            "-MAKEFLAGS",
            "OPT_FAST=-O0 OPT_SLOW=-O0",
            "--timescale-override",
            "1ns/1ps",
            "--timing",
            "--assert",
            "-Wall",
            "--top-module",
            "apb_tb",
            "--Mdir",
            str(build / "obj"),
            "-I" + str(build),
            "-f",
            str(IP / "rtl/contracts/files.f"),
            "-f",
            str(IP / "rtl/control/files.f"),
            str(IP / "dv/host/apb_tb.sv"),
            "-j",
            "2",
        ],
        [str(build / "obj/Vapb_tb")],
        [
            str(verilator),
            "--binary",
            "--output-split",
            "10000",
            "--output-split-cfuncs",
            "500",
            "-MAKEFLAGS",
            "OPT_FAST=-O0 OPT_SLOW=-O0",
            "--timescale-override",
            "1ns/1ps",
            "--timing",
            "--assert",
            "-Wall",
            "--top-module",
            "apb_cdc_tb",
            "--Mdir",
            str(build / "cdc-obj"),
            "-f",
            str(IP / "rtl/contracts/files.f"),
            "-f",
            str(IP / "rtl/cdc/files.f"),
            "-f",
            str(IP / "rtl/control/files.f"),
            str(IP / "dv/host/apb_cdc_tb.sv"),
            "-j",
            "2",
        ],
        [str(build / "cdc-obj/Vapb_cdc_tb")],
        [
            "gcc",
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fsyntax-only",
            str(IP / "sw/examples/lpddr5_mc_control.c"),
        ],
    ]
    for parameter_set in (
        ["-GCHANNELS=1", "-GPORTS=1", "-GRANKS=1", "-GAXI_BITS=64", "-GADDRESS_BITS=32", "-GQUEUE_DEPTH=16"],
        ["-GCHANNELS=2", "-GPORTS=2", "-GRANKS=2", "-GAXI_BITS=256", "-GADDRESS_BITS=40", "-GQUEUE_DEPTH=64"],
    ):
        commands.append(
            [
                str(verilator),
                "--lint-only",
                "--timing",
                "--assert",
                "-Wall",
                "--top-module",
                "lpddr5_mc_apb",
                *parameter_set,
                "-f",
                str(IP / "rtl/contracts/files.f"),
                "-f",
                str(IP / "rtl/control/files.f"),
            ]
        )
    results = []
    args.report.parent.mkdir(parents=True, exist_ok=True)
    log = args.report.with_suffix(".log")
    with log.open("w") as stream:
        for command in commands:
            start = time.monotonic()
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
            results.append({"argv": command, "exit_code": result.returncode, "seconds": time.monotonic() - start})
            stream.flush()
            if result.returncode:
                raise SystemExit(f"Failed command; see {log}")
    after_hashes = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths if p.is_file()
    }
    if after_hashes != source_hashes:
        raise SystemExit("APB source changed during validation; report withheld")
    output = log.read_text()
    if "APB_PASS" not in output or "APB_CDC_PASS" not in output:
        raise SystemExit("Missing APB behavior completion markers")
    report = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)),
        "python": platform.python_version(),
        "verilator": subprocess.check_output([str(verilator), "--version"], env=env, text=True).strip(),
        "source_sha256": source_hashes,
        "commands": results,
        "limitations": [
            "APB and control-link RTL tested with independent clocks. "
            "Actual channel numeric-profile/drain validation and final system wiring remain integration dependencies.",
            "No combined controller, pin, throughput or physical qualification.",
        ],
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"APB RTL checks passed: {args.report}")


if __name__ == "__main__":
    main()
