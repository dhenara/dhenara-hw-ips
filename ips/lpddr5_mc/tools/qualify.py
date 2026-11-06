"""Retain deterministic numeric fixture and periodic schedule qualification."""

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

IP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(IP / "dv"))

from contracts.configuration import example_configuration, register_image, validate_register_image  # noqa: E402
from contracts.performance import check, witness  # noqa: E402
from contracts.profiles import admit, waveform  # noqa: E402
from contracts.waveform_oracle import check as check_waveform  # noqa: E402
from contracts.waveform_oracle import check_data_cs_sequence  # noqa: E402


def check_benchmark_profile(benchmark: dict) -> None:
    # The independent checker has standard-derived constants. Qualify its use
    # against this exact selected fixture; never substitute an arbitrary profile.
    for key, value in {
        "trcd": 15,
        "trp_ab": 17,
        "tras_min": 34,
        "tras_max": 56000,
        "trc": 51,
        "trrd": 4,
        "tfaw": 16,
        "trfc_ab": 168,
        "trefi": 3124,
        "twr": 28,
        "trbtp": 4,
        "tmrr": 8,
        "tzqlat": 24,
    }.items():
        if benchmark["dram_ck"][key] != value:
            raise ValueError("periodic witness fixture changed: " + key)
    if benchmark["rl_ck"] != 17 or benchmark["wl_ck"] != 9 or benchmark["mr"]["28"] != 0:
        raise ValueError("periodic witness latency/ZQ mode changed")
    if benchmark["device"]["mr27"] != 0x45 or benchmark["device"]["mr57"] != 0:
        raise ValueError("periodic witness RFM mode changed")
    if benchmark["wck"]["twck_dis"] != 20 or benchmark["phy_half_wck"] != {
        "command": 16,
        "wck": 16,
        "write": 18,
        "read": 16,
        "dq_input": 2,
        "dq_output": 6,
        "trained_phase": 0,
    }:
        raise ValueError("periodic witness PHY timing changed")


def qualification() -> dict:
    profiles_path = IP / "config/contracts/profiles.json"
    profiles = json.loads(profiles_path.read_text())["profiles"]
    schema = json.loads((IP / "config/contracts/registers.json").read_text())
    qualified = []
    traces = []
    for profile in profiles:
        admit(profile)
        count = 0
        for direction in ("read", "write"):
            for beats in (16, 32):
                for rank in range(profile["ranks"]):
                    for fast in (False, True):
                        event = waveform(profile, direction, beats, rank, fast)
                        check_waveform(profile, event, fast)
                        count += 1
                        if profile is profiles[0] and rank == 0:
                            traces.append({"fast_sync": fast, **asdict(event)})
        qualified.append({"name": profile["name"], "digital_waveform_cases": count})
        rank_cases = 0
        for first_direction in ("read", "write"):
            for next_direction in ("read", "write"):
                first = waveform(profile, first_direction, 32, rank=0)
                second = waveform(profile, next_direction, 32, rank=1)
                next_cas = max(first.disabled - second.cas, first.data_cs_end - second.data_cs[0][0])
                check_data_cs_sequence(profile, [(0, first), (next_cas, second)])
                rank_cases += 1
        qualified[-1]["data_cs_rank_transition_cases"] = rank_cases
    config = example_configuration()
    validate_register_image(register_image(schema, profiles[0], config), profiles[0], config, schema)
    benchmark = next(
        p
        for p in profiles
        if p["ratio"] == 4
        and p["always_on"]
        and not any(p[k] for k in ("read_link", "write_link", "read_dbi", "write_dbi"))
    )
    check_benchmark_profile(benchmark)
    schedules = []
    for direction in ("read", "write"):
        for divider in (1, 2, 4):
            commands = witness(4, direction, divider)
            schedules.append(
                {
                    "direction": direction,
                    "service_div": divider,
                    "commands_sha256": hashlib.sha256(repr(commands).encode()).hexdigest(),
                    **check(commands, 4, direction, divider),
                }
            )
    return {
        "scope": "digital reference contracts; no production controller or vendor PHY qualification",
        "profile_source_sha256": hashlib.sha256(profiles_path.read_bytes()).hexdigest(),
        "profiles": qualified,
        "representative_half_wck_traces": traces,
        "programming_example": "config/generated/example-register-image.json",
        "benchmark_profile": benchmark,
        "benchmark_configuration": config,
        "schedules": schedules,
        "seed": 2095,
        "result": "passed",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("report already exists")
    report = qualification()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Qualified {len(report['profiles'])} digital profiles and {len(report['schedules'])} periodic schedules")


if __name__ == "__main__":
    main()
