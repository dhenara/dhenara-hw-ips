"""Non-mutating owning check entry point; build products use tool storage."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
IP = REPO / "ips/lpddr5_mc"
COMMANDS: list[dict] = []
SHAPING_COMMIT = "252648f79786d740c6518abfdb58a325e70af500"
INITIAL_COMMIT = "b46072841f1a8e89bb110ee2ed578af6fc80dd0a"
PREDECESSOR_COMMIT = "441e24c847bd909aaa297ffa3928440b45843c22"
ACCEPTED_DOCUMENTS = {
    "lpddr5_mc_microarch_spec.md": "0d05f3cd2fe858ba30d4a87d89a117989fbbf6d7a5889cf18ae12da09d437ba6",
    "lpddr5_mc_microarch_addendum_v0.2.md": "7aaff327b6f857a37a7a66e89c7614318205dd103d905f840734ddd68bbc6d4b",
    "review.md": "d5b1e4d85631a755f0b47134c2b8b42aeea55ee52d45d1ce2fb15677968c3c4c",
}


def run(argv: list[str], env: dict[str, str]) -> None:
    print("RUN " + " ".join(argv), flush=True)
    start = time.monotonic()
    subprocess.run(argv, cwd=REPO, env=env, check=True)
    COMMANDS.append({"argv": argv, "elapsed_seconds": time.monotonic() - start, "exit_code": 0})


def check_docs() -> None:
    original = IP / "docs/spec"
    for name, expected in ACCEPTED_DOCUMENTS.items():
        if hashlib.sha256((original / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Accepted source document changed: " + name)
    for line in (original / "SHA256SUMS").read_text().splitlines():
        expected, name = line.split()
        if hashlib.sha256((original / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Original document changed: " + name)
    paths = [
        REPO / "README.md",
        REPO / "CONTRIBUTING.md",
        *REPO.glob("docs/**/*.md"),
        IP / "README.md",
        *(IP / "docs").glob("**/*.md"),
    ]
    for path in paths:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text()):
            if "://" in target or target.startswith(("#", "scratchpad/")):
                continue
            location = target.split("#", 1)[0]
            if location and not (path.parent / location).resolve().exists():
                raise ValueError(f"Broken relative link in {path.relative_to(REPO)}: {target}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    parser.add_argument("--format", action="store_true")
    parser.add_argument("--build-subdir", default="contract-smoke")
    args = parser.parse_args()
    if sys.version_info[:3] != (3, 13, 18):
        raise SystemExit("This reference flow is qualified only with Python 3.13.18")
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]).resolve()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.build_subdir):
        parser.error("build subdirectory must be a simple name")
    out = storage / "lpddr5-contracts" / args.build_subdir
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(IP / "dv")
    env["RUFF_CACHE_DIR"] = str(storage / "lpddr5-contracts/ruff-cache")
    env["PYTHONPYCACHEPREFIX"] = str(storage / "lpddr5-contracts/pycache")
    tool = storage / "lpddr5-contracts/verilator-5.006-3"
    env["VERILATOR_ROOT"] = str(tool / "usr/share/verilator")
    env["VERILATOR_BIN"] = "../../bin/verilator_bin"
    ruff = str(storage / "lpddr5-contracts/python-tools/bin/ruff")
    ruff_version = subprocess.check_output([ruff, "--version"], text=True).strip()
    if ruff_version != "ruff 0.17.4":
        raise ValueError("Unqualified Ruff version: " + ruff_version)
    owned_python = ["ips/lpddr5_mc/dv", "ips/lpddr5_mc/tools"]
    ruff_config = ["--config", "ips/lpddr5_mc/tools/ruff.toml"]
    if args.format:
        run([ruff, "format", *ruff_config, *owned_python], env)
        run([ruff, "check", "--fix", *ruff_config, *owned_python], env)
        return
    if args.report is None:
        parser.error("--report is required for a non-mutating check")
    if args.report.exists():
        parser.error("report already exists; choose a new path to preserve earlier evidence")
    run([ruff, "check", *ruff_config, *owned_python], env)
    run([ruff, "format", "--check", *ruff_config, *owned_python], env)
    verilator = str(tool / "usr/bin/verilator")
    version = subprocess.check_output([verilator, "--version"], env=env, text=True).strip()
    if not version.startswith("Verilator 5.006 "):
        raise ValueError("Unqualified simulator version: " + version)
    check_docs()
    run(["git", "merge-base", "--is-ancestor", INITIAL_COMMIT, SHAPING_COMMIT], env)
    run(["git", "merge-base", "--is-ancestor", SHAPING_COMMIT, PREDECESSOR_COMMIT], env)
    run(["git", "merge-base", "--is-ancestor", PREDECESSOR_COMMIT, "HEAD"], env)
    run(["git", "diff", "--check"], env)
    run([sys.executable, "ips/lpddr5_mc/tools/generate.py", "--check"], env)
    run(
        [
            sys.executable,
            "ips/lpddr5_mc/tools/qualify.py",
            "--report",
            str(args.report.with_name(args.report.stem + "-qualification.json")),
        ],
        env,
    )
    run(
        [
            "gcc",
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic",
            "-I",
            "ips/lpddr5_mc/sw/generated",
            "ips/lpddr5_mc/dv/contracts/register_header_smoke.c",
            "-o",
            str(out / "register_header_smoke"),
        ],
        env,
    )
    run([str(out / "register_header_smoke")], env)
    run([sys.executable, "-m", "unittest", "discover", "-s", "ips/lpddr5_mc/dv/tests", "-v"], env)
    run(
        [
            verilator,
            "--binary",
            "--assert",
            "--top-module",
            "lpddr5_mc_contract_tb",
            "--Mdir",
            str(out),
            "-j",
            "2",
            "-Wall",
            "-f",
            "ips/lpddr5_mc/rtl/contracts/files.f",
            "ips/lpddr5_mc/dv/contracts/lpddr5_mc_contract_tb.sv",
        ],
        env,
    )
    run([str(out / "Vlpddr5_mc_contract_tb")], env)
    report = {
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO)),
        "python": sys.version,
        "verilator": version,
        "ruff": ruff_version,
        "compiler": subprocess.check_output(["g++", "--version"], text=True).splitlines()[0],
        "make": subprocess.check_output(["make", "--version"], text=True).splitlines()[0],
        "provenance": {
            "initial_commit": INITIAL_COMMIT,
            "retained_shaping_commit": SHAPING_COMMIT,
            "retained_predecessor_commit": PREDECESSOR_COMMIT,
        },
        "accepted_document_sha256": ACCEPTED_DOCUMENTS,
        "commands": COMMANDS,
        "seed": 2095,
        "result": "passed",
        "scope": "canonical contracts, digital PHY fixtures and independent periodic schedules",
        "controller_implemented": False,
        "digital_fixtures_qualified": True,
        "vendor_phy_qualified": False,
        "remaining": ["Production controller and multi-clock RTL qualification; lead/owner agreement review"],
        "qualification_report": args.report.stem + "-qualification.json",
    }
    source_names = (
        subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=REPO)
        .decode()
        .split("\0")
    )
    report["source_files"] = {
        name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
        for name in sorted(set(source_names))
        if name and (REPO / name).is_file()
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
