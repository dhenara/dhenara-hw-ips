"""Lifecycle/maintenance oracle with one refresh owner and explicit trust.

All times are CK, except elapsed retention which is absolute picoseconds so a
clock change cannot extend a deadline. Conservative policy: no postponed REF,
no low-temperature interval extension, no RFM exemption, paired-bank counters.
"""

from dataclasses import asdict, dataclass, field
from fractions import Fraction

MULTIPLIERS = {
    1: Fraction(8),
    2: Fraction(6),
    3: Fraction(4),
    4: Fraction(33, 10),
    5: Fraction(5, 2),
    6: Fraction(2),
    7: Fraction(17, 10),
    8: Fraction(13, 10),
    9: Fraction(1),
    10: Fraction(7, 10),
    11: Fraction(1, 2),
    12: Fraction(1, 4),
    13: Fraction(1, 4),
    14: Fraction(1, 8),
    15: Fraction(1, 8),
}


@dataclass
class Maintenance:
    mr27: int = 0x45
    mr57: int = 0
    ranks: int = 2
    now: int = 0
    interval: int = 3124
    last_refresh: list[int] = field(init=False)
    raa: list[list[int]] = field(init=False)
    extra: list[bool] = field(init=False)
    thermal: int = 9
    debt: int = 0
    credit: int = 0
    fault: bool = False
    pb_next: list[int] = field(init=False)
    pb_seen: list[set[int]] = field(init=False)
    self_refresh: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.ranks not in (1, 2) or not 0 <= self.mr27 < 256 or not 0 <= self.mr57 < 256:
            raise ValueError("discovery")
        if self.mr27 & 1 and self.imt == 0 or self.mr57 >> 4 or (self.mr57 >> 2 & 3) > 1:
            raise ValueError("RFM capability")
        self.last_refresh = [0] * self.ranks
        self.raa = [[0] * 8 for _ in range(self.ranks)]
        self.extra = [False] * self.ranks
        self.pb_next = [0] * self.ranks
        self.pb_seen = [set() for _ in range(self.ranks)]

    @property
    def imt(self) -> int:
        return (self.mr27 >> 1 & 31) * 8

    @property
    def maximum(self) -> int:
        return (2 + 2 * (self.mr27 >> 6)) * self.imt

    def advance(self, to: int) -> None:
        if to < self.now:
            raise ValueError("time reversal")
        self.now = to
        if any(
            to - last > self.interval for rank, last in enumerate(self.last_refresh) if rank not in self.self_refresh
        ):
            self.fault = True
            raise ValueError("missed periodic refresh")

    def activate(self, rank: int, bank: int) -> None:
        if self.fault or not 0 <= rank < self.ranks or not 0 <= bank < 16:
            raise ValueError("ACT admission")
        pair = bank % 8
        if self.mr27 & 1:
            if self.raa[rank][pair] >= self.maximum:
                raise ValueError("RAAMMT reached")
            self.raa[rank][pair] += 1

    def refresh(self, rank: int, *, extra: bool = False, pair: int | None = None) -> None:
        if not 0 <= rank < self.ranks:
            raise ValueError("rank")
        if pair is not None:
            if pair != self.pb_next[rank]:
                raise ValueError("REFpb pair sequence")
            self.pb_seen[rank].add(pair)
            self.pb_next[rank] = (pair + 1) % 8
            self.raa[rank][pair] = max(0, self.raa[rank][pair] - self.imt)
            # A full eight-pair round discharges one all-bank obligation.
            if len(self.pb_seen[rank]) < 8:
                return
            self.pb_seen[rank].clear()
        else:
            self.raa[rank] = [max(0, n - self.imt) for n in self.raa[rank]]
        if extra:
            if not self.extra[rank]:
                raise ValueError("no extra-refresh obligation")
            self.extra[rank] = False
        else:
            self.last_refresh[rank] = self.now
        self.credit = 0
        self.debt = max(0, max(self.now - last for last in self.last_refresh) - self.interval)

    def rfm(self, rank: int, pair: int | None = None) -> None:
        if not self.mr27 & 1 or not 0 <= rank < self.ranks or pair is not None and not 0 <= pair < 8:
            raise ValueError("RFM")
        decrement = self.imt * (Fraction(1), Fraction(3, 2), Fraction(2), Fraction(4))[self.mr57 & 3]
        for i in range(8) if pair is None else [pair]:
            self.raa[rank][i] = max(0, self.raa[rank][i] - decrement)
        # RFM never advances periodic refresh or clears SRX extra obligations.

    def temperature(self, mr4: int) -> None:
        code = mr4 & 31
        if code not in MULTIPLIERS:
            self.fault = True
            raise ValueError("temperature out of range/reserved")
        self.credit = 0
        self.thermal = code
        self.interval = int(3124 * min(Fraction(1), MULTIPLIERS[code]))
        # No accumulated postponed debt in this policy. An already-overdue
        # thermal transition blocks ACT/data until immediate maintenance.
        self.debt = max(0, max(self.now - last for last in self.last_refresh) - self.interval)

    def service_allowed(self) -> bool:
        return not self.fault and self.debt == 0

    def enter_sr(self, rank: int) -> None:
        if self.extra[rank] or self.debt:
            raise ValueError("extra refresh/thermal debt before SRE")

    def hold_sr(self, rank: int) -> None:
        self.enter_sr(rank)
        self.self_refresh.add(rank)

    def exit_sr(self, rank: int) -> None:
        if not 0 <= rank < self.ranks:
            raise ValueError("rank")
        self.self_refresh.discard(rank)
        self.extra[rank] = True
        self.last_refresh[rank] = self.now

    def timing_derating(self) -> dict[str, int]:
        # Table 406: 1.875ns -> 2 CK, 3.75ns -> 3 CK at 800MHz.
        return {"trcd": 2, "trp_ab": 2, "trp_pb": 2, "tras_min": 2, "trc": 3} if self.thermal in (13, 15) else {}


