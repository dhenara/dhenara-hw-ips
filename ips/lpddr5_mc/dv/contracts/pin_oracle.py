"""Standard-derived event constraints; no dependency on the DFI generator.

The rows below were transcribed from JESD209-5C Tables 204, 205, 208, 209.
They cover only the named operating points, full x16, no DBI/data copy/DVFSC.
These boundary checks do not qualify the complete WCK waveform or DRAM model.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class PinEvents(Protocol):
    cas: int
    command: int
    enable: int
    slow_toggle: int
    fast_toggle: int
    first_data: int
    data_end: int
    stop: int
    disable: int


@dataclass(frozen=True)
class Row:
    ratio: int
    rl: int
    wl: int
    en_read: int
    en_write: int
    en_fast: int
    static: int
    toggle_read: int
    toggle_write: int


ROWS = {
    "lp5_6400_bg_no_link": Row(4, 17, 9, 7, 4, 2, 4, 7, 2),
    "lp5_6400_bg_read_link": Row(4, 19, 9, 9, 4, 2, 4, 7, 2),
    "lp5_3200_16b_no_link": Row(2, 18, 10, 5, 3, 2, 4, 10, 4),
}


def check_paired(name: str, direction: str, beats: int, event: PinEvents) -> None:
    row = ROWS[name]
    ck = row.ratio * 2
    en = row.en_read if direction == "read" else row.en_write
    latency = row.rl if direction == "read" else row.wl
    toggle = row.toggle_read if direction == "read" else row.toggle_write
    expected = {
        "command": event.cas + ck,
        "enable": event.cas + en * ck,
        "slow_toggle": event.cas + (en + row.static) * ck,
        "fast_toggle": event.cas + (en + row.static + 1) * ck,
        "first_data": event.cas + (1 + latency) * ck,
    }
    for field, value in expected.items():
        if getattr(event, field) != value:
            raise ValueError(f"{field}: pin boundary differs from selected JEDEC row")
    if event.first_data - event.slow_toggle != toggle * ck:
        raise ValueError("wrong preamble toggle duration")
    span = beats + (16 if row.ratio == 4 and beats == 32 else 0)
    if event.data_end != event.first_data + span:
        raise ValueError("split-burst completion boundary")
    # MR10 OP[3:2]=00 specifies 2.5 WCK. Retain the half-WCK bound.
    if event.stop - event.data_end < 5:
        raise ValueError("WCK postamble too short")
    if event.disable < event.stop:
        raise ValueError("WCK disabled before postamble")


def check_edges(name: str, event: PinEvents, edges: Sequence[int]) -> None:
    """Check the selected ideal trained phase, including missing/extra edges."""
    row = ROWS[name]
    if event.fast_toggle - event.slow_toggle != row.ratio * 2:
        raise ValueError("half-rate pattern must last one CK")
    cursor = event.slow_toggle
    for at in edges:
        if at != cursor:
            raise ValueError("unexpected WCK transition")
        cursor += 2 if at < event.fast_toggle else 1
    if cursor != event.stop:
        raise ValueError("missing WCK transitions before postamble completion")
