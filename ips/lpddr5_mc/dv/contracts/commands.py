"""One-CK 14-bit CA representation from JEDEC Table 201 / DFI Table 7.

Fields are logical operands, not a scheduler. Column is the six-bit 32-byte
index inside a 2KB row; WR32 requires its low bit zero. AP is disabled for the
periodic witness but represented for dependent implementation.
"""

from contracts.configuration import uint


def pack(rising: int, falling: int) -> int:
    return uint(rising, 7, "rising CA") | (uint(falling, 7, "falling CA") << 7)


def access(write: bool, beats: int, bank: int, column: int, ap: bool = False, masked: bool = False) -> int:
    uint(bank, 4, "bank")
    uint(column, 6, "column")
    if beats not in (16, 32) or write and beats == 32 and column & 1 or masked and (not write or beats != 16):
        raise ValueError("burst column")
    rise = 2 if masked else (6 if write else 1) if beats == 16 else (4 if write else 5)
    rise |= (column & 1) << 3 | (column >> 3) << 4
    fall = bank | ((column >> 1) & 3) << 4 | int(ap) << 6
    return pack(rise, fall)


def activate(row: int, bank: int) -> tuple[int, int]:
    uint(row, 18, "row")
    uint(bank, 4, "bank")
    return pack(7 | (row >> 14) << 3, bank | ((row >> 11) & 7) << 4), pack(3 | ((row >> 7) & 15) << 3, row & 127)


def cas(kind: str) -> int:
    ws = {"read": 2, "write": 1, "fast": 4, "off": 7}
    if kind not in ws:
        raise ValueError("CAS sync type")
    return pack(12 | ws[kind] << 4, 0)


def maintenance(kind: str, bank: int = 0, all_banks: bool = True) -> int:
    uint(bank, 4, "bank")
    if kind not in ("PRE", "REF", "RFM"):
        raise ValueError("maintenance opcode")
    if kind != "PRE" and bank > 7:
        raise ValueError("refresh bank-pair operand")
    return pack(120 if kind == "PRE" else 56, bank | int(kind == "RFM") << 3 | int(all_banks) << 6)


def mode_write(address: int, value: int) -> tuple[int, int]:
    uint(address, 7, "MR address")
    uint(value, 8, "MR data")
    return pack(88, address), pack(8 | (value >> 7) << 6, value & 127)


def mode_read(address: int) -> int:
    return pack(24, uint(address, 7, "MR address"))


def check_masked_sequence(commands: list[tuple[int, int]], ratio: int) -> None:
    """MWR16-only spacing from Table 231, BG ratio4 or 16B ratio2."""
    if ratio not in (2, 4):
        raise ValueError("masked mode")
    previous = []
    for at, bank in commands:
        uint(bank, 4, "bank")
        for earlier, target in previous:
            minimum = 16 if bank == target else 4 if ratio == 2 or bank // 4 == target // 4 else 2
            if at - earlier < minimum:
                raise ValueError("tCCDMW")
        previous.append((at, bank))
