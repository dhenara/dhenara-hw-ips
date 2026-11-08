"""Measure finite workload costs from sampled source/pin observations.

Use alongside the owning data/protocol checker. Successful source handshakes
provide payload credit; command counts and response stalls describe overhead.
These intervals include source pacing and backpressure, so their rates are not
sustained-bandwidth qualification or isolated feature-penalty estimates.
"""

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from system.dram import decode
from system.host import Address, Violation, byte_ranges, require
from system.trace import number, source_order


@dataclass
class Transfer:
    start: int
    lengths: list[int]
    write: bool
    data_beats: int = 0
    strobed_bytes: int = 0
    response_beats: int = 0
    failed: bool = False


@dataclass
class Direction:
    admitted: int = 0
    completed: int = 0
    failed: int = 0
    successful_payload_bytes: int = 0
    response_handshakes: int = 0
    response_stall_samples: int = 0
    latencies: list[int] = field(default_factory=list)

    def report(self, elapsed: int) -> dict:
        return {
            "admitted_transactions": self.admitted,
            "completed_transactions": self.completed,
            "failed_transactions": self.failed,
            "successful_payload_bytes": self.successful_payload_bytes,
            "gb_per_second_over_shared_interval": self.successful_payload_bytes * 1000 / elapsed,
            "response_handshakes": self.response_handshakes,
            "response_stall_source_samples": self.response_stall_samples,
            "latency_ps": {
                "minimum": min(self.latencies) if self.latencies else None,
                "maximum": max(self.latencies) if self.latencies else None,
                "mean": sum(self.latencies) / len(self.latencies) if self.latencies else None,
            },
        }


def segment(events, start: int, end: int):
    """Select a sampled interval only at empty source-transaction boundaries.

    Inspect the full source ledger so a transaction spanning either boundary
    cannot disappear from a cost report. The original end marker must cover
    the selected interval. Correctness still belongs to the full trace replay.
    """
    require(type(start) is int and type(end) is int and 0 <= start < end, "workload segment bounds")
    yield next(events)
    pending = Counter()
    entered = exited = ended = False
    last = -1
    for event in events:
        kind, ps = event["kind"], event["ps"]
        require(not ended and type(ps) is int and ps >= last, "workload segment time/order/tail")
        last = ps
        if not entered and ps >= start:
            require(not any(pending.values()), "workload transaction crosses segment start")
            entered = True
        if not exited and ps > end:
            require(not any(pending.values()), "workload transaction crosses segment end")
            exited = True
        if kind in ("aw", "ar"):
            pending[event["port"], kind == "aw", event["id"]] += 1
        elif kind in ("b", "r") and event["ready"] and (kind == "b" or event["last"]):
            key = event["port"], kind == "b", event["id"]
            require(pending[key] > 0, "workload segment unmatched response")
            pending[key] -= 1
        elif kind == "end":
            require(ps >= end and not any(pending.values()), "workload segment uncovered/unsettled end")
            ended = True
        if start <= ps <= end and kind != "end":
            yield event
    require(entered and ended, "workload segment truncated trace")
    yield {"kind": "end", "ps": end}


