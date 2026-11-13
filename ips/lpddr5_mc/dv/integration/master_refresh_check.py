"""Bounded lifecycle/maintenance seam; never invokes the controller wrapper."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def replay(rows, profile, observations=None):
    from master_refresh import MasterRefresh
    from system.dram import DramModel

    dram = DramModel(profile)
    # An explicit initial image, not evidence of an AXI write. REF must retain
    # it. The same object and retention owner span the entire observation.
    dram.memory[0, 0, 0, 0] = 0xA5
    checker = MasterRefresh(dram, observations=observations)
    for edge, sample in zip(rows[::2], rows[1::2], strict=True):
        checker.observe(edge, sample)
        if edge["temperature_valid"]:
            dram.maintenance.temperature(edge["temperature"])
    assert dram.memory == {(0, 0, 0, 0): 0xA5}
    return checker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    dv = Path(__file__).resolve().parent
    ip = root / "ips/lpddr5_mc"
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"])
    build = args.build.resolve()
    if not build.is_relative_to(storage) or build.exists() or args.report.exists():
        raise RuntimeError("Fresh isolated build and report required")
    build.mkdir(parents=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    sv = [
        ip / "rtl/contracts/lpddr5_mc_contract_pkg.sv",
        ip / "rtl/channel/lpddr5_mc_channel_pkg.sv",
        ip / "rtl/channel/lpddr5_mc_lifecycle.sv",
        ip / "rtl/channel/lpddr5_mc_maintenance.sv",
        dv / "master_refresh_tb.sv",
    ]
    inputs = [*sv, Path(__file__), dv / "master_refresh.py", dv / "master_refresh_test.py"]
    inputs += list((ip / "dv/contracts").glob("*.py"))
    inputs += [ip / "dv/system/dram.py", ip / "dv/system/host.py", ip / "config/contracts/profiles.json"]
    snapshot = args.report.parent / "executed-inputs"
    for p in inputs:
        dest = snapshot / p.relative_to(root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)

    def digest(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()

    tool = storage / "lpddr5-contracts/verilator-5.006-3/usr"
    env = dict(os.environ, VERILATOR_ROOT=str(tool / "share/verilator"), VERILATOR_BIN="../../bin/verilator_bin")
    base = [
        str(tool / "bin/verilator"),
        "--timing",
        "--timescale-override",
        "1ns/1ps",
        "--assert",
        "-Wall",
        "--top-module",
        "master_refresh_tb",
    ]
    report = dict(
        status="running",
        base_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        source_changes=subprocess.check_output(["git", "status", "--short"], cwd=root, text=True),
        python=sys.version,
        verilator=subprocess.check_output([str(tool / "bin/verilator"), "--version"], env=env, text=True).strip(),
        qualification="preparation seam only; full active-refresh reachability unresolved",
        commands=[],
        source_sha256={str(p.relative_to(root)): digest(p) for p in inputs},
        seams=[
            "empty host and backend settlement, closed rows and disabled WCK",
            "maintenance request acceptance is direct DRAM-side CA, not the backend/DFI path",
            "MR4 and ZQ completion are bounded register-fixture attestations, not observed MRR returns",
            "PHY REF CA is separately observed; done attests issue, not tRFC completion",
        ],
    )

    def run(argv):
        log = args.report.parent / f"command-{len(report['commands'])}.log"
        record = dict(argv=argv, log=str(log), timeout_seconds=120)
        report["commands"].append(record)
        start = time.monotonic()
        try:
            with log.open("x") as out:
                result = subprocess.run(
                    argv, cwd=root, env=env, stdout=out, stderr=subprocess.STDOUT, timeout=120, check=False
                )
            record["exit_code"] = result.returncode
            if result.returncode:
                raise RuntimeError(f"Command failed; inspect {log}")
        finally:
            record["seconds"] = time.monotonic() - start

    try:
        run([*base, "--lint-only", *map(str, sv)])
        run([*base, "--binary", "--Mdir", str(build), *map(str, sv), "-j", "2"])
        sys.path[:0] = [str(dv), str(ip / "dv")]
        from system.host import Violation

        profile = json.loads((ip / "config/contracts/profiles.json").read_text())["profiles"][0]
        report["cases"] = []
        for mode in (0, 1):
            profile["ranks"] = 2 if mode == 0 else 1
            path = args.report.parent / f"mode-{mode}.jsonl"
            run([str(build / "Vmaster_refresh_tb"), f"+mode={mode}", f"+observations={path}"])
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            edges = rows[::2]
            ack = next(e for e in rows[1::2] if e["ack"])
            requests = [e for e in edges if e["pin_owner"] == "phy" and e["pin_cs"]]
            assert len(requests) == profile["ranks"] and {e["pin_cs"] for e in requests} == set(
                range(1, profile["ranks"] + 1)
            )
            case = dict(mode=mode, records=len(rows), ack=ack, executions=requests)
            case["independent_phy_effects"] = []
            case["physical_ready_ck"] = max(e["ck"] + profile["dram_ck"]["trfc_ab"] for e in requests)
            case["type_end_ck"] = ack["ck"] + profile["management_ck"]["tphymstr_type0"]
            case["raw_reassertions"] = [
                sample
                for edge, sample in zip(rows[::2], rows[1::2], strict=True)
                if edge["external"] and sample["request"] & edge["external"]
            ]
            assert case["raw_reassertions"]
            if mode == 0:
                checker = replay(rows, profile, case["independent_phy_effects"])
                assert len(checker.executions) == 2 and not checker.returned
                assert any(e["fault"] and e["code"] == 8 for e in edges)
                try:
                    checker.finish()
                except Violation as error:
                    case.update(verdict="type limit prevents legal recovered handback", rejection=str(error))
                else:
                    raise AssertionError("Unfinished fault tail passed")
            else:
                handback = next(
                    sample
                    for edge, sample in zip(rows[::2], rows[1::2], strict=True)
                    if edge["ack"] and not sample["ack"]
                )
                service = next(e for e in edges if e["ck"] > ack["ck"] and e["admission"] and e["issue"])
                assert handback["ck"] < service["ck"] < case["physical_ready_ck"]
                case.update(handback=handback, renewed_service=service)
                try:
                    replay(rows, profile, case["independent_phy_effects"])
                except Violation as error:
                    assert str(error) in (
                        "master handback before PHY REF recovery",
                        "master unfinished refresh at handback",
                    )
                    case.update(verdict="early handback rejected independently", rejection=str(error))
                else:
                    raise AssertionError("Premature handback passed")
            report["cases"].append(case)
        run([sys.executable, str(dv / "master_refresh_test.py")])
        assert all(digest(p) == report["source_sha256"][str(p.relative_to(root))] for p in inputs)
        generated = [p for p in build.iterdir() if p.is_file() and p.suffix in (".cpp", ".h", ".mk", ".dat")]
        generated += [build / "Vmaster_refresh_tb"]
        report["generated_sha256"] = {p.name: digest(p) for p in generated}
        report["status"] = "passed_expected_counterexamples"
    except Exception as error:
        report.update(status="failed", error=repr(error))
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