@dataclass
class Lifecycle:
    state: str = "RESET"
    owner: str = "PHY"
    trusted: bool = False
    profile_epoch: int = 0
    boot_epoch: int = 0
    training_epoch: int | None = None
    retention_deadline_ps: int | None = None
    pending: set[int] = field(default_factory=set)
    write_end: int = 0
    ecc_dirty: bool = False
    wck_disabled: bool = True
    maintenance: Maintenance = field(default_factory=Maintenance)
    transfer_at: int | None = None
    kind: str | None = None
    target_fsp: int | None = None
    maximum: int = 0
    active_fsp: int = 0
    retired_profile_epoch: int = 0
    fault_diagnostics: list[dict] = field(default_factory=list)

    def boot(self, at: int, *, complete: bool, boot_state: str, training_epoch: int, profile_epoch: int) -> None:
        if (
            self.state != "RESET"
            or not complete
            or boot_state not in ("IDLE", "SELF_REFRESH")
            or type(at) is not int
            or at < 0
            or training_epoch != self.boot_epoch
            or type(profile_epoch) is not int
            or not self.retired_profile_epoch < profile_epoch < 1 << 32
            or not self.wck_disabled
        ):
            raise ValueError("boot/training/profile handover")
        self.training_epoch = training_epoch
        self.profile_epoch = profile_epoch
        self.owner = "MC"
        self.trusted = True
        self.state = "QUIESCED"
        self.maintenance.now = at
        self.maintenance.last_refresh = [at] * self.maintenance.ranks
        if boot_state == "SELF_REFRESH":
            for rank in range(self.maintenance.ranks):
                self.maintenance.exit_sr(rank)
        # No refresh debt/credit is imported from PHY boot.
        self.maintenance.debt = 0
        self.maintenance.credit = 0

    def enter_self_refresh(self, at: int) -> None:
        """Acknowledge completed SRE, after bounded entry, for indefinite hold."""
        if (
            self.state != "TRANSFER"
            or self.kind != "SELF_REFRESH"
            or at < self.transfer_at
            or at - self.transfer_at > self.maximum
        ):
            raise ValueError("self-refresh entry")
        self.maintenance.advance(at)
        for rank in range(self.maintenance.ranks):
            self.maintenance.hold_sr(rank)
        self.state = "SELF_REFRESH"
        self.retention_deadline_ps = None

    def exit_self_refresh(self, at: int, txsr: int) -> None:
        if self.state != "SELF_REFRESH" or txsr < 174:
            raise ValueError("self-refresh exit")
        self.maintenance.advance(at)
        self.maintenance.now = at + txsr
        for rank in range(self.maintenance.ranks):
            self.maintenance.exit_sr(rank)
        self.state = "QUIESCED"
        self.kind = None
        self.transfer_at = None

    def enable(self) -> None:
        if (
            self.state != "QUIESCED"
            or self.owner != "MC"
            or not self.trusted
            or not self.profile_epoch
            or not self.maintenance.service_allowed()
        ):
            raise ValueError("not initialized")
        self.state = "RUN"

    def drain(self, at: int) -> bool:
        return not self.pending and not self.ecc_dirty and at >= self.write_end

    def request(self, kind: str, at: int, maximum: int, return_ck: int = 2, target_fsp: int | None = None) -> None:
        if kind not in (
            "CTRL_UPDATE",
            "PHY_UPDATE",
            "PHY_MASTER_IDLE",
            "PHY_MASTER_SR",
            "POWER_DOWN",
            "SELF_REFRESH",
            "DVFS",
        ):
            raise ValueError("unsupported lifecycle operation")
        if self.state not in ("RUN", "QUIESCED") or self.owner != "MC" or not self.drain(at) or not self.wck_disabled:
            raise ValueError("drain/WCK/owner")
        if not self.maintenance.service_allowed():
            raise ValueError("urgent refresh first")
        budget = min(last + self.maintenance.interval - at for last in self.maintenance.last_refresh)
        if maximum + return_ck > budget:
            raise ValueError("refresh admission budget")
        if kind == "SELF_REFRESH":
            for rank in range(self.maintenance.ranks):
                self.maintenance.enter_sr(rank)
        if kind == "DVFS" and target_fsp not in (0, 1, 2):
            raise ValueError("FSP")
        if maximum < 1:
            raise ValueError("maximum pause")
        self.maximum = maximum
        self.target_fsp = target_fsp
        self.transfer_at = at
        self.kind = kind
        self.state = "TRANSFER"
        self.owner = (
            "PHY" if kind in ("CTRL_UPDATE", "PHY_UPDATE", "PHY_MASTER_IDLE", "PHY_MASTER_SR", "DVFS") else "MC"
        )
        self.retention_deadline_ps = (at + budget) * 1250

    def finish(
        self,
        at: int,
        *,
        acknowledged: bool = True,
        ownership_returned: bool = True,
        elapsed_ps: int | None = None,
        profile_epoch: int | None = None,
    ) -> None:
        if self.state != "TRANSFER" or self.transfer_at is None:
            raise ValueError("no transition")
        absolute = at * 1250 if elapsed_ps is None else elapsed_ps
        if (
            at < self.transfer_at
            or at - self.transfer_at > self.maximum
            or not ownership_returned
            or absolute > self.retention_deadline_ps
        ):
            self.fail()
            raise ValueError("ownership/retention lost")
        if not self.wck_disabled:
            self.fail()
            raise ValueError("WCK at ownership return")
        if self.kind == "DVFS" and acknowledged:
            if profile_epoch is None or profile_epoch <= self.profile_epoch:
                raise ValueError("atomic target profile missing")
            self.active_fsp = self.target_fsp
            self.profile_epoch = profile_epoch
        if self.kind in ("SELF_REFRESH", "PHY_MASTER_SR") and acknowledged:
            self.maintenance.now = at
            for rank in range(self.maintenance.ranks):
                self.maintenance.exit_sr(rank)
        self.maintenance.advance(at)
        self.owner = "MC"
        self.state = "QUIESCED"
        self.transfer_at = None
        self.kind = None

    def phy_refresh(self, at: int, rank: int, latency: int) -> None:
        if self.state != "TRANSFER" or self.kind != "PHY_MASTER_IDLE" or latency < 1:
            raise ValueError("PHY refresh owner")
        self.maintenance.advance(at + latency)
        self.maintenance.refresh(rank)
        self.retention_deadline_ps = min(x + self.maintenance.interval for x in self.maintenance.last_refresh) * 1250

    def fail(self) -> None:
        if self.state != "FAULT":
            # Retain evidence, separate from the volatile state that reset
            # must clear. asdict copies nested counters and rank sets.
            self.fault_diagnostics.append(
                {
                    "boot_epoch": self.boot_epoch,
                    "profile_epoch": self.profile_epoch,
                    "training_epoch": self.training_epoch,
                    "state": self.state,
                    "owner": self.owner,
                    "active_fsp": self.active_fsp,
                    "kind": self.kind,
                    "target_fsp": self.target_fsp,
                    "maximum": self.maximum,
                    "transfer_at": self.transfer_at,
                    "retention_deadline_ps": self.retention_deadline_ps,
                    "write_end": self.write_end,
                    "pending": set(self.pending),
                    "ecc_dirty": self.ecc_dirty,
                    "maintenance": asdict(self.maintenance),
                }
            )
        self.state = "FAULT"
        self.trusted = False
        self.owner = "UNKNOWN"

    def recover(self, host_settled: bool, reset_confirmed: bool) -> None:
        if self.state != "FAULT" or not host_settled or not reset_confirmed:
            raise ValueError("destructive recovery prerequisites")
        if self.boot_epoch >= (1 << 32) - 1:
            raise ValueError("boot epoch exhausted; coordinated reset required")
        self.retired_profile_epoch = max(self.retired_profile_epoch, self.profile_epoch)
        self.state = "RESET"
        self.owner = "PHY"
        self.trusted = False
        self.profile_epoch = 0
        self.boot_epoch += 1
        self.training_epoch = None
        self.pending.clear()
        self.write_end = 0
        self.ecc_dirty = False
        self.wck_disabled = True
        self.retention_deadline_ps = None
        self.transfer_at = None
        self.kind = None
        self.target_fsp = None
        self.maximum = 0
        self.active_fsp = 0
        # These capability inputs describe the selected device, not retained
        # refresh state. Fresh boot still attests discovery/MRs/training.
        old = self.maintenance
        self.maintenance = Maintenance(mr27=old.mr27, mr57=old.mr57, ranks=old.ranks)


