"""Bounded adapters for canonical packed DFI and sampled APB observations.

DFI snapshots are field values from lpddr5_mc_return4_t, not a flattened
implementation struct with guessed offsets. Byte slices interleave inside
32-bit words. The caller supplies an independent enable/completion oracle.
"""

from contracts.configuration import RegisterBank
from contracts.read_completion import ReadBusOracle

from system.host import require


def returned_snapshot(ratio: int, *, data: int, valid: int, dnv: int, link_parity: int) -> dict:
    require(ratio in (2, 4), "DFI ratio")
    require(0 <= data < 1 << 128 and 0 <= valid < 256, "DFI data/valid width")
    require(0 <= dnv < 65536 and 0 <= link_parity < 65536, "DFI DNV/parity width")
    require(not (valid >> (ratio * 2)), "inactive ratio-two lanes asserted")
    lanes, unavailable, parity = [], [], []
    for word in range(ratio):
        values, missing, raw = [], [], []
        for lane in range(2):
            value = sum((data >> (word * 32 + half * 16 + lane * 8) & 255) << (half * 8) for half in range(2))
            values.append(value if valid >> (word * 2 + lane) & 1 else None)
            missing.append(sum((dnv >> (word * 4 + half * 2 + lane) & 1) << half for half in range(2)))
            raw.append(sum((link_parity >> (word * 4 + half * 2 + lane) & 1) << half for half in range(2)))
        lanes.append(values)
        unavailable.append(missing)
        parity.append(raw)
    return {"data": lanes, "dnv": unavailable, "parity": parity}


def observe_return(oracle: ReadBusOracle, ck: int, **snapshot) -> tuple[list[int], dict]:
    decoded = returned_snapshot(oracle.ratio, **snapshot)
    completed = oracle.snapshot(ck, decoded["data"], decoded["dnv"])
    return completed, decoded


class ApbMonitor:
    """Sample setup/access/waits, then compare a canonical register completion.

    Hardware status snapshots and publication ACKs must be supplied through the
    RegisterBank's explicit owner APIs; PSLVERR never means a partial write.
    Reset aborts an incomplete transfer and resets the reference bank.
    """

    def __init__(self, schema: dict, channels: int = 2):
        self.bank = RegisterBank(schema, channels)
        self.pending: tuple | None = None
        self.access_phase = False
        self.completed = 0

    def sample(self, *, reset: bool, select: bool, enable: bool, ready: bool, request: tuple, data: int, error: bool):
        # request=(channel, name, write, data, strobes, broadcast, aligned)
        require(len(request) == 7, "APB request adapter shape")
        if reset:
            self.pending = None
            self.access_phase = False
            self.bank.reset()
            return
        if not select:
            require(not enable and self.pending is None, "APB dropped before completion")
            return
        if not enable:
            require(self.pending is None, "APB repeated setup before completion")
            self.pending = request
            self.access_phase = False
            return
        require(self.pending == request, "APB access without stable setup/payload")
        self.access_phase = True
        if ready:
            try:
                value = self.bank.access(*request)
                expected_error = False
            except ValueError:
                value, expected_error = 0, True
            require(error == expected_error, "APB error disposition")
            if not request[2] and not error:
                require(data == value, "APB read data")
            self.completed += 1
            self.pending = None
            self.access_phase = False

    def finish(self):
        require(self.pending is None, "APB incomplete trace tail")