def measure(path: Path, *, start_ps: int | None = None, end_ps: int | None = None) -> dict:
    """Account for complete AW-before-W traces, including partial/error traffic.

    The current connected binding observes W in AW order. B/R may complete
    different IDs out of order; reuse of an ID retains its admission order.
    Operational resets need separate epoch segments and are rejected here.
    """
    directions = [Direction(), Direction()]
    responses, writes = defaultdict(deque), defaultdict(deque)
    commands, rank_switches, turns = Counter(), Counter(), Counter()
    previous = {}
    first = final = observation_start = None
    last = -1
    ended = False
    transferred_strobes = fault_events = physical_events = 0
    with path.open() as stream:
        events = source_order(stream)
        if start_ps is not None or end_ps is not None:
            events = segment(events, start_ps, end_ps)
        header = next(events)
        require(header["kind"] == "header" and header["version"] == 1, "workload observation header")
        bus_bytes, ports, channels = header["bus_bytes"], header["ports"], header["channels"]
        require(bus_bytes in (8, 16, 32) and ports in (1, 2) and channels in (1, 2), "workload configuration")
        for event in events:
            kind, ps = event["kind"], event["ps"]
            require(not ended and type(ps) is int and ps >= last, "workload time/order/tail")
            last = ps
            if observation_start is None:
                observation_start = ps
            if kind in ("aw", "ar"):
                port, identity, write = event["port"], event["id"], kind == "aw"
                require(0 <= port < ports, "workload source port")
                address = Address(
                    port, identity, number(event["address"]), event["length"], event["size"], event["burst"]
                )
                try:
                    lengths = [len(beat) for beat in byte_ranges(address, bus_bytes)]
                except Violation:
                    lengths = [0] * (address.length + 1)
                transfer = Transfer(ps, lengths, write)
                responses[port, write, identity].append(transfer)
                if write:
                    writes[port].append(transfer)
                directions[write].admitted += 1
                first = ps if first is None else first
            elif kind == "w":
                require(bool(writes[event["port"]]), "workload W without AW")
                transfer = writes[event["port"]][0]
                require(transfer.data_beats < len(transfer.lengths), "workload excess W")
                mask = number(event["strobe"])
                require(0 <= mask < 1 << bus_bytes, "workload strobe width")
                transfer.strobed_bytes += mask.bit_count()
                transferred_strobes += mask.bit_count()
                transfer.data_beats += 1
                if transfer.data_beats == len(transfer.lengths):
                    writes[event["port"]].popleft()
            elif kind in ("b", "r"):
                write = kind == "b"
                queue = responses[event["port"], write, event["id"]]
                require(bool(queue), "workload response without admission")
                transfer, stats = queue[0], directions[write]
                if not event["ready"]:
                    stats.response_stall_samples += 1
                    continue
                stats.response_handshakes += 1
                transfer.response_beats += 1
                transfer.failed |= event["response"] != 0
                if not write:
                    require(transfer.response_beats <= len(transfer.lengths), "workload excess R")
                    require(
                        bool(event["last"]) == (transfer.response_beats == len(transfer.lengths)),
                        "workload R length/last",
                    )
                if write or event["last"]:
                    require(not write or transfer.data_beats == len(transfer.lengths), "workload B before full W")
                    queue.popleft()
                    stats.completed += 1
                    stats.failed += int(transfer.failed)
                    if not transfer.failed:
                        stats.successful_payload_bytes += transfer.strobed_bytes if write else sum(transfer.lengths)
                    stats.latencies.append(ps - transfer.start)
                    final = ps
            elif kind == "phy":
                channel = event["channel"]
                require(0 <= channel < channels, "workload physical channel")
                physical_events += 1
                if event["cs"]:
                    command = decode(event["ca"])
                    commands[channel, command.kind] += 1
                    if command.kind in ("RD", "WR", "MWR"):
                        access = event["cs"], command.kind != "RD"
                        if channel in previous:
                            rank_switches[channel] += int(access[0] != previous[channel][0])
                            turns[channel] += int(access[1] != previous[channel][1])
                        previous[channel] = access
            elif kind == "fault":
                fault_events += 1
            elif kind == "end":
                ended = True
            else:
                require(kind == "axi", "unsupported workload event/epoch")
    require(ended and first is not None and final is not None and final > first, "workload missing interval/tail")
    require(not any(responses.values()) and not any(writes.values()), "workload unsettled source tail")
    require(physical_events > 0, "workload missing physical observations")
    elapsed = final - first
    return {
        "kind": "finite_workload_measurement",
        "first_admission_ps": first,
        "last_completion_ps": final,
        "elapsed_ps": elapsed,
        "observation_start_ps": observation_start,
        "observation_end_ps": last,
        "read": directions[0].report(elapsed),
        "write": directions[1].report(elapsed),
        "accepted_w_strobe_bytes_including_failed_writes": transferred_strobes,
        "commands_by_channel": {"/".join(map(str, key)): value for key, value in sorted(commands.items())},
        "access_rank_switches_by_channel": dict(rank_switches),
        "access_direction_changes_by_channel": dict(turns),
        "fault_events": fault_events,
        "limits": [
            "Measurements require separate owning data/protocol validation; no correctness verdict is supplied.",
            "Shared first-admission to last-completion interval includes source pacing, interference and backpressure.",
            "Payload credits fully successful transactions only: strobed write bytes and addressed read bytes.",
            "Finite workload rates have no benchmark threshold or isolated feature-penalty interpretation.",
            "Physical commands cover the complete observation interval, including boot/drain and idle maintenance.",
        ],
    }
