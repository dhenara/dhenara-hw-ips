"""Timed capture/stop regression using the actual shared SV implementation."""

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    dv = Path(__file__).resolve().parent
    storage = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"])
    build = args.build.resolve()
    if not build.is_relative_to(storage) or build.exists() or args.report.exists():
        raise RuntimeError("Fresh isolated build and evidence report required")
    build.mkdir(parents=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    tool = storage / "lpddr5-contracts/verilator-5.006-3/usr"
    env = dict(os.environ, VERILATOR_ROOT=str(tool / "share/verilator"), VERILATOR_BIN="../../bin/verilator_bin")
    sources = [
        root / "ips/lpddr5_mc/rtl/contracts/lpddr5_mc_contract_pkg.sv",
        dv / "lpddr5_mc_observer.sv",
        dv / "wrapper_capture_tb.sv",
    ]
    report = dict(
        status="running",
        commands=[],
        cases=[],
        source_sha256={
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [*sources, dv / "wrapper_capture.svh", Path(__file__)]
        },
    )

    def run(argv, name):
        start = time.monotonic()
        log = args.report.parent / (name + ".log")
        with log.open("w") as stream:
            result = subprocess.run(
                argv, cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=60, check=False
            )
        report["commands"].append(
            dict(
                argv=[str(a) for a in argv], exit_code=result.returncode, seconds=time.monotonic() - start, log=str(log)
            )
        )
        if result.returncode:
            raise RuntimeError(f"Capture regression failed; see {log}")

    try:
        run(
            [
                str(tool / "bin/verilator"),
                "--binary",
                "--timing",
                "--timescale-override",
                "1ns/1ps",
                "--assert",
                "-Wall",
                "--top-module",
                "wrapper_capture_tb",
                "--Mdir",
                str(build),
                "-I" + str(dv),
                *map(str, sources),
                "-j",
                "2",
            ],
            "build",
        )
        # Before/at/after falling CK and coincident CK/AXI rising, plus the
        # independent source edge at24.5ns. Reset modes exercise drain and the
        # final rising NBA; the latter must remain an observed incomplete reset.
        selections = [
            (s, r, False) for s in (19999, 20000, 20001, 24499, 24500, 24501, 24999, 25000, 25001) for r in (0, 1, 2)
        ]
        selections.append((20001, 0, True))
        for index, (stop, reset, legacy) in enumerate(selections):
            name = f"case{index:02}"
            wrapper, source = [args.report.parent / (name + suffix) for suffix in ("-wrapper.jsonl", "-source.jsonl")]
            run(
                [
                    str(build / "Vwrapper_capture_tb"),
                    f"+stop_ps={stop}",
                    f"+reset_mode={reset}",
                    f"+capture={wrapper}",
                    f"+observations={source}",
                    *(["+legacy"] if legacy else []),
                ],
                name,
            )
            records = [json.loads(line) for line in wrapper.read_text().splitlines()]
            src = [json.loads(line) for line in source.read_text().splitlines()]
            samples = [e for e in records if e["kind"] == "sample" and e["channel"] == 0]
            hosts = [e for e in records if e["kind"] == "host_edge"]
            addresses = [(e["port"], e["ps"]) for e in records if e["kind"] == "address"]
            axi = [(e["port"], e["ps"]) for e in src if e["kind"] == "axi"]
            missing_host = sorted({e["tick"] for e in samples} - {e["tick"] for e in hosts})
            missing_address = sorted(set(axi) - set(addresses))
            if legacy:
                assert missing_host and missing_address, "Old guards must demonstrably lose real events"
            else:
                assert not missing_host and not missing_address
                assert len(addresses) == len(set(addresses)) == len(axi) == len(set(axi))
                assert set(addresses) == set(axi)
                for c in range(2):
                    for kind in ("edge", "management_edge", "window_edge"):
                        edges = [e for e in records if e["kind"] == kind and e["channel"] == c]
                        assert [e["tick"] for e in edges] == [e["tick"] for e in samples]
                        assert all(e["ps"] == s["ps"] + 5000 for e, s in zip(edges, samples, strict=True))
                assert records[-1]["kind"] == src[-1]["kind"] == "end"
                assert records[-1]["ps"] > src[-1]["ps"] == hosts[-1]["ps"]
                if reset:
                    resets = [e for e in records if e["kind"] == "host_reset"]
                    assert len(resets) == 1 and resets[0]["ps"] < records[-1]["ps"]
                    if reset == 1:
                        assert any(e["ps"] > resets[0]["ps"] and e["count"] == 0 for e in hosts)
                else:
                    handshake = next(e for e in hosts if e["handshake"])
                    assert any(e["ps"] > handshake["ps"] and e["count"] == handshake["count"] + 1 for e in hosts)
            report["cases"].append(
                dict(
                    stop_ps=stop,
                    reset_mode=reset,
                    legacy=legacy,
                    missing_host=missing_host,
                    missing_address=missing_address,
                    wrapper=str(wrapper),
                    source=str(source),
                    status="passed",
                )
            )
        report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