def check_low_power(response: int, wakeup: int, ack: list[bool], release: int) -> str:
    if response < 1 or wakeup < 1 or release < response or len(ack) <= release + wakeup:
        raise ValueError("LP trace")
    first = next((i for i, x in enumerate(ack) if x), None)
    if first is None:
        if release != response:
            raise ValueError("ignored opportunity release")
        return "declined"
    if not 1 <= first <= response or first >= release or any(not x for x in ack[first:release]):
        raise ValueError("LP acknowledgement")
    returned = next((i for i in range(release, len(ack)) if not ack[i]), None)
    if returned is None or returned > release + wakeup or any(ack[returned:]):
        raise ValueError("LP wake timeout/reassertion")
    return "acknowledged"


def arbitrate_update(ctrl_active: bool, ctrl_released: bool, phy_req: bool, elapsed: int, response_bound: int) -> str:
    if elapsed > response_bound:
        raise ValueError("PHY response deadline")
    if ctrl_active and phy_req:
        return "wait_controller_release"
    if phy_req and ctrl_released:
        return "ack_phy"
    return "idle"


def check_fsp(steps: list[tuple[int, str, int]], target: int) -> None:
    """Independent ordered FSP protocol fixture, constant CK, WCK ratio change.

    All target-bank mode/training images must already be prepared. Fastest
    short tFC is deliberately not used: allow 251.25ns = 201CK and six CK
    running clock on both sides. DVFSC and voltage changes are unsupported.
    """
    if target not in (0, 1, 2) or [op for _, op, _ in steps] != [
        "DRAIN",
        "WS_OFF",
        "DISABLE",
        "FSP_WR",
        "PROGRAM",
        "FSP_OP",
        "CLOCK_STABLE",
        "TRAINED",
        "PUBLISH",
    ]:
        raise ValueError("FSP sequence")
    times = [at for at, _, _ in steps]
    if any(a >= b for a, b in zip(times, times[1:], strict=False)):
        raise ValueError("FSP edge order")
    if times[2] - times[1] < 5 or times[5] - times[4] < 12 or times[6] - times[5] < 201 or times[8] - times[6] < 6:
        raise ValueError("FSP WCK/MRD/tFC clock guards")
    if any(value != target for _, op, value in steps if op in ("FSP_WR", "PROGRAM", "FSP_OP", "TRAINED", "PUBLISH")):
        raise ValueError("FSP target identity")


