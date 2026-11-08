"""Check actual returned words against CA and the physical read path.

DFI enable/deadline checks alone admit physically premature data. Each slice
must also wait for its two device beats and the declared deserializer delay.
All lanes are sampled at CK, with DFI's independent per-slice word rotation.
"""

from collections import deque

from contracts.waveform_oracle import ROWS

from system.dram import decode
from system.host import require


class ReturnTiming:
    def __init__(self, profile):
        self.p = profile
        self.ratio = profile["ratio"]
        self.half_ck = 2 * self.ratio
        self.rl = ROWS[self.ratio, profile["read_link"]][1]
        self.rl += int(profile["read_dbi"]) * (1 if self.ratio == 4 else 2)
        self.pending = [deque(), deque()]
        self.pointer = [0, 0]
        self.serial = self.words = 0
        self.last_ck = -1

    def sample(self, ck, cs, ca, valid):
        require(ck == self.last_ck + 1, "missing return-timing CK")
        self.last_ck = ck
        operation = decode(ca) if cs else None
        if operation and operation.kind in ("RD", "MRR"):
            self.serial += 1
            beats = operation.beats if operation.kind == "RD" else 16
            physical = ck * self.half_ck + self.p["phy_half_wck"]["command"] + self.rl * self.half_ck
            physical += self.p["phy_half_wck"]["dq_output"] + self.p["phy_half_wck"]["read"]
            for word in range(beats // 2):
                split = 16 if self.ratio == 4 and beats == 32 and word >= 8 else 0
                earliest = (physical + 2 * word + split + 1 + self.half_ck - 1) // self.half_ck
                enable = ck * self.ratio + self.p["dfi_wck"]["trddata_en"] + word + split // 2
                deadline = (enable + self.p["dfi_wck"]["tphy_rdlat"]) // self.ratio
                for pending in self.pending:
                    pending.append((self.serial, word, earliest, deadline))
        for byte_slice in range(2):
            count = 0
            for ordinal in range(self.ratio):
                lane = (self.pointer[byte_slice] + ordinal) % self.ratio
                if not (valid >> (lane * 2 + byte_slice) & 1):
                    continue
                require(count == ordinal and bool(self.pending[byte_slice]), "returned word hole/identity")
                count += 1
                serial, word, earliest, deadline = self.pending[byte_slice].popleft()
                require(
                    earliest <= ck <= deadline,
                    f"physical read return command{serial} word{word} slice{byte_slice} CK{ck} "
                    f"outside [{earliest},{deadline}]",
                )
                self.words += 1
            self.pointer[byte_slice] = (self.pointer[byte_slice] + count) % self.ratio
            require(
                not self.pending[byte_slice] or ck < self.pending[byte_slice][0][3],
                "missing returned slice at physical/DFI deadline",
            )
        require(valid >> (self.ratio * 2) == 0, "returned upper lane in ratio two")

    def finish(self):
        require(not any(self.pending), "missing physical read return tail")
        return {"commands": self.serial, "returned_slices": self.words, "last_ck": self.last_ck}
