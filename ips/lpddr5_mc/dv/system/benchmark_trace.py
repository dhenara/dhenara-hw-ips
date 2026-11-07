"""Replay sampled RTL-fixture handshakes into the exact 100-period accountant.

This format is a stimulus qualification seam, not a production result. The
stream fixture checks its data/response identities in SV. Production adapters
must also provide independent host/pin/protocol validation before completion.
"""

import argparse
import json
from pathlib import Path

from system.control import ManagementPort, ResponseScript
from system.host import require
from system.measurement import Benchmark


def replay(events: list[dict], direction: str, service: int) -> dict:
    kinds = ("update", "training", "pm", "fsp", "master", "fsp", "pm")
    management = [
        ManagementPort(
            kind,
            ResponseScript(
                None if i >= 5 else 1,
                None if i >= 5 else 11 if kind == "fsp" else 6 if kind == "pm" else 3,
                initial=kind in ("training", "fsp"),
            ),
            completion_limit=5,
        )
        for i, kind in enumerate(kinds)
    ]
    for e in events:
        if e["kind"] == "management":
            lane = e["lane"]
            require(0 <= lane < len(management), "unknown management fixture lane")
            fsp = {"clock_change_done": e["clock_done"], "target": (1, 2, 1)} if kinds[lane] == "fsp" else {}
            management[lane].sample(
                e["edge"],
                request=e["request"],
                response=e["response"],
                traffic=e["traffic"],
                drained=e["drained"],
                wck_disabled=e["wck_disabled"],
                returned_state_known=e["known"],
                **fsp,
            )
    for monitor in management:
        monitor.finish()
    benchmark = Benchmark(direction, service)
    require(any(e["kind"] == "stimulus" for e in events), "missing RTL stimulus summary")
    for channel in range(2):
        found = [e for e in events if e["kind"] == "stimulus" and e["channel"] == channel]
        require(
            len(found) == 1
            and found[0]["max_live"] > 1
            and found[0]["half_ps"] == 1250
            and found[0]["consecutive_r" if direction == "read" else "consecutive_w"] > 100000,
            "missing pipelined stimulus",
        )
    timed = [e for e in events if e["kind"] in ("admit", "complete")]
    require(timed, "missing observed handshakes")
    ends = [e["ps"] for e in events if e["kind"] == "end"]
    require(
        len(ends) == 1 and ends[0] >= max(e["ps"] for e in timed) and ends[0] >= 375000000,
        "missing/truncated 100-period observation",
    )
    # Stable order retains actual same-edge source observations. Boundary
    # backlog is sampled just before observations on its exact edge.
    timeline = [(e["ps"], 1, i, e) for i, e in enumerate(timed)]
    for ck in range(0, 300001, 3000):
        for channel in range(2):
            timeline.append((ck * 1250, 0, channel, {"kind": "boundary", "channel": channel}))
    for ps, _, _, e in sorted(timeline):
        ck, channel = ps // 1250, e["channel"]
        if e["kind"] == "admit":
            benchmark.admission(ck, channel, e["tag"])
        elif e["kind"] == "complete":
            benchmark.completion(ck, channel, e["tag"], physical_ok=True, integrity_ok=True)
        else:
            benchmark.boundary(ck, channel)
    require(benchmark.outstanding == [0, 0], "unsettled fixture handshakes")
    startup = []
    for channel in range(2):
        first_admit = min(e["ps"] for e in timed if e["channel"] == channel and e["kind"] == "admit")
        first_complete = min(e["ps"] for e in timed if e["channel"] == channel and e["kind"] == "complete")
        startup.append((first_complete - first_admit) // 1250)
    report = benchmark.report(ends[0] // 1250)
    report["observed_until_ps"] = ends[0]
    report["sampled_management_ports"] = len(management)
    report["management_dispositions"] = [m.disposition for m in management]
    report["startup_latency_ck"] = startup
    report["kind"] = "rtl_stimulus_measurement"
    report["source"] = "sampled_fixture_AXI_edges"
    report["stimulus"] = [e for e in events if e["kind"] == "stimulus"]
    report["limitations"] = [
        "Finite toy endpoint has no LPDDR5 maintenance, channel scheduler or CDC.",
        "Service divisor selects the comparison reference only; this fixture does not implement selection enables.",
        "Not combined-controller performance or protocol evidence.",
    ]
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("log", type=Path)
    p.add_argument("--direction", choices=("read", "write"), required=True)
    p.add_argument("--service", type=int, choices=(1, 2, 4), required=True)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()
    require(not a.report.exists(), "preserve prior benchmark evidence")
    events = [json.loads(line) for line in a.log.read_text().splitlines() if line.startswith("{")]
    report = replay(events, a.direction, a.service)
    a.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