def check_phy_update(
    request_at: int,
    ack_at: int,
    done_at: int,
    release_at: int,
    *,
    response: int,
    type_bound: int,
    write_end: int,
    ctrl_release: int,
    wck_disabled: bool,
) -> None:
    if not wck_disabled or ack_at < write_end or ack_at <= ctrl_release:
        raise ValueError("update drain/competing owner")
    if (
        not request_at < ack_at <= request_at + response
        or not ack_at < done_at <= ack_at + type_bound
        or release_at <= done_at
    ):
        raise ValueError("PHY update response/duration/release")


def check_master(
    request_at: int,
    ack_at: int,
    done_at: int,
    release_at: int,
    *,
    response: int,
    type_bound: int,
    closed_at: int,
    write_end: int,
    wck_disabled: bool,
    cs_state: tuple[int, ...],
    syscs: tuple[int, ...],
    refreshes: list[tuple[int, int, int]],
    refresh_deadline: int,
    refresh_latency: int,
    state_sel: int,
    chosen_states: tuple[str, ...],
    returned_states: tuple[str, ...],
) -> None:
    if not wck_disabled or ack_at < max(closed_at, write_end):
        raise ValueError("master drain/closed pages/WCK")
    if any(x not in (0, 1) for x in (*cs_state, *syscs)) or len(cs_state) != len(syscs):
        raise ValueError("CS state")
    if (
        not request_at < ack_at <= request_at + response
        or not ack_at < done_at <= ack_at + type_bound
        or release_at <= done_at
    ):
        raise ValueError("master request/type/release")
    # IDLE mode: the MC remains the sole refresh requester. The PHY executes
    # each request within tphymstr_rfsh. Normal ACT/PRE is prohibited in this
    # window. The caller supplies the closed-state attestation, not a timeout.
    if state_sel not in (0, 1) or len(chosen_states) != len(syscs) or returned_states != chosen_states:
        raise ValueError("master returned-state attestation")
    for active, choice, state in zip(syscs, cs_state, chosen_states, strict=True):
        if active and (
            state not in ("IDLE", "SELF_REFRESH") or not choice and state != ("SELF_REFRESH" if state_sel else "IDLE")
        ):
            raise ValueError("master requested rank state")
    deadlines = [refresh_deadline for _ in syscs]
    for request, executed, rank in refreshes:
        if (
            not 0 <= rank < len(syscs)
            or not syscs[rank]
            or chosen_states[rank] != "IDLE"
            or not ack_at <= request <= executed <= done_at
            or executed - request > refresh_latency
        ):
            raise ValueError("PHY refresh execution")
        if executed > deadlines[rank]:
            raise ValueError("PHY refresh retention")
        deadlines[rank] = executed + 3124
    if any(
        active and state == "IDLE" and release_at > deadline
        for active, state, deadline in zip(syscs, chosen_states, deadlines, strict=True)
    ):
        raise ValueError("master missed refresh")
