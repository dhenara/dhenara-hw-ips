"""Physical CA and sampled master obligations for the real AXI wrapper.

The physical command table binds the existing model's delayed controller
timeline to independently captured pins. Lookahead is only a phase adapter:
each physical sample is used once, with its original CK, owner and raw CA.
It never creates a second DRAM/maintenance model or credits a done as REF.
"""

from collections import deque

from system.host import require


class PhysicalCommands:
    def __init__(self, records, p):
        self.delay = p["management_ck"]["tctrl_delay"]
        require(self.delay == 2 and p["ranks"] == 1, "wrapper physical phase/configuration")
        self.samples = {(e["channel"], e["tick"]): e for e in records if e["kind"] == "sample"}
        pins = [e for e in records if e["kind"] == "physical_ca"]
        self.pins = {(e["channel"], e["tick"]): e for e in pins}
        require(len(pins) == len(self.pins) and self.pins.keys() == self.samples.keys(), "physical CA coverage")
        state_rows = [e for e in records if e["kind"] == "master_state"]
        states = {(e["channel"], e["tick"], e["phase"]): e for e in state_rows}
        require(len(states) == len(state_rows) == 2 * len(self.samples), "master state phase coverage")
        for (c, tick), s in self.samples.items():
            for phase, displacement in (("window_sample", 0), ("window_edge", p["ck_ps"] // 2)):
                require(
                    (c, tick, phase) in states and states[c, tick, phase]["ps"] == s["ps"] + displacement,
                    "master state timestamp/phase",
                )
        hosts = [e for e in records if e["kind"] == "host_settlement"]
        require([e["tick"] for e in hosts] == [t for c, t in self.samples if c == 0], "host settlement edge coverage")
        for e in hosts:
            require(e["ps"] == self.samples[0, e["tick"]]["ps"] + p["ck_ps"] // 2, "host settlement timestamp/phase")
        queues = {c: deque([(0, 0)] * self.delay) for c in (0, 1)}
        for key, s in self.samples.items():
            pin, q = self.pins[key], queues[key[0]]
            require(pin["ps"] == s["ps"] and pin["phy"] == s["master"], "physical owner/phase binding")
            reset = not s["reset_n"] or s["memory_reset"] or s["destructive"]
            if reset:
                q.clear()
                q.extend([(0, 0)] * self.delay)
                require((pin["cs"], pin["ca"]) == (0, 0), "physical CA during reset")
                continue
            delayed = q.popleft()
            q.append((s["cs"], s["ca"]))
            if pin["phy"]:
                require(not s["cs"] and not delayed[0], "controller CA tail under PHY owner")
                require(not pin["cs"] or pin["cs"] == 1 and pin["ca"] == 0x2038, "PHY physical REF encoding/rank")
            else:
                require((pin["cs"], pin["ca"]) == delayed, "controller/physical CA delay")
        self.applied = []

    def command(self, s):
        """Return CA observed at the DRAM CK this DFI sample will advance."""
        key = s["channel"], s["tick"] + self.delay
        pin = self.pins.get(key)
        if pin is None:
            require(not s["cs"], "physical command missing final delay tail")
            return (0, 0), None
        future = self.samples[key]
        if future["boot"] != s["boot"] or future["destructive"] or future["memory_reset"]:
            require(not s["cs"], "controller command crosses destructive boundary")
            return (0, 0), None
        return (pin["cs"], pin["ca"]), pin if pin["phy"] and pin["cs"] else None

    def applied_ref(self, pin, physical, before):
        dram, m = physical.phy.dram, physical.phy.dram.maintenance
        at = physical.ck - 1 + self.delay
        require(m.last_refresh[0] == at, "physical REF did not renew independent retention")
        require(
            all(b.row is None and b.ready == at + dram.p["dram_ck"]["trfc_ab"] for b in dram.banks[0]),
            "physical REF lacks full independent recovery",
        )
        imt = (dram.p["device"]["mr27"] >> 1 & 31) * 8
        require(m.raa[0] == [max(0, debt - imt) for debt in before], "physical REF RAA decrement")
        self.applied.append(
            dict(channel=pin["channel"], tick=pin["tick"], model_ck=at, ready=at + dram.p["dram_ck"]["trfc_ab"])
        )


class RfmTimeout:
    """Count the first consumed request through the first overdue decision."""

    def __init__(self, response):
        self.response = response
        self.request = self.decision = self.fatal = None
        self.edges = 0

    def edge(self, e, w):
        require(not e["error"] if "error" in e else True, "RFM timeout uses external error")
        require(not e["master"], "mandatory-RFM early ACK")
        if not e["phymstr_req"]:
            return
        if self.request is None:
            self.request = e["tick"]
        self.edges += 1
        if not w["master_pending"]:
            require(self.edges == 1, "RFM pending tracking disappeared")
            return
        overdue = w["master_age"] + 1 > self.response
        require(w["master_age"] == self.edges - 2, "RFM sampled entry age")
        require(w["life_failure"] == (12 if overdue else 0), "RFM first-overdue decision")
        if overdue and self.decision is None:
            self.decision = e["tick"]

    def failed(self, sample, window):
        require(
            self.decision is not None and sample["tick"] == self.decision + 1,
            "RFM fault not at first overdue registered edge",
        )
        require(
            window["life_fault_code"] == 12 and not sample["master"] and not sample["trusted"],
            "RFM timeout fault/trust outcome",
        )
        self.fatal = sample["tick"]

    def finish(self):
        require(self.fatal is not None, "missing RFM timeout/fatal tail")
        return dict(request=self.request, decision=self.decision, fault=self.fatal)


def settled_abort(e, source_settled):
    require(
        source_settled
        and e["wrapper"]
        and e["core"]
        and e["bridge"]
        and e["abort"]
        and not e["admission_busy"]
        and e["fatal"],
        "destructive reset before actual host/bridge abort settlement",
    )


def active_refresh(records, p, applied):
    """Check execution, consumed done, application, recovery and fresh return.

    The one continuous PhysicalStream already executed the raw pin CA and
    checked retention/RAA/row/data/WCK. These sampled witnesses must agree
    with that physical execution; they cannot replace it.
    """
    samples = {e["tick"]: e for e in records if e["kind"] == "sample" and e["channel"] == 0}
    edges = {e["tick"]: e for e in records if e["kind"] == "management_edge" and e["channel"] == 0}
    states = {(e["phase"], e["tick"]): e for e in records if e["kind"] == "master_state" and e["channel"] == 0}
    requests, executions, done = [], [], []
    pulses = []
    low = False
    fresh = False
    ack = False
    returned = False
    for tick, edge in edges.items():
        after = samples.get(tick + 1)
        if after is None:
            continue
        state = states["window_sample", tick + 1]
        require(
            state["external"] == (edge["refresh_done"] & edge["refresh_request"] if edge["master"] else 0),
            "PHY REF sampled done/application pulse",
        )
        if not edge["master"] and after["master"]:
            require(not ack and edge["phymstr_req"] and edge["settled"] and edge["drained"], "active REF entry")
            ack = True
            entered = tick
        if after["refresh_request"] and not edge["refresh_request"]:
            require(edge["master"] and edge["phymstr_req"], "active REF request outside ownership")
            requests.append(tick)
        if edge["refresh_done"]:
            require(
                edge["master"] and edge["refresh_request"] == edge["refresh_done"] == 1,
                "unsolicited/wrong-rank PHY done",
            )
            done.append(tick)
        if state["external"]:
            pulses.append(tick + 1)
        if edge["master"]:
            require(edge["settled"] and not edge["admission"] and not edge["issue"], "host service under PHY owner")
            if not edge["rank_valid"]:
                low = True
            elif low and edge["rank_valid"] == 1 and edge["rank_sr"] == 0:
                fresh = True
            if not after["master"]:
                require(not edge["phymstr_req"] and fresh and not edge["refresh_request"], "stale PHY returned state")
                require(ack and tick - entered <= p["management_ck"]["tphymstr_type0"], "master type bound")
                returned = True
                returned_at = tick
    executions = [e for e in applied if e["channel"] == 0]
    require(
        ack and returned and len(requests) == len(executions) == len(done) == len(pulses) == 1,
        "missing/duplicate active PHY REF phases/tail",
    )
    request, execution, completion = requests[0], executions[0]["tick"], done[0]
    require(
        request < execution <= completion <= request + p["management_ck"]["tphymstr_rfsh"],
        "PHY REF execution/completion response bound",
    )
    application = states["window_edge", completion + 1]
    after = states["window_sample", completion + 2]
    require(
        application["external"] == 1 and after["last_refresh"] == application["maint_ck"] - application["external_age"],
        "PHY REF maintenance application/retention origin",
    )
    require(
        after["last_refresh"] == states["window_edge", request]["maint_ck"] + 1, "PHY REF earliest execution origin"
    )
    for tick in range(completion + 2, completion + p["dram_ck"]["trfc_ab"]):
        require(not states["window_sample", tick]["recovered"] & 1, "early PHY REF recovery")
    require(returned_at >= completion + p["dram_ck"]["trfc_ab"], "PHY return before full REF recovery")
    require(states["window_edge", returned_at]["recovered"] == 1, "PHY return without maintenance recovery")
    fresh_low = [
        tick
        for tick, e in edges.items()
        if completion + p["dram_ck"]["trfc_ab"] <= tick <= returned_at and e["master"] and not e["rank_valid"]
    ]
    require(
        fresh_low
        and any(
            edges[t]["rank_valid"] == 1 and edges[t]["rank_sr"] == 0 for t in range(fresh_low[-1] + 1, returned_at + 1)
        ),
        "stale post-recovery PHY report",
    )
    return dict(
        request=request,
        execution=execution,
        done=completion,
        application=completion + 1,
        returned=returned_at,
        physical=executions,
    )
