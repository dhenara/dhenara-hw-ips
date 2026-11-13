"""PHY-master REF observation, independent of the completion attestation.

The caller owns one continuous DramModel and supplies actual DRAM-side CA,
sampled sidebands and registered observations. A done bit does not execute REF.
The model's ordinary REF decoder owns retention, RAA and bank recovery. The
same model must survive entry and handback; no retention suspension is used.
"""

from system.dram import decode
from system.host import require


class MasterRefresh:
    def __init__(self, dram, response=2, type_limit=256, observations=None):
        self.dram = dram
        self.response = response
        self.type_limit = type_limit
        self.ack_at = None
        self.previous = None
        self.requests = {}
        self.executions = []
        self.low = 0
        self.returned = False
        self.last_ck = -1
        self.physical = [] if observations is None else observations
        self.windows = []
        self.credits = {}

    def observe(self, edge, sample):
        """Consume pre-NBA inputs, then the matching registered output sample.

        pin_cs/pin_ca are observed at DRAM CK, independently of done. This
        focused owner supports active IDLE ranks; SR needs its separate path.
        """
        ck = edge["ck"]
        require(ck == sample["ck"] and ck == self.last_ck + 1, "master phase/clock gap")
        require(edge["kind"] == "edge" and sample["kind"] == "sample", "master phase order")
        self.last_ck = ck
        if self.previous is not None:
            require(
                all(edge[k] == self.previous[k] for k in ("ack", "request", "external")),
                "master registered phase continuity",
            )
        active = (1 << self.dram.p["ranks"]) - 1
        require(not (edge["done"] | edge["request"] | sample["request"]) & ~active, "absent-rank refresh sideband")
        ack = bool(edge["ack"])
        if ack:
            require(self.ack_at is not None and not self.returned, "master unobserved ACK entry")
            require(edge["settled"] and edge["closed"] and edge["wck_off"], "master source/row/WCK obligations")
            require(not edge["admission"] and not edge["issue"], "master premature service")
            self.low |= ~edge["reports"] & active
        cs, ca = edge["pin_cs"], edge["pin_ca"]
        op = decode(ca) if cs else None
        external = edge["pin_owner"] == "phy"
        if ack and cs:
            require(external and op.kind == "REF" and op.all_banks, "master unexpected pin command")
        if external and cs:
            rank = cs - 1
            require(ack and rank in self.requests, "unsolicited/wrong-rank PHY REF")
            request = self.requests[rank]
            require("executed" not in request, "duplicate PHY REF")
            require(0 < ck - request["request"] <= self.response, "late PHY REF execution")
            request["executed"] = ck
            before = dict(
                last_refresh=self.dram.maintenance.last_refresh[rank], raa=list(self.dram.maintenance.raa[rank])
            )
        # One authoritative command owner advances all physical state, even
        # on DES. In particular a done pulse cannot refresh the model here.
        self.dram.command(ck, cs, ca)
        if external and cs:
            self.physical.append(
                dict(
                    rank=rank,
                    ck=ck,
                    before=before,
                    after=dict(
                        last_refresh=self.dram.maintenance.last_refresh[rank], raa=list(self.dram.maintenance.raa[rank])
                    ),
                    bank_ready=[b.ready for b in self.dram.banks[rank]],
                )
            )
        for rank in range(self.dram.p["ranks"]):
            bit = 1 << rank
            if edge["done"] & bit:
                require(ack and rank in self.requests, "unsolicited/stale/wrong-rank done")
                request = self.requests[rank]
                require("executed" in request and not request.get("done"), "done without new PHY REF")
                require(ck - request["request"] <= self.response, "late PHY REF completion")
                request["done"] = ck
                self.credits[rank] = (request["request"] + 1, ck)
                self.executions.append(
                    dict(rank=rank, **request, ready=request["executed"] + self.dram.p["dram_ck"]["trfc_ab"])
                )
            expected_pulse = bit if ack and edge["request"] & edge["done"] & bit else 0
            require(sample["external"] & bit == expected_pulse, "registered REF consumption")
            if edge["external"] & bit and "external_age0" in sample:
                earliest, latest = self.credits[rank]
                require(sample[f"last_refresh{rank}"] == earliest, "external REF retention origin")
                require(earliest <= self.dram.maintenance.last_refresh[rank] <= latest, "execution attestation bounds")
            if rank in self.credits and "recovered" in edge:
                _, latest = self.credits[rank]
                if ck > latest + 1 and edge["recovered"] & bit:
                    require(ck >= latest + self.dram.p["dram_ck"]["trfc_ab"], "early recovery attestation")
            if sample["request"] & bit and not edge["request"] & bit:
                require(edge["phy_request"], "new REF on handback")
                require(ack and rank not in self.requests, "duplicate refresh request")
                require(not edge["external"] & bit, "request before registered REF application")
                require(ck >= max(b.ready for b in self.dram.banks[rank]), "request during REF recovery")
                self.requests[rank] = dict(request=ck)
            if edge["request"] & bit and not sample["request"] & bit:
                require(rank in self.requests and self.requests[rank].get("done") == ck, "uncompleted refresh request")
                del self.requests[rank]
            if rank in self.requests:
                require(
                    ck - self.requests[rank]["request"] <= self.response or sample["fault"], "missing PHY completion"
                )
        if not edge["ack"] and sample["ack"]:
            require(self.ack_at is None or self.returned, "duplicate master ACK")
            require(
                edge["phy_request"] and edge["settled"] and edge["closed"] and edge["wck_off"],
                "master entry obligations",
            )
            self.ack_at = ck
            self.returned = False
            self.low = 0
            self.windows.append(dict(ack=ck))
            self.require_recovered(ck)
        if edge["ack"] and not sample["ack"]:
            require(
                not edge["phy_request"] and self.low == active and edge["reports"] & active == active,
                "master stale/early handback",
            )
            require(not self.requests, "master unfinished refresh at handback")
            self.require_recovered(ck)
            require(ck - self.ack_at <= self.type_limit, "master type duration")
            self.returned = True
            self.windows[-1]["return"] = ck
            require(sample["request"] == 0, "new REF on handback")
        self.previous = sample

    def require_recovered(self, ck):
        dram = self.dram
        trc = dram.p["dram_ck"]["trc"] + dram.maintenance.timing_derating().get("trc", 0)
        ready = max(
            dram.zq_ready,
            *dram.rank_ready,
            *(max(b.ready, b.act + trc) for rank in dram.banks for b in rank),
            *(at + dram.p["dram_ck"]["tpbr2act"] for at in dram.last_pb),
        )
        require(
            ck >= ready
            and all(b.row is None for r in dram.banks for b in r)
            and dram.pending is None
            and not (dram.sync and dram.sync[2] in (1, 2)),
            "master handback before PHY REF recovery",
        )

    def finish(self):
        require(self.ack_at is not None and self.returned and not self.requests, "unfinished master tail")
        self.require_recovered(self.last_ck)
        return self.executions
