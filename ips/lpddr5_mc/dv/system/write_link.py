"""Raw write-link parity -> corrected wire -> mask/DBI -> memory byte enables.

Decode complete 16-beat byte-lane groups before observing any memory effect.
Both DQ and DMI have their own codeword; a masked zero is distinguished from
an enabled zero. Canonical JEDEC matrices are data, never DUT algorithms.
"""

from contracts.integrity import WireBeat, receive_byte
from contracts.link_ecc import decode

from system.host import require


def receive(rows: dict, wire: list[WireBeat], *, dbi: bool, masked: bool) -> tuple[bytes, int, list[str]]:
    require(len(wire) in (32, 64), "write parity burst length")
    beats = len(wire) // 2
    require(len({x.tag for x in wire}) == 1, "write parity tag")
    lookup = {(x.beat, x.byte): x for x in wire}
    require(
        len(lookup) == len(wire) and set(lookup) == {(i, j) for i in range(beats) for j in range(2)},
        "write parity identity",
    )
    output, enables, statuses = bytearray(len(wire)), 0, []
    for group in range(beats // 16):
        for lane in range(2):
            block = [lookup[group * 16 + i, lane] for i in range(16)]
            require(all(0 <= x.dq < 256 and x.dmi in (0, 1) and x.parity in (0, 1) for x in block), "raw write width")
            require(block[0].parity == 0, "reserved write parity")
            dq = sum(x.dq << (8 * i) for i, x in enumerate(block))
            dm = sum(x.dmi << i for i, x in enumerate(block))
            data = decode(rows["data_rows"], dq | sum(block[7 + i].parity << (128 + i) for i in range(9)))
            masks = decode(rows["dmi_rows"], dm | sum(block[1 + i].parity << (16 + i) for i in range(6)))
            statuses.extend((data.status, masks.status))
            require("uncorrectable" not in statuses, "uncorrectable physical write link")
            for i in range(16):
                value = receive_byte(data.payload >> (8 * i) & 255, masks.payload >> i & 1, dbi, masked)
                offset = (group * 16 + i) * 2 + lane
                if value is not None:
                    output[offset] = value
                    enables |= 1 << offset
    return bytes(output), enables, statuses
