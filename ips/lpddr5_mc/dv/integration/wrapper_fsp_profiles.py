"""Persistent constant-CK profile handover owner for the wrapper FSP adapter.

Consumes sampled DFI histories, decoded CA and actual publication handshakes.
Each channel keeps the same PhysicalStream across successful profile changes.
Execution and qualification require a real producer stream at a recorded source.
"""

import copy

from contracts.dvfs import check_dvfs
from contracts.profiles import admit
from system.dram import decode
from system.host import require

BANKED_MRS = (1, 2, 3, 10, 11, 12, 14, 15, 17, 18, 19, 20, 22, 24, 30, 41, 58)


def expanded_mrs(profile):
    """Match vectors.py's explicit zeroed typed profile, then canonical MRs."""
    admit(profile)
    words = [0] * 128
    for key, value in profile["mr"].items():
        words[int(key)] = value
    return words


def expected_program(current, target, old_fsp, target_fsp):
    require(
        old_fsp in range(3) and target_fsp in range(3) and old_fsp != target_fsp,
        "unsupported FSP selection",
    )
    old_mr, target_mr = expanded_mrs(current), expanded_mrs(target)
    return (
        [(16, (old_mr[16] & 0xF0) | old_fsp << 2 | target_fsp)]
        + [(address, target_mr[address]) for address in BANKED_MRS]
        + [(16, (target_mr[16] & 0xF0) | target_fsp << 2 | target_fsp | 64)]
        + [(16, (target_mr[16] & 0xF0) | target_fsp << 2 | target_fsp)]
    )


def decoded_program(samples):
    """Decode actual rank0 CA in the accepted constant-CK FSP interval.

    Timing, row recovery, data, WCK and maintenance remain checked by the same
    PhysicalStream. This projection adds exact twenty-pair target identity.
    """
    result, pending, last = [], None, None
    for e in samples:
        tick = e["tick"]
        require(last is None or tick == last + 1, "missing FSP pin sample")
        last = tick
        if not e["cs"]:
            require(pending is None, "missing FSP MRW2")
            continue
        require(e["cs"] == 1, "FSP fixture rank selection")
        command = decode(e["ca"])
        if pending is None:
            require(command.kind == "MRW1", "non-MRW command during accepted FSP")
            pending = tick, command.operand
        else:
            require(tick == pending[0] + 1 and command.kind == "MRW2", "broken FSP MRW pair")
            result.append((pending[1], command.operand))
            pending = None
    require(pending is None, "missing FSP MRW2 tail")
    return result


