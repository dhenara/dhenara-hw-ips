"""Natural channel/granule RFM entry proof on one continuous physical model.

The CA-driven RTL fixture starts with zero-filled memory. Read expectations
come from decoded commands and that initial image; completion data/stages are
observed. This does not extend pin-WCK/data waveform or real AXI qualification.
"""

from master_refresh import MasterRefresh
from system.dram import DramModel, decode
from system.host import require


def replay_rfm(rows, profile, mode):
    dram = DramModel(profile)
    # Outside the two churn banks. Never replace memory or maintenance on
    # entry/return; this explicit initial sentinel is not an AXI write claim.
    dram.memory[0, 15, 0, 0] = 0xA5
    master = MasterRefresh(dram)
    live, accepted, completed, rfm, refs, issues = {}, [], [], [], [], []
    requested = ack = fault = None
    delay = profile["phy_half_wck"]["command"] // (2 * profile["ratio"])
    edges, samples = rows[::2], rows[1::2]
    require(len(edges) == len(samples), "RFM truncated phases")
    for i, (edge, sample) in enumerate(zip(edges, samples, strict=True)):
        try:
            ck = edge["ck"]
            old = edges[i - delay] if i >= delay else dict(dfi_cs=0, dfi_ca=0)
            require(
                edge["pin_owner"] == "controller"
                and (edge["pin_cs"], edge["pin_ca"]) == (old["dfi_cs"], old["dfi_ca"]),
                "RFM declared CA delay/owner",
            )
            require(edge["accepted"] == len(accepted) and edge["completed"] == len(completed), "RFM ledger counters")
            require(edge["settled"] == int(not live and not edge["request_valid"]), "RFM host settlement witness")
            if edge["request_valid"] and edge["request_ready"]:
                tag = edge["request_tag"]
                n = tag - 3000
                address = (n // 2 << 15) | ((0 if n % 2 == 0 else 8) << 11)
                require(0 <= n < 128 and tag not in accepted and edge["request_address"] == address, "RFM admission")
                live[tag] = dict(tag=tag, address=address, accepted=ck, burst=None)
                accepted.append(tag)
            if edge["phy_request"] and requested is None:
                requested = ck
            if not edge["ack"] and sample["ack"]:
                require(
                    not live and not edge["maintenance_valid"] and not sample["maintenance_valid"], "RFM entry work"
                )
                ack = ck
            if sample["fault"] and fault is None:
                fault = ck
            before = dict(raa=[int(n) for n in dram.maintenance.raa[0]], last_refresh=dram.maintenance.last_refresh[0])
            count = len(dram.bursts)
            master.observe(edge, sample)
            if edge["pin_cs"]:
                op = decode(edge["pin_ca"])
                if op.kind == "RFM":
                    decrement = dram.maintenance.imt * (2, 3, 4, 8)[profile["device"]["mr57"] & 3] // 2
                    require(op.all_banks and before["raa"][0] >= dram.maintenance.maximum - 1, "RFM natural ACT debt")
                    require(
                        dram.maintenance.raa[0] == [max(0, n - decrement) for n in before["raa"]]
                        and dram.maintenance.last_refresh[0] == before["last_refresh"],
                        "RFM physical debt/retention effect",
                    )
                    rfm.append(
                        dict(
                            ck=ck,
                            before=before,
                            after=dict(
                                raa=[int(n) for n in dram.maintenance.raa[0]],
                                last_refresh=dram.maintenance.last_refresh[0],
                            ),
                            recovered=max(b.ready for b in dram.banks[0]),
                        )
                    )
                elif op.kind == "REF":
                    refs.append(ck)
            if edge["maintenance_valid"] and edge["maintenance_ready"] and edge["maintenance_rfm"]:
                decrement = dram.maintenance.imt * (2, 3, 4, 8)[profile["device"]["mr57"] & 3] // 2
                require(
                    sample["raa0"] == max(0, edge["raa0"] - decrement)
                    and sample["last_refresh0"] == edge["last_refresh0"],
                    "RFM RTL debt/retention effect",
                )
            if len(dram.bursts) != count:
                burst = dram.bursts[-1]
                address = (burst.rank << 30) | (burst.row << 15) | (burst.bank << 11) | (burst.column << 5)
                matches = [owner for owner in live.values() if owner["address"] == address and owner["burst"] is None]
                require(len(matches) == 1 and not burst.write and burst.beats == 32, "RFM physical granule identity")
                owner = matches[0]
                owner["burst"] = burst
                owner["expected"] = b"".join(
                    dram.beat(burst, beat, at).to_bytes(2, "little") for beat, at in enumerate(burst.times)
                )
                issues.append(dict(tag=owner["tag"], ck=ck, final_pin_half_wck=burst.times[-1]))
            if edge["completion_valid"] and edge["completion_ready"]:
                tag = edge["completion_tag"]
                require(tag in live, "RFM duplicate/unaccepted completion")
                owner = live.pop(tag)
                require(
                    edge["completion_generation"] == 1
                    and edge["completion_boot"] == 0
                    and edge["completion_config"] == 1
                    and not edge["completion_write"],
                    "RFM completion identity",
                )
                if edge["completion_failed"]:
                    require(mode == 6 and edge["fault"] and edge["completion_response"] != 0, "RFM fault disposition")
                else:
                    require(
                        owner["burst"] is not None
                        and edge["completion_physical"]
                        and edge["completion_integrity"]
                        and edge["completion_response"] == 0
                        and int(edge["completion_data"], 16).to_bytes(64, "little") == owner["expected"],
                        "RFM completion data/stages",
                    )
                    require(
                        ck * dram.ck >= owner["burst"].times[-1] + profile["phy_half_wck"]["read"],
                        "RFM completion before physical return",
                    )
                completed.append(dict(tag=tag, ck=ck, failed=bool(edge["completion_failed"])))
            require(sample["accepted"] == len(accepted) and sample["completed"] == len(completed), "RFM sampled ledger")
            require(sample["settled"] == int(not live and not sample["request_valid"]), "RFM sampled settlement")
            if edge["temperature_valid"]:
                dram.maintenance.temperature(edge["temperature"])
        except ValueError as error:
            error.observation = dict(edge=edge, sample=sample)
            raise
    require(rfm, "RFM missing physical proof")
    require(dram.memory == {(0, 15, 0, 0): 0xA5}, "RFM initial memory preservation")
    require(requested is not None, "RFM missing entry request")
    if mode == 6:
        # This interface has no fatal-cancellation return. The real host
        # settles its own responses before confirmed destructive recovery.
        # Keep the exact unissued read pending and settlement false here.
        require(
            set(live) == {3062}
            and live[3062]["burst"] is None
            and len(accepted) == 63
            and len(completed) == 62
            and not samples[-1]["settled"],
            "RFM retained pending obligation",
        )
        trigger = next(e for e in edges if e["ck"] == requested)
        require(trigger["maintenance_valid"] and trigger["maintenance_rfm"], "RFM pending trigger")
        require(fault == requested + profile["management_ck"]["tphymstr_resp"] + 1, "RFM first overdue fault")
        require(ack is None and not any(e["ack"] for e in rows), "RFM early ACK")
        require(samples[-1]["ck"] - fault >= 180, "RFM short containment tail")
        require(
            all(
                s["fault"] and s["code"] == 12 and not s["admission"] and not s["issue"] and not s["trusted"]
                for s in samples
                if s["ck"] >= fault
            ),
            "RFM failure containment",
        )
        # No healthy completed master window is claimed for a failed entry.
        require(master.ack_at is None and not master.requests, "RFM fault ownership")
    else:
        require(not live and len(accepted) == len(completed) == 67, "RFM incomplete healthy work")
        require(fault is None and requested == rfm[0]["recovered"] - 1, "RFM late physical trigger")
        master.finish()
        require(ack is not None and ack >= rfm[0]["recovered"], "RFM entry before recovery")
        require(ack - requested <= profile["management_ck"]["tphymstr_resp"], "RFM response bound")
        require(any(c["ck"] > master.windows[0]["return"] for c in completed), "RFM renewed granule service")
        require(
            refs and any(e["zq_done"] for e in edges) and any(e["temperature_valid"] for e in edges),
            "RFM missing ordinary REF/MR4/ZQ",
        )
    return dict(
        mode=mode,
        qualification="channel/granule proof; not actual AXI or new pin-WCK/data qualification",
        request=requested,
        ack=ack,
        fault=fault,
        response_ck=None if ack is None else ack - requested,
        rfm=rfm,
        periodic_ref=refs,
        accepted=accepted,
        completed=completed,
        retained_pending=[
            dict(tag=owner["tag"], address=owner["address"], accepted=owner["accepted"], physical_read_issued=False)
            for owner in live.values()
        ],
        host_error_settlement_qualified=False,
        physical_reads=issues,
        windows=master.windows,
        final_raa=[int(n) for n in dram.maintenance.raa[0]],
        final_last_refresh=dram.maintenance.last_refresh[0],
        final_retention_interval=dram.maintenance.interval,
        final_ck=master.last_ck,
        sentinel_preserved=True,
    )


def sensitivity_rfm(rows, profile, mode):
    """Reject corruptions of the new stream's ownership/accounting evidence."""
    probes = []
    unsettled = next(i for i, e in enumerate(rows) if e["kind"] == "edge" and e["accepted"] > e["completed"])
    completion = next(
        i
        for i, e in enumerate(rows)
        if e["kind"] == "edge" and e["completion_valid"] and e["completion_ready"] and not e["completion_failed"]
    )
    effect = next(
        i + 1
        for i, e in enumerate(rows)
        if e["kind"] == "edge" and e["maintenance_valid"] and e["maintenance_ready"] and e["maintenance_rfm"]
    )
    for index, key, value, expected in (
        (unsettled, "settled", 1, "RFM host settlement witness"),
        (completion, "completion_data", "1", "RFM completion data/stages"),
        (completion, "completion_tag", 9999, "RFM duplicate/unaccepted completion"),
        (effect, "raa0", rows[effect]["raa0"] + 1, "RFM RTL debt/retention effect"),
        (effect, "last_refresh0", rows[effect]["last_refresh0"] + 1, "RFM RTL debt/retention effect"),
    ):
        changed = list(rows)
        changed[index] = dict(changed[index], **{key: value})
        try:
            replay_rfm(changed, profile, mode)
        except ValueError as error:
            require(str(error) == expected, "RFM wrong sensitivity rejection: " + str(error))
            probes.append(dict(index=index, key=key, rejection=str(error)))
        else:
            raise ValueError("RFM mutation escaped: " + key)
    # Independently consume actual physical CA up to the last recovery CK.
    # A handback assertion at precisely that phase must reject the real tail.
    dram = DramModel(profile)
    master = MasterRefresh(dram)
    endpoint = None
    for edge in rows[::2]:
        ck = edge["ck"]
        dram.command(ck, edge["pin_cs"], edge["pin_ca"])
        if edge["temperature_valid"]:
            dram.maintenance.temperature(edge["temperature"])
        if edge["pin_cs"] and decode(edge["pin_ca"]).kind == "RFM":
            endpoint = ck + profile["dram_ck"]["trfm_ab"]
        if endpoint is not None and ck == endpoint - 1:
            try:
                master.require_recovered(ck)
            except ValueError as error:
                require(str(error) == "master handback before PHY REF recovery", "RFM recovery sensitivity")
                probes.append(dict(ck=ck, recovery=endpoint, rejection=str(error)))
            else:
                raise ValueError("RFM recovery endpoint escaped")
            break
    require(len(probes) == 6, "RFM sensitivity incomplete")
    return probes
