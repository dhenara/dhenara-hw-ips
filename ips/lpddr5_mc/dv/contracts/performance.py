"""Periodic command witness and independently evaluated resource bounds.

The witness is intentionally static, not an implementation scheduler. The
checker maintains independent bank/CA/data state and a periodic host ledger.
Default: CK=800MHz, two x16 channels, two 128-bit AXI400 ports, ECC off,
64-byte traffic, 64 entries per direction/channel, always-on WCK during traffic.
"""

from collections import Counter, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    ck: int
    op: str
    bank: int = -1
    tag: int = -1
    row: int = 0
    column: int = 0


def witness(periods: int = 4, direction: str = "read", service_div: int = 2) -> list[Command]:
    if direction not in ("read", "write") or service_div not in (1, 2, 4):
        raise ValueError("benchmark")
    result = []
    for period in range(periods):
        base = period * 3000
        result.append(Command(base, "REF"))
        for bank in range(16):
            at = 170 + bank * 4 if bank < 8 else 202 if bank == 8 else 212 + (bank - 9) * 8
            result.extend(
                [
                    Command(base + at, "ACT1", bank, row=period % 32768),
                    Command(base + at + 1, "ACT2", bank, row=period % 32768),
                ]
            )
        result.append(Command(base + (169 if service_div == 4 else 185), "SYNC"))
        times = [208 + i * 8 + bg * 2 for i in range(40) for bg in range(2)] + [528 + i * 8 for i in range(295)]
        # Reduced selector at CK/4 cannot start both accesses two CK apart.
        # It uses one command every eight CK after the first access, with
        # backpressure at admission: its target is explicitly 90% of AXI400.
        if service_div == 4:
            times = [188, 196] + list(range(208, 2897, 8))
        counts = Counter()
        for i, at in enumerate(times):
            bank = (i // 2 % 4) + (i % 2) * 4 if i < 80 and service_div != 4 else i % 16
            column = counts[bank] * 64
            counts[bank] += 1
            result.append(Command(base + at, direction, bank, period * 375 + i, period % 32768, column))
        result.extend(
            [
                Command(base + 2922, "OFF"),
                Command(base + 2928, "ZQ_LATCH"),
                Command(base + 2956, "SYNC"),
                Command(base + 2960, "MR4"),
                Command(base + 2984, "OFF"),
                Command(base + 2954, "PRE"),
            ]
        )
    return sorted(result, key=lambda c: c.ck)


def check(commands: list[Command], periods: int, direction: str, service_div: int = 2) -> dict:
    if periods < 2 or direction not in ("read", "write") or service_div not in (1, 2, 4):
        raise ValueError("benchmark length/direction")
    opened = {}
    act1 = None
    activated = []
    last_bg = {}
    data = set()
    cas = set()
    ids = set()
    last_ref = -3000
    last_pre = -100
    blocked_until = -1
    sync = False
    last_end = -1
    last_write_end = -1
    payload = Counter()
    bursts = []
    bybank = Counter()
    raa = [0] * 8
    ref_times = set()
    mr_times = set()
    zq_times = set()
    sync_at = -100
    mr_at = -100
    after_mrr = False
    if any(a.ck >= b.ck for a, b in zip(commands, commands[1:], strict=False)):
        raise ValueError("command order/collision")
    for c in commands:
        if c.ck in cas:
            raise ValueError("CA collision")
        cas.add(c.ck)
        if c.ck < blocked_until:
            raise ValueError("command during reserved maintenance/recovery")
        if act1 and (c.op != "ACT2" or c.bank != act1.bank or not 1 <= c.ck - act1.ck <= 8):
            raise ValueError("ACT pair deadline/intervening command")
        if c.op == "REF":
            if opened or c.ck - last_pre < 17 or c.ck - last_ref > 3124:
                raise ValueError("REF deadline/precharge")
            ref_times.add(c.ck)
            last_ref = c.ck
            blocked_until = c.ck + 168
            raa = [max(0, n - 16) for n in raa]
        elif c.op == "ACT1":
            if c.bank in opened or c.ck - last_pre < 17:
                raise ValueError("ACT precharge")
            act1 = c
        elif c.op == "ACT2":
            if act1 is None or c.row != act1.row:
                raise ValueError("ACT2 without identity")
            if activated and c.ck - activated[-1] < 4 or len(activated) >= 4 and c.ck - activated[-4] < 16:
                raise ValueError("tRRD/tFAW")
            activated.append(c.ck)
            opened[c.bank] = (c.row, c.ck)
            act1 = None
            raa[c.bank % 8] += 1
            if raa[c.bank % 8] > 64:
                raise ValueError("mandatory RFM counter")
        elif c.op == "SYNC":
            if sync:
                raise ValueError("duplicate WCK sync")
            sync_at = c.ck
            sync = True
            after_mrr = False
        elif c.op == "MR4":
            if not sync or c.ck - sync_at < 3:
                raise ValueError("MRR fast synchronization")
            # JESD209-5C 7.6.1: BL16, opcode repeated over first eight UI.
            # Table 214 note 4 reserves four CK in BG mode even though
            # only sixteen UI carry the MRR burst. Conservatively reserve
            # the entire effective window, including undefined values.
            first = (c.ck + 17) * 8 + 6
            for beat in range(32):
                if first + beat in data:
                    raise ValueError("MRR pin collision")
                data.add(first + beat)
            last_end = max(last_end, first + 32 + 5)
            mr_times.add(c.ck)
            mr_at = c.ck
            after_mrr = True
        elif c.op == direction:
            if after_mrr:
                raise ValueError("MRR requires stop and fresh synchronization before host traffic")
            if not sync or c.bank not in opened or c.row != opened[c.bank][0] or c.ck - opened[c.bank][1] < 15:
                raise ValueError("read/write ACT/WCK state")
            if direction == "write" and c.ck - mr_at < 16:
                raise ValueError("MRR to write turnaround")
            if c.column % 64 or not 0 <= c.column <= 2048 - 64:
                raise ValueError("row column wrap")
            bg = c.bank // 4
            if bg in last_bg and c.ck - last_bg[bg] < 8:
                raise ValueError("same BG tCCD")
            if c.tag in ids or c.ck % service_div:
                raise ValueError("duplicate tag/selector slot")
            ids.add(c.tag)
            last_bg[bg] = c.ck
            bybank[(c.ck // 3000, c.bank)] += 1
            latency = 17 if direction == "read" else 9
            offset = 6 if direction == "read" else 2
            first = (c.ck + latency) * 8 + offset
            for beat in range(32):
                at = first + beat + (16 if beat >= 16 else 0)
                if at in data:
                    raise ValueError("split BL32 pin collision")
                data.add(at)
            last_end = max(last_end, first + 48 + 5)
            if direction == "write":
                last_write_end = first + 48
            payload[c.ck // 3000] += 64
            bursts.append((c.ck, (first + 48 + 7) // 8))
        elif c.op == "OFF":
            if not sync or c.ck * 8 < last_end:
                raise ValueError("OFF before final postamble")
            sync = False
            blocked_until = c.ck + 5
        elif c.op == "PRE":
            if (
                any(c.ck - at < 34 for _, at in opened.values())
                or c.ck * 8 < last_end + 4 * 8
                or direction == "write"
                and c.ck * 8 < last_write_end + 29 * 8
            ):
                raise ValueError("PRE tRAS/data recovery")
            opened.clear()
            last_pre = c.ck
        elif c.op == "ZQ_LATCH":
            if c.ck * 8 < last_end or c.ck < last_pre:
                raise ValueError("ZQ with pending DQ")
            zq_times.add(c.ck)
            blocked_until = c.ck + 24
        else:
            raise ValueError("unknown command")
    if opened or act1 or sync or set(payload) != set(range(periods)) or last_ref != (periods - 1) * 3000:
        raise ValueError("incomplete periodic state")
    if (
        ref_times != {i * 3000 for i in range(periods)}
        or mr_times != {i * 3000 + 2960 for i in range(periods)}
        or zq_times != {i * 3000 + 2928 for i in range(periods)}
    ):
        raise ValueError("missing periodic REF/thermal/ZQ obligation")
    expected = 375 if service_div != 4 else 339
    if any(payload[i] != expected * 64 for i in range(periods)):
        raise ValueError("periodic payload deficit")
    # Host admission is periodic, after a three-CK asynchronous crossing. Full
    # rate arrives one 64B request per 8CK/channel. Initial 20-entry backlog is
    # reproduced every period; no finite-preload throughput proof is accepted.
    queue = 20
    max_queue = queue
    min_queue = queue
    active = deque()
    reserved = deque()
    returned = deque()
    max_live = queue
    max_return = 0
    retired = 0
    next_response = 0
    in_service = None
    index = 0
    queue_at = []
    arrivals = 375 if service_div != 4 else expected
    arrival_phases = {3 + (i * 3000 // arrivals) for i in range(arrivals)}
    retired_period = Counter()
    live_at = []
    for ck in range(periods * 3000):
        if ck % 3000 in arrival_phases:
            queue += 1
        while index < len(bursts) and bursts[index][0] - 3 == ck:
            if queue == 0:
                raise ValueError("command without arrived host data")
            queue -= 1
            # CA times in the witness are DRAM pin times. Select one CK
            # before DFI issue, which precedes the pin by the two-CK PHY
            # command path. Read return also traverses the two-CK PHY path.
            reserved.append((bursts[index][0] - 2, bursts[index][1] + 3 + (2 if direction == "read" else 0)))
            index += 1
        while reserved and reserved[0][0] <= ck:
            _, completion = reserved.popleft()
            active.append(completion)
        while active and active[0] <= ck:
            active.popleft()
            returned.append(ck)
        if in_service is not None and ck >= in_service:
            retired += 1
            retired_period[ck // 3000] += 1
            in_service = None
        if returned and ck >= next_response:
            returned.popleft()
            # Four 128-bit R beats at AXI400: last beat is six CK
            # after first. Keep the output slot until that handshake.
            if direction == "read":
                in_service = ck + 6
            else:
                retired += 1
                retired_period[ck // 3000] += 1
            next_response = ck + 8
        max_queue = max(max_queue, queue)
        min_queue = min(min_queue, queue)
        max_return = max(max_return, len(returned) + int(in_service is not None))
        max_live = max(
            max_live,
            queue + len(reserved) + len(active) + len(returned) + int(in_service is not None),
        )
        if ck % 3000 == 2999:
            queue_at.append(queue)
            live_at.append(queue + len(reserved) + len(active) + len(returned) + int(in_service is not None))
    if len(set(queue_at)) != 1 or queue_at[0] != 20:
        raise ValueError("backlog grows between periods")
    if any(retired_period[i] != expected for i in range(1, periods)) or len(set(live_at[1:])) != 1:
        raise ValueError("completed payload/backlog not periodic")
    if max_live > 64:
        raise ValueError("outstanding/buffer capacity exceeded")
    # Periodic return tail crosses measurement boundaries. Check steady state
    # completed payload in integral periods after one warm-up, not final drains.
    # Each independent channel supplies 8 bytes/CK; two channels at 800MHz
    # give 12.8GB/s. Counts below are per channel.
    return {
        "period_ck": 3000,
        "periods": periods,
        "payload_per_channel_per_period": expected * 64,
        "combined_gb_s": expected * 64 * 2 * 800 / 3000 / 1000,
        "queue_min": min_queue,
        "queue_max": max_queue,
        "outstanding_max": max_live,
        "return_buffer_max": max_return,
        "periodic_backlog": queue_at,
        "retired_bursts": retired,
        "steady_retired_per_period": [retired_period[i] for i in range(1, periods)],
        "maximum_bursts_per_bank_row": max(bybank.values()),
        "feedback_ck": 1,
        "phy_command_ck": 2,
        "phy_read_ck": 2,
        "maintenance": "REFab, MR4 polling, periodic background ZQ latch; mandatory RFM counters enabled",
        "host_cdc_ck": 3,
    }