class ProfilePublication:
    """Real all-channel publication coordinates persistent physical owners.

    A channel profile can change after all prepare ACKs and its actual commit
    handshake. Host service additionally waits for every channel commit and
    the real final APB/host image epoch. A mixed accept/decline is a fault.
    """

    def __init__(self, profiles, physical, *, boot, epoch, active_fsp=0):
        require(len(profiles) == len(physical) == 2, "two-channel FSP fixture binding")
        require(all(p.p == q for p, q in zip(physical, profiles, strict=True)), "unbound persistent physical profile")
        require(physical[0] is not physical[1], "channels share a physical owner")
        self.profiles = copy.deepcopy(profiles)
        self.physical = tuple(physical)
        self.boot, self.epoch, self.active_fsp = boot, epoch, active_fsp
        self.last_sequence = epoch
        self.transaction = None

    def prepare(self, *, sequence, boot, target_fsp, targets, source_settled):
        require(
            self.transaction is None and boot == self.boot and sequence > self.last_sequence,
            "stale/overlapping FSP prepare",
        )
        require(
            source_settled and all(p.drained() for p in self.physical),
            "FSP publication before independent drain",
        )
        require(
            len(targets) == 2 and target_fsp in range(3) and target_fsp != self.active_fsp,
            "FSP target binding",
        )
        for current, target in zip(self.profiles, targets, strict=True):
            admit(target)
            require(
                current["ck_ps"] == target["ck_ps"] and current["ranks"] == target["ranks"] == 1,
                "FSP fixture supports constant CK and rank1",
            )
            require(
                current["device"] == target["device"],
                "FSP changed physical device descriptor",
            )
            require(
                not any(target[k] for k in ("read_link", "write_link", "read_dbi")),
                "FSP physical adapter unsupported integrity mode",
            )
        self.last_sequence = sequence
        self.transaction = {
            "sequence": sequence,
            "target_fsp": target_fsp,
            "targets": copy.deepcopy(targets),
            "ready": {},
            "committed": set(),
        }

    def returned(self, channel, trace, pin_samples, *, sequence, boot):
        t = self.transaction
        require(
            t is not None and channel in (0, 1) and channel not in t["ready"],
            "unexpected/duplicate FSP return",
        )
        require(sequence == t["sequence"] and boot == self.boot, "stale FSP return identity")
        p, q = self.profiles[channel], t["targets"][channel]
        result = check_dvfs(
            p["management_ck"]["tinit_start"],
            p["management_ck"]["tinit_complete"],
            trace,
            drained=True,
        )
        require(
            result.disposition in ("acknowledged", "declined"),
            f"FSP handshake failed: {result.disposition}",
        )
        require(
            (trace[0].frequency, trace[0].ratio, trace[0].fsp)
            == (0 if q["ratio"] == 2 else 1, q["ratio"], t["target_fsp"]),
            "FSP returned a different target",
        )
        require(
            len(pin_samples) == len(trace) and pin_samples,
            "FSP control/pin coverage mismatch",
        )
        origin = pin_samples[0]["tick"]
        require(
            [e["tick"] for e in pin_samples] == list(range(origin, origin + len(trace))),
            "FSP control/pin phase gap",
        )
        require(
            self.physical[channel].ck == pin_samples[-1]["tick"] + 1,
            "FSP return detached from persistent physical owner",
        )
        program = decoded_program(pin_samples)
        if result.activate_target:
            require(
                program == expected_program(p, q, self.active_fsp, t["target_fsp"]),
                "FSP pin program differs from frozen target",
            )
            selected = [e for e in pin_samples if e["cs"]]
            require(
                selected[0]["tick"] - origin >= p["management_ck"]["tinit_start"],
                "FSP programming before sampled acceptance",
            )
            restore_end = selected[-1]["tick"] - origin + (100000 + q["ck_ps"] - 1) // q["ck_ps"]
            restore_end += max(p["management_ck"]["tctrl_delay"], q["management_ck"]["tctrl_delay"])
            require(
                not any(e.clock_change_done for e in trace[:restore_end]),
                "FSP clock-done before independent normal-restore pin tail",
            )
        else:
            require(not program, "declined FSP emitted programming")
        t["ready"][channel] = result.disposition
        return result.disposition

    def commit(self, channel, *, sequence, boot):
        t = self.transaction
        require(
            t is not None and sequence == t["sequence"] and boot == self.boot,
            "stale FSP commit identity",
        )
        require(
            t["ready"] == {0: "acknowledged", 1: "acknowledged"},
            "partial/declined FSP prepare cannot commit",
        )
        require(channel in (0, 1) and channel not in t["committed"], "duplicate FSP commit")
        # profile() retains memory/MRs/maintenance/row timing and rejects
        # unclosed rows, live data/returns/WCK and normal-VRCG recovery tails.
        self.physical[channel].profile(t["targets"][channel])
        t["committed"].add(channel)

    def published(self, *, sequence, boot, host_epoch, channel_epochs):
        t = self.transaction
        require(
            t is not None and sequence == t["sequence"] and boot == self.boot,
            "stale final publication",
        )
        require(
            t["committed"] == {0, 1} and host_epoch == sequence and channel_epochs == [sequence, sequence],
            "host image before atomic all-channel publication",
        )
        self.epoch, self.active_fsp = sequence, t["target_fsp"]
        self.profiles = t["targets"]
        self.transaction = None

    def declined(self, *, published_epoch, channel_epochs, prepared_valid):
        t = self.transaction
        require(
            t is not None and t["ready"] == {0: "declined", 1: "declined"},
            "mixed FSP outcome requires fault/recovery",
        )
        require(
            not t["committed"]
            and not any(prepared_valid)
            and published_epoch == self.epoch
            and channel_epochs == [self.epoch, self.epoch],
            "decline changed epoch or retained frozen target",
        )
        self.transaction = None

    def service(self, *, host_epoch, channel_epochs):
        require(
            self.transaction is None and host_epoch == self.epoch and channel_epochs == [self.epoch, self.epoch],
            "host service before current common publication",
        )
