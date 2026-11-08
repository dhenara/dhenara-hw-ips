"""Generate a fixture encoder from independently encoded one-bit messages.

The fixture never imports the RTL codec. The linear basis comes from the
admitted JEDEC matrix and the Python oracle used for captured-wire checks.
"""

import json
from pathlib import Path

from contracts.link_ecc import encode


def generate_read_encoder(destination: Path) -> None:
    ip = Path(__file__).resolve().parents[2]
    rows = json.loads((ip / "config/reference/link_ecc_matrix.json").read_text())["data_rows"]
    basis = [encode(rows, 1 << bit) for bit in range(128)]
    lines = [
        "// Independent JEDEC-matrix basis; fixture only.",
        "function automatic logic [8:0] fixture_read_checks(input logic [127:0] payload);",
        "  logic [8:0] result;",
        "  result=0;",
    ]
    lines.extend(f"  if(payload[{bit}]) result ^= 9'h{value:03x};" for bit, value in enumerate(basis))
    lines.extend(["  return result;", "endfunction"])
    (destination / "read_link_encoder.svh").write_text("\n".join(lines) + "\n")
