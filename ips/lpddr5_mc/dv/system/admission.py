"""Observe preacceptance range ownership without choosing a CDC algorithm.

A grant precedes a source handshake and carries a boot epoch. Revocation is
acknowledged in the source domain before an overlapping grant can be recycled.
An accepted reservation remains until its real hazard/response owner releases
it. Disjoint ranges may be granted concurrently. This is a monitor seam, not a
proof that a particular asynchronous implementation meets the contract.
"""

from dataclasses import dataclass

from system.host import Address, byte_ranges, require


@dataclass
class Reservation:
    port: int
    addresses: set[int]
    epoch: int
    accepted: bool = False
    revoking: bool = False


class AdmissionMonitor:
    def __init__(self, bus_bytes: int = 16, credits: int = 64):
        self.bus_bytes, self.credits = bus_bytes, credits
        self.boot = 0
        self.live: dict[int, Reservation] = {}
        self.last_ps = -1

    def grant(self, key: int, a: Address, epoch: int) -> None:
        addresses = {b for beat in byte_ranges(a, self.bus_bytes) for b in beat}
        require(
            key not in self.live and epoch == self.boot and len(self.live) < self.credits, "grant identity/credit/epoch"
        )
        require(not any(r.addresses & addresses for r in self.live.values()), "conflicting preacceptance grant")
        self.live[key] = Reservation(a.port, addresses, epoch)

    def accept(self, key: int, port: int, epoch: int, ps: int) -> None:
        require(ps >= self.last_ps, "source acceptance time reversal")
        require(key in self.live, "source acceptance without grant")
        r = self.live[key]
        require((port, epoch) == (r.port, self.boot) and not r.accepted, "stale/duplicate source acceptance")
        # A revocation request in flight does not retroactively cancel a source
        # handshake. The acknowledged answer must say whether it accepted.
        r.accepted = True
        self.last_ps = ps

    def revoke(self, key: int) -> None:
        require(key in self.live and not self.live[key].accepted, "revoke accepted hazard")
        self.live[key].revoking = True

    def revoked(self, key: int, *, source_accepted: bool) -> None:
        require(key in self.live and self.live[key].revoking, "unsolicited revocation ACK")
        r = self.live[key]
        require(source_accepted == r.accepted, "revocation lost concurrent acceptance")
        if not r.accepted:
            del self.live[key]
        else:
            r.revoking = False

    def release(self, key: int, *, hazard_settled: bool) -> None:
        require(key in self.live and self.live[key].accepted and hazard_settled, "premature accepted hazard release")
        del self.live[key]

    def reset(self, *, source_fenced: bool, host_settled: bool) -> None:
        require(source_fenced and host_settled and self.boot < (1 << 32) - 1, "reset before source fence/settlement")
        self.boot += 1
        self.live.clear()
