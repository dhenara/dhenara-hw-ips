"""Known sampled intervals qualify cost accounting, not controller behavior."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from contracts.commands import access
from system.host import Violation
from workload_costs import measure


def address(kind, ps, identity, at=0, length=1, size=4):
    return {
        "kind": kind,
        "ps": ps,
        "port": 0,
        "id": identity,
        "address": at,
        "length": length,
        "size": size,
        "burst": 1,
        "lock": 0,
    }


def response(kind, ps, identity, *, ready=1, failed=0, last=1):
    result = {"kind": kind, "ps": ps, "port": 0, "id": identity, "ready": ready, "response": 2 if failed else 0}
    if kind == "r":
        result.update(data=0, last=last)
    return result


def observations():
    return [
        {"kind": "header", "version": 1, "ports": 1, "channels": 1, "bus_bytes": 16},
        {"kind": "phy", "ps": 0, "ck": 0, "channel": 0, "cs": 0, "ca": 0},
        address("aw", 10, 1),
        address("ar", 11, 2, at=18, size=2),
        {"kind": "w", "ps": 12, "port": 0, "data": 0, "strobe": 3, "last": 0},
        {"kind": "w", "ps": 13, "port": 0, "data": 0, "strobe": 32768, "last": 1},
        response("b", 20, 1, ready=0),
        response("b", 21, 1, ready=0),
        response("r", 22, 2, last=0),
        response("b", 30, 1),
        response("r", 35, 2),
        address("aw", 40, 3),
        address("ar", 41, 4),
        {"kind": "w", "ps": 42, "port": 0, "data": 0, "strobe": 65535, "last": 0},
        {"kind": "w", "ps": 43, "port": 0, "data": 0, "strobe": 65535, "last": 1},
        response("r", 60, 4, failed=1, last=0),
        response("b", 65, 3, failed=1),
        response("r", 70, 4, failed=1),
        {"kind": "end", "ps": 80},
    ]


class WorkloadCostsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"])
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "observations.jsonl"

    def replay(self, events):
        self.path.write_text("".join(json.dumps(e) + "\n" for e in events))
        return measure(self.path)

    def test_partial_unaligned_narrow_errors_and_stalls(self):
        result = self.replay(observations())
        self.assertEqual(result["elapsed_ps"], 60)
        self.assertEqual(result["observation_end_ps"], 80)
        self.assertEqual(result["write"]["successful_payload_bytes"], 3)
        self.assertEqual(result["read"]["successful_payload_bytes"], 6)
        self.assertEqual(result["accepted_w_strobe_bytes_including_failed_writes"], 35)
        self.assertEqual(result["write"]["response_stall_source_samples"], 2)
        self.assertEqual(result["write"]["gb_per_second_over_shared_interval"], 50)
        self.assertEqual(result["read"]["gb_per_second_over_shared_interval"], 100)
        for direction in ("read", "write"):
            self.assertEqual(result[direction]["completed_transactions"], 2)
            self.assertEqual(result[direction]["failed_transactions"], 1)
        self.assertEqual(result["read"]["latency_ps"], {"minimum": 24, "maximum": 29, "mean": 26.5})
        self.assertEqual(result["write"]["latency_ps"], {"minimum": 20, "maximum": 25, "mean": 22.5})

    def test_different_ids_can_complete_out_of_order_and_same_id_can_recur(self):
        events = observations()[:2] + [
            address("ar", 10, 1, length=0),
            address("ar", 11, 2, length=0),
            address("ar", 12, 1, length=0),
            response("r", 20, 2),
            response("r", 21, 1),
            response("r", 22, 1),
            {"kind": "end", "ps": 23},
        ]
        result = self.replay(events)
        self.assertEqual(result["read"]["successful_payload_bytes"], 48)
        self.assertEqual(result["read"]["latency_ps"], {"minimum": 9, "maximum": 11, "mean": 10})
        self.assertIsNone(result["write"]["latency_ps"]["maximum"])

    def test_sampled_command_counts_and_turns(self):
        events = observations()
        events[2:2] = [
            {"kind": "phy", "ps": ps, "ck": ps, "channel": 0, "cs": cs, "ca": access(write, 32, 0, 0)}
            for ps, cs, write in ((1, 1, True), (2, 1, False), (3, 2, False), (4, 2, True))
        ]
        result = self.replay(events)
        self.assertEqual(result["commands_by_channel"], {"0/RD": 2, "0/WR": 2})
        self.assertEqual(result["access_rank_switches_by_channel"], {0: 1})
        self.assertEqual(result["access_direction_changes_by_channel"], {0: 2})

    def test_missing_response_or_end_and_reversed_time_fail(self):
        cases = []
        events = observations()
        cases.append((events[:-1], "missing interval/tail"))
        cases.append(([e for e in events if e.get("ps") != 70], "unsettled source tail"))
        cases.append((events[:10] + [{**events[10], "ps": 1}] + events[11:], "time/order/tail"))
        cases.append((events[:-1] + [{"kind": "reset", "ps": 80}], "unsupported workload event/epoch"))
        cases.append((events[:10] + [{**events[10], "last": 0}] + events[11:], "R length/last"))
        for altered, marker in cases:
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(Violation, marker):
                    self.replay(altered)


if __name__ == "__main__":
    unittest.main()
