"""Apply temperature only after independently checked complete MRR4 returns."""

from system.host import require


class ThermalReturns:
    def __init__(self, maintenance):
        self.maintenance = maintenance
        self.pending = {}
        self.observations = []

    def begin(self, identity: int, command_ck: int, rank_mask: int, register: int) -> None:
        if register == 4:
            require(identity not in self.pending, "duplicate thermal MRR identity")
            self.pending[identity] = (command_ck, rank_mask, {})

    def receive(self, identity: int, word: int, lane: int, value: int, ck: int, ps: int) -> None:
        if identity not in self.pending:
            return
        command_ck, rank_mask, received = self.pending[identity]
        require(0 <= word < 8 and lane in (0, 1) and 0 <= value < 65536, "thermal return slice")
        require((word, lane) not in received, "duplicate thermal return slice")
        received[word, lane] = value
        if len(received) != 16:
            return
        # The restricted digital device repeats the MR byte four times on DQ0
        # and uses DA markers elsewhere. All slices have already passed the
        # physical-return oracle; also reject inconsistent replicated MR data.
        byte = received[0, 0] & 255
        require(
            all(received[w, s] == (byte * 257 if w < 4 and s == 0 else 0xDADA) for w in range(8) for s in range(2)),
            "inconsistent complete thermal return",
        )
        self.maintenance.temperature(byte)
        self.observations.append(
            {
                "identity": identity,
                "command_ck": command_ck,
                "rank_mask": rank_mask,
                "completed_ck": ck,
                "completed_ps": ps,
                "mr4": byte,
                "interval_ck": self.maintenance.interval,
                "debt_ck": self.maintenance.debt,
                "derating_ck": self.maintenance.timing_derating(),
            }
        )
        del self.pending[identity]

    def finish(self) -> list[dict]:
        require(not self.pending, "incomplete thermal return tail")
        return self.observations
