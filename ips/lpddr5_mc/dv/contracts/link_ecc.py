"""Independent, matrix-driven LPDDR5 link-ECC reference functions.

The matrix is admitted reference data, not an RTL-generated polynomial.
Index zero is beat zero/DQ zero (or DMI beat zero). Check bits follow payload.
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Decode:
    payload: int
    status: str
    syndrome: int
    corrected_column: int | None


def syndrome(rows: Sequence[str], word: int) -> int:
    if word < 0 or word >= 1 << len(rows[0]):
        raise ValueError("word width")
    result = 0
    for i, row in enumerate(rows):
        mask = sum(int(value) << bit for bit, value in enumerate(row))
        result |= ((word & mask).bit_count() & 1) << i
    return result


def encode(rows: Sequence[str], payload: int) -> int:
    width = len(rows[0]) - len(rows)
    if payload < 0 or payload >= 1 << width:
        raise ValueError("payload width")
    lower = syndrome(rows, payload) & ((1 << (len(rows) - 1)) - 1)
    intermediate = payload | (lower << width)
    parity = (syndrome(rows, intermediate) >> (len(rows) - 1)) & 1
    return lower | (parity << (len(rows) - 1))


def decode(rows: Sequence[str], word: int) -> Decode:
    width = len(rows[0]) - len(rows)
    value = syndrome(rows, word)
    if value == 0:
        return Decode(word & ((1 << width) - 1), "clean", 0, None)
    if value & (1 << (len(rows) - 1)):
        for column in range(len(rows[0])):
            expected = sum(int(row[column]) << i for i, row in enumerate(rows))
            if expected == value:
                word ^= 1 << column
                return Decode(word & ((1 << width) - 1), "corrected", value, column)
    return Decode(word & ((1 << width) - 1), "uncorrectable", value, None)


def write_parity(data_checks: int, dmi_checks: int) -> tuple[int, ...]:
    if not 0 <= data_checks < 512 or not 0 <= dmi_checks < 64:
        raise ValueError("check width")
    return (0,) + tuple((dmi_checks >> i) & 1 for i in range(6)) + tuple((data_checks >> i) & 1 for i in range(9))


def read_parity(data_checks: int) -> tuple[int, ...]:
    if not 0 <= data_checks < 512:
        raise ValueError("check width")
    return (0,) * 7 + tuple((data_checks >> i) & 1 for i in range(9))
