"""Independent natural-maintenance admission for the four-case wrapper fixture.

Command origins are deliberately earlier than DUT completion/timer renewal.
Complete physical replay supplies MR4 returns, recovery and RFM accounting.
Internal timers are retained diagnostics, never permission to enter a window.
"""

from system.dram import decode
from system.host import require

WINDOW_FIELDS = (
    "life_failure life_fault_code life_first_fault master_pending master_age master_elapsed life_elapsed "
    "external_fault update_fault fsp_fault backend_fault inline_fault register_fault maintenance_fault "
    "maint_ck running urgent pending candidate poll_refresh mr4_pending zq_pending mr4_request zq_request "
    "mr4_done zq_done thermal thermal_debt thermal_deadline extra_pending last_refresh last_mr4 last_zq "
    "refresh_budget refresh_lead poll_lead reserve maintenance_valid maintenance_ready maintenance_rfm "
    "maintenance_all register_pending register_state register_owner register_valid register_ready "
    "register_result_valid register_result_failed register_result_zq register_result_address "
    "register_result_value register_io_state"
).split()


def window_budget(p):
    """Bound ALL preparation from a recovered idle boundary to dirty request.

    Three full inline writes each read check/data then write data/check; the
    held read adds two reads. Pay all fourteen backend transfers serially,
    even across channels. Each slot pays the longest DFI/physical tail, full
    WCK disable (also covers direction recovery), and sixteen CK for inline,
    adapter, queue selection at CK/2, ACT/access pairs and sampled results.
    Four distinct row openings pay full tRC/PRE/tRCD plus eight dispatch CK.
    The replay rejects more ACTs; this is a finite-workload bound, not a
    universal scheduler-progress claim.

    Source allowance: six address drivers (two slow edges each), three full
    W drivers (five edges each), one trailing W driver (two), eight READY/
    response observation edges. Four completed/produced operations each pay
    request/result crossings and response retirement (two four-edge source
    crossings, three four-edge core crossings); final settlement pays the
    existing four-core/six-source/two-core/three-reduction schedule. Held B/R
    need only be produced here. Their consumption belongs to remaining_budget.
    """
    require(p["ranks"] == 1 and p["ratio"] == 4 and not p["always_on"], "window fixture shape")
    require(p["device"]["mr27"] == 69 and p["device"]["mr57"] == 3, "window RFM binding")
    t, d, w, r = p["dram_ck"], p["dfi_wck"], p["wck"], p["ratio"]

    def ceil(n, unit=r):
        return (n + unit - 1) // unit

    disable = ceil(w["twck_dis"])
    tail = max(
        ceil(d["tphy_wrlat"] + d["tphy_wrdata"] + 24 + d["twrdata_delay"]),
        ceil(d["trddata_en"] + 23 + d["tphy_rdlat"]),
        ceil(
            p["phy_half_wck"]["command"]
            + max(p["rl_ck"], p["wl_ck"]) * 2 * r
            + max(p["phy_half_wck"]["dq_input"], p["phy_half_wck"]["dq_output"])
            + 48
            + 2 * w["twck_toggle_post"],
            2 * r,
        ),
    )
    slot = tail + max(disable, t["twr"], t["twtr_l"], t["twtr_s"], t["trbtp"]) + 16
    rows = 4 * (t["trc"] + t["trp_ab"] + t["trcd"] + 8)

    def slow(n):
        return ceil(n * 3500, p["ck_ps"])

    source = slow(6 * 2 + 3 * 5 + 2 + 8) + 4 * (slow(8) + 12) + 4 + slow(6) + 2 + 3
    setup = 14 * slot + rows + source
    admission = max(
        p["wl_ck"] + t["twr"] + 8 + t["trp_ab"] + t["trfc_ab"] + disable + 8,
        t["tras_min"] + t["trp_ab"] + t["trfc_ab"] + 8,
    )
    # The largest normal-temperature lead includes one RFM before REF/MR4.
    # Use it even though the independently bounded ACT debt excludes new RFM.
    periodic = (
        max(
            ceil(d["trddata_en"] + 7 + d["tphy_rdlat"]),
            ceil(
                p["phy_half_wck"]["command"]
                + p["rl_ck"] * 2 * r
                + p["phy_half_wck"]["dq_output"]
                + 16
                + 2 * w["twck_toggle_post"],
                2 * r,
            ),
            t["tmrr"] + t["tmrw"],
        )
        + disable
        + 16
    )
    poll_lead = 2 * admission + periodic
    # Even the short test window must meet the unchanged full type0 admission
    # reserve, and must not encounter register urgency before that reserve ends.
    from wrapper_management import remaining_budget

    entry = remaining_budget(p)["total_ck"]
    ownership = max(p["management_ck"]["tphymstr_type0"], p["management_ck"]["tphyupd_type0"])
    reserve = ownership + p["management_ck"]["tphymstr_rfsh"] + 8
    request = entry + reserve
    total = setup + request
    renewal = min(t["trefi"] - admission, 3000 - poll_lead)
    require(total < renewal, "no feasible natural maintenance setup window")
    return dict(
        backend_transfers=14,
        max_acts=4,
        transfer_slot_ck=slot,
        row_ck=rows,
        source_ck=source,
        setup_ck=setup,
        entry_ck=entry,
        ownership_reserve_ck=reserve,
        request_ck=request,
        total_ck=total,
        refresh_lead_ck=admission,
        poll_lead_ck=poll_lead,
        renewal_floor_ck=renewal,
    )


