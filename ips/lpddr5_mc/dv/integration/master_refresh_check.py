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
        try:
            checker.observe(edge, sample)
            if edge["temperature_valid"]:
                dram.maintenance.temperature(edge["temperature"])
        except ValueError as error:
            error.observation = dict(edge=edge, sample=sample)
            raise
    assert dram.memory == {(0, 0, 0, 0): 0xA5}
    return checker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", action="store_true", help="dedicated real channel/register fixture")
    parser.add_argument("--channel-mode", type=int, choices=range(8), action="append", help="only these channel modes")
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.channel_mode and not args.channel:
        parser.error("--channel-mode requires --channel")
    modes = args.channel_mode if args.channel_mode is not None else range(3 if args.channel else 19)
    if len(set(modes)) != len(modes):
        parser.error("duplicate channel mode")
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
    top = "master_refresh_tb"
    includes = []
    if args.channel:
        top = "master_refresh_channel_tb"
        sv = [root / line for line in (ip / "rtl/channel/files.f").read_text().splitlines() if line]
        sv += [ip / "dv/channel/lpddr5_mc_channel_memory.sv", dv / f"{top}.sv"]
        sys.path.insert(0, str(ip / "dv"))
        from contracts.raw_parity import generate_read_encoder

        (build / "inputs").mkdir()
        generate_read_encoder(build / "inputs")
        includes = ["-I" + str(build / "inputs")]
    inputs = [
        *sv,
        Path(__file__),
        dv / "master_refresh.py",
        dv / "master_refresh_test.py",
        dv / "master_refresh_rfm.py",
    ]
    inputs += list((ip / "dv/contracts").glob("*.py"))
    inputs += [ip / "dv/system/dram.py", ip / "dv/system/host.py", ip / "config/contracts/profiles.json"]
    if args.channel:
        inputs += [ip / "rtl/channel/files.f", *list((build / "inputs").glob("*"))]
    snapshot = args.report.parent / "executed-inputs"
    for p in inputs:
        dest = snapshot / (p.relative_to(root) if p.is_relative_to(root) else Path("generated-inputs") / p.name)
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
        top,
        *includes,
    ]
    report = dict(
        status="running",
        base_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        source_changes=subprocess.check_output(["git", "status", "--short"], cwd=root, text=True),
        python=sys.version,
        verilator=subprocess.check_output([str(tool / "bin/verilator"), "--version"], env=env, text=True).strip(),
        qualification="coupled lifecycle/maintenance proof; actual-channel and wrapper evidence separate",
        commands=[],
        selected_modes=list(modes),
        build=str(build),
        tool_environment={k: env[k] for k in ("VERILATOR_ROOT", "VERILATOR_BIN")},
        source_sha256={str(p.relative_to(root) if p.is_relative_to(root) else p): digest(p) for p in inputs},
        seams=[
            "empty host and backend settlement, closed rows and disabled WCK",
            "maintenance request acceptance is direct DRAM-side CA, not the backend/DFI path",
            "MR4 and ZQ completion are bounded register-fixture attestations, not observed MRR returns",
            "PHY REF CA is separately observed; done attests issue, not tRFC completion",
        ],
    )

    if args.channel:
        report["qualification"] = "actual channel/register reachability; full AXI wrapper remains downstream"
        report["seams"] = [
            "modes0-5 empty granules; modes6/7 real accepted zero-image reads and ledger-derived settlement",
            "channel/granule proof only; no actual AXI or new independent pin-WCK/data qualification",
            "real CA-driven MRR returns and register/ZQ completion; no done constants",
            "controller CA physical delay is profile16 half-WCK / 8 = 2CK",
            "external PHY REF is directly decoded separately from sampled execution attestation",
        ]

    def run(argv, timeout=120):
        log = args.report.parent / f"command-{len(report['commands'])}.log"
        record = dict(argv=argv, log=str(log), timeout_seconds=timeout)
        report["commands"].append(record)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        start = time.monotonic()
        try:
            with log.open("x") as out:
                result = subprocess.run(
                    argv, cwd=root, env=env, stdout=out, stderr=subprocess.STDOUT, timeout=timeout, check=False
                )
            record["exit_code"] = result.returncode
            if result.returncode:
                raise RuntimeError(f"Command failed; inspect {log}")
        finally:
            record["seconds"] = time.monotonic() - start

    try:
        run([*base, "--lint-only", *map(str, sv)])
        run([*base, "--binary", "--Mdir", str(build), *map(str, sv), "-j", "2"], timeout=600 if args.channel else 120)
        generated = [p for p in build.iterdir() if p.is_file() and p.suffix in (".cpp", ".h", ".mk", ".dat")]
        generated += [build / f"V{top}"]
        report["generated_sha256"] = {p.name: digest(p) for p in generated}
        sys.path[:0] = [str(dv), str(ip / "dv")]
        from system.host import Violation

        profile = json.loads((ip / "config/contracts/profiles.json").read_text())["profiles"][0]
        report["cases"] = []
        for mode in modes:
            profile["ranks"] = (2 if mode == 1 else 1) if args.channel else 2 if mode in (0, 8, 13) else 1
            path = args.report.parent / f"mode-{mode}.jsonl"
            run([str(build / f"V{top}"), f"+mode={mode}", f"+observations={path}"])
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            edges, samples = rows[::2], rows[1::2]
            requests = [e for e in edges if e["pin_owner"] == "phy" and e["pin_cs"]]
            case = dict(
                mode=mode, records=len(rows), raw_sha256=digest(path), executions=requests, independent_phy_effects=[]
            )
            report["cases"].append(case)
            faults = [e for e in samples if e["fault"]]
            if args.channel and mode in (6, 7):
                from master_refresh_rfm import replay_rfm, sensitivity_rfm

                case["rfm_proof"] = replay_rfm(rows, profile, mode)
                case["sensitivity"] = sensitivity_rfm(rows, profile, mode)
                case["verdict"] = (
                    "contained pending RFM timeout" if mode == 6 else "healthy late RFM recovered handback"
                )
            elif args.channel and mode == 5:
                assert faults and faults[0]["code"] == 12 and not any(e["ack"] for e in samples)
                assert all(not e["admission"] and not e["issue"] and not e["trusted"] for e in faults)
                first = faults[0]
                requested = next(e["ck"] for e in edges if e["phy_request"])
                assert first["ck"] - requested == profile["management_ck"]["tphymstr_resp"] + 1
                case["request_to_fault_ck"] = first["ck"] - requested
                assert all(
                    e["fault"] and not e["ack"] and not e["admission"] and not e["issue"] and not e["trusted"]
                    for e in samples
                    if e["ck"] >= first["ck"]
                )
                assert samples[-1]["ck"] - first["ck"] >= 180
                try:
                    replay(rows, profile, case["independent_phy_effects"]).finish()
                except (Violation, ValueError) as error:
                    assert str(error) == "unfinished master tail", str(error)
                    case.update(verdict="contained entry response timeout", first_fault=first, rejection=str(error))
                else:
                    raise AssertionError("Unacknowledged entry passed")
            elif not args.channel and mode in (2, 3, 4, 5, 6, 9, 11, 12, 13, 14, 15, 16, 17):
                assert (
                    faults
                    and not faults[0]["admission"]
                    and not faults[0]["issue"]
                    and faults[0]["ack"] == int(mode not in (16, 17))
                )
                case["first_fault"] = faults[0]
                assert all(
                    not e["admission"] and not e["issue"] and e["ack"] == int(mode not in (16, 17))
                    for e in samples
                    if e["ck"] >= faults[0]["ck"]
                )
                try:
                    replay(rows, profile, case["independent_phy_effects"]).finish()
                except (Violation, ValueError) as error:
                    expected = {
                        2: "unfinished master tail",
                        3: "unfinished master tail",
                        4: "late PHY REF execution",
                        5: "unsolicited/stale/wrong-rank done",
                        6: "absent-rank refresh sideband",
                        9: "unfinished master tail",
                        11: "unfinished master tail",
                        12: "unfinished master tail",
                        13: "unsolicited/stale/wrong-rank done",
                        14: "unfinished master tail",
                        15: "missed periodic refresh",
                        16: "unsolicited/stale/wrong-rank done",
                        17: "unsolicited/stale/wrong-rank done",
                    }[mode]
                    assert str(error) == expected, (mode, str(error), expected)
                    case.update(verdict="contained rejected transaction", rejection=str(error))
                else:
                    raise AssertionError("Faulted unfinished/invalid transaction passed")
            else:
                assert not faults
                checker = replay(rows, profile, case["independent_phy_effects"])
                checker.finish()
                case.update(
                    verdict="healthy recovered handback", windows=checker.windows, attestations=checker.executions
                )
                assert len(checker.windows) == (2 if mode == 8 else 1)
                assert len(requests) == (
                    (0 if mode >= 2 else profile["ranks"]) if args.channel else 2 if mode == 0 else 1
                )
                if mode == 18 and not args.channel:
                    effect = case["independent_phy_effects"][0]
                    assert effect["before"]["raa"][0] == 1 and effect["after"]["raa"][0] == 0
                if args.channel:
                    # Controller CA is independently delayed by the declared
                    # 16 half-WCK / (2*ratio4) = 2CK path. PHY CA is direct.
                    delay = profile["phy_half_wck"]["command"] // (2 * profile["ratio"])
                    for i, edge in enumerate(edges):
                        if edge["pin_owner"] == "controller":
                            old = edges[i - delay] if i >= delay else dict(dfi_cs=0, dfi_ca=0)
                            assert (edge["pin_cs"], edge["pin_ca"]) == (old["dfi_cs"], old["dfi_ca"])
                    reports = [e for e in edges if e["temperature_valid"]]
                    assert reports and reports[0]["temperature"] == 12
                    assert any(e["register_result"] for e in edges)
                    assert any(e["return_valid"] for e in edges)
                    assert any(e["zq_done"] for e in edges)
                    case["thermal_publication"] = reports[0]
                    case["controller_ca_delay_ck"] = delay
                    request_edge = next(e for e in edges if e["phy_request"])
                    entry = next(s for e, s in zip(edges, samples, strict=True) if not e["ack"] and s["ack"])
                    case["entry"] = dict(request=request_edge, ack=entry, response_ck=entry["ck"] - request_edge["ck"])
                    zq = [
                        e["ck"]
                        for e in edges
                        if e["pin_cs"] and e["pin_ca"] == 0x370 and e["pin_owner"] == "controller"
                    ]
                    assert zq and max(zq) + profile["dram_ck"]["tzqlat"] <= entry["ck"]
                    case["physical_zq"] = dict(executions=zq, recovered=max(zq) + profile["dram_ck"]["tzqlat"])
        run([sys.executable, str(dv / "master_refresh_test.py")])
        assert all(
            digest(p) == report["source_sha256"][str(p.relative_to(root) if p.is_relative_to(root) else p)]
            for p in inputs
        )
        assert all(digest(p) == report["generated_sha256"][p.name] for p in generated)
        report["status"] = "passed_channel_preparation" if args.channel else "passed_focused_repair"
    except Exception as error:
        report.update(status="failed", error=repr(error))
        if hasattr(error, "observation"):
            report["first_rejection"] = error.observation
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
