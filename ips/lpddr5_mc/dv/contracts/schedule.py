"""Finite row-hit schedule and an independent pin-occupancy check.

Only same-direction, pre-opened rows, no maintenance, BG4 BL32 is modeled.
No controller scheduler, bank timing engine or throughput RTL is present.
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Access:
    ck: int
    tag: int
    channel: int
    bank_group: int


def reference_stream(count: int, spacing_ck: int = 8, channels: int = 2) -> list[Access]:
    return [Access(32 + i * spacing_ck, i, ch, 0) for i in range(count) for ch in range(channels)]


def interleaved_stream(count: int, channels: int = 2) -> list[Access]:
    return [
        Access(32 + i * 8 + bg * 2, i * 2 + bg, ch, bg)
        for i in range(count)
        for bg in range(2)
        for ch in range(channels)
    ]


def check_bg32(accesses: Sequence[Access], latency_ck: int = 17) -> int:
    """Return payload bytes after checking every pin half-WCK data interval."""
    occupied: set[tuple[int, int]] = set()
    previous: dict[tuple[int, int], int] = {}
    identities: set[tuple[int, int]] = set()
    for access in accesses:
        key = (access.channel, access.bank_group)
        if key in previous and access.ck - previous[key] < 8:
            raise ValueError("same-BG BL32 command spacing below 8 CK")
        previous[key] = access.ck
        identity = (access.channel, access.tag)
        if identity in identities:
            raise ValueError("duplicate burst identity")
        identities.add(identity)
        # Two 16-beat halves. Each beat is one half-WCK, eight per CK.
        first = (access.ck + latency_ck) * 8
        for beat in range(32):
            at = first + beat + (16 if beat >= 16 else 0)
            resource = (access.channel, at)
            if resource in occupied:
                raise ValueError("pin data collision")
            occupied.add(resource)
    return len(accesses) * 64