class MaintenanceWindow:
    def __init__(self, p):
        self.p, self.budget = p, window_budget(p)
        self.origins = [dict() for _ in range(2)]
        self.diagnostics = [None, None]
        self.samples = [None, None]
        self.acts = self.transfers = 0
        self.start = None

    def observe(self, e, physical):
        """Consume only a raw falling sample already checked by PhysicalStream."""
        c, tick = e["channel"], e["tick"]
        self.samples[c] = e
        if not e["reset_n"] or e["memory_reset"] or e["destructive"]:
            self.origins[c] = {}
            return
        origins = self.origins[c]
        if e["cs"]:
            op = decode(e["ca"])
            if op.kind in ("REF", "RFM", "MRR", "MPC") and self.start is not None:
                require(False, "maintenance arrived during reserved setup")
            if op.kind == "REF":
                require(op.all_banks and e["cs"] == 1, "window requires natural rank0 REFab")
                origins["ref"] = tick
            elif op.kind == "MRR" and op.operand == 4:
                origins["mrr"] = tick
                origins["mrr_local"] = physical.ck - 1
                origins.pop("mr4", None)
            elif op.kind == "MPC" and op.operand == 134:
                origins["zq"] = tick
            elif op.kind == "ACT1" and self.start is not None:
                self.acts += 1
                require(self.acts <= self.budget["max_acts"], "setup exceeds bounded ACT workload")
        # ThermalReturns publishes only after all independently timed slices
        # and repeated MR data have passed. An internal done pulse cannot do so.
        if physical and physical.thermal.observations and "mrr" in origins:
            obs = physical.thermal.observations[-1]
            if obs["command_ck"] == origins["mrr_local"]:
                require(obs["mr4"] == 9 and obs["rank_mask"] == 1, "window thermal fixture changed")
                origins["mr4"] = origins["mrr"]

    def diagnostic(self, e):
        c = e["channel"]
        s = self.samples[c]
        require(
            s is not None and e["tick"] == s["tick"] and e["ps"] == s["ps"] + 625, "misphased maintenance diagnostic"
        )
        require(all(k in e for k in WINDOW_FIELDS), "incomplete maintenance diagnostic")
        self.diagnostics[c] = e

    def check(self, ps, required, physical, *, idle=False, source_settled=False):
        for c in range(2):
            s, d, o, p = self.samples[c], self.diagnostics[c], self.origins[c], physical[c]
            require(s is not None and s["ps"] < ps <= s["ps"] + 1, "stale maintenance readiness")
            # The current rising edge has not happened at the 1ps-after-falling
            # marker. Require the immediately preceding consumed diagnostic.
            require(
                d is not None and d["tick"] == s["tick"] - 1 and d["ps"] == s["ps"] - 625,
                "stale maintenance diagnostic",
            )
            require(all(k in o for k in ("ref", "mr4", "zq")), "missing natural maintenance renewal")
            require(
                s["trusted"]
                and s["config_valid"]
                and s["mr4"] == 9
                and not s["fault"]
                and s["enabled"]
                and not s["phyupd_req"]
                and not s["phymstr_req"],
                "window trust/thermal",
            )
            until = min(
                o["ref"] + self.p["dram_ck"]["trefi"] - self.budget["refresh_lead_ck"],
                o["mr4"] + 3000 - self.budget["poll_lead_ck"],
                o["zq"] + 3000 - self.budget["poll_lead_ck"],
            )
            require(s["tick"] + required < until, "insufficient independent periodic service headroom")
            require(p is not None, "missing independent maintenance history")
            m = p.phy.dram.maintenance
            require(
                m.interval == 3124 and not m.debt and not any(m.extra) and not m.self_refresh,
                "pending thermal/extra maintenance",
            )
            require(
                max(m.raa[0]) + (self.budget["max_acts"] if idle else 0) + 1 < m.maximum,
                "insufficient independent RFM headroom",
            )
            # A complete new REF recovery must finish BEFORE setup. At request
            # normal data/check work is deliberately live; no maintenance may
            # have intervened since that independently drained start.
            if idle:
                require(source_settled and p.drained(), "pending independent maintenance/host recovery")
            require(
                d["running"]
                and d["thermal"] == 9
                and not any(
                    d[k]
                    for k in (
                        "urgent",
                        "pending",
                        "candidate",
                        "mr4_pending",
                        "zq_pending",
                        "mr4_request",
                        "zq_request",
                        "thermal_debt",
                        "extra_pending",
                        "register_pending",
                        "register_state",
                        "register_owner",
                        "maintenance_valid",
                        "register_valid",
                        "register_result_valid",
                        "life_failure",
                        "life_fault_code",
                        "master_pending",
                        "external_fault",
                        "update_fault",
                        "fsp_fault",
                        "backend_fault",
                        "inline_fault",
                        "register_fault",
                        "maintenance_fault",
                        "register_io_state",
                    )
                ),
                "pending maintenance/register diagnostic",
            )

    def begin(self, ps, physical, source_settled):
        require(self.start is None, "duplicate maintenance readiness")
        self.check(ps, self.budget["total_ck"], physical, idle=True, source_settled=source_settled)
        self.start, self.acts, self.transfers = ps, 0, 0
        return dict(ps=ps, origins=[dict(o) for o in self.origins], budget=self.budget)

    def physical_transfer(self):
        if self.start is not None:
            self.transfers += 1
            require(self.transfers <= self.budget["backend_transfers"], "setup exceeds bounded physical workload")

    def request(self, ps, physical):
        require(self.start is not None, "request without fresh maintenance readiness")
        require(
            0 < ps - self.start <= self.budget["setup_ck"] * self.p["ck_ps"], "setup exceeded source-derived budget"
        )
        self.check(ps, self.budget["request_ck"], physical)
        self.start = None
