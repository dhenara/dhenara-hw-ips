# LPDDR5 draft review and scope decisions

Reviewed on 2026-11-05 against the [base specification](lpddr5_mc_microarch_spec.md) and [v0.2 addendum](lpddr5_mc_microarch_addendum_v0.2.md). Their original digests are recorded in [SHA256SUMS](SHA256SUMS).

This is a source-document review and a record of the owner's direction. There is no controller implementation to verify yet, and the cited JEDEC and DFI standards were not supplied or inspected. Recommendations below are proposed refinements, not approved replacements for the drafts or verified hardware behavior.

## Owner decisions

| Topic | Confirmed direction on 2026-11-05 | Consequence for refinement |
| --- | --- | --- |
| PHY | Generic PHY-facing controller using the standard DFI interface | Retain the draft DFI 5.0 target pending standard validation; define configurable timing and supported interface options without coupling the controller to one PHY vendor. Vendor qualification remains separate evidence. |
| Technology and PPA | Keep the implementation independent of technology node and pursue the best practical power, performance, and area (PPA) | Optimize architecture and portable RTL. Assess resource costs and critical paths; report any later synthesis measurements with their tool, target, and constraints. No absolute physical PPA number or technology-node sign-off is agreed. |
| DFI throughput | Full throughput at 1:2; reduced throughput at 1:4 is acceptable | A single command-issue lane is a reasonable starting proposal. The contradictory dual-slot promise in addendum A.1 needs correction, and reduced-mode limits must be documented and tested. |

## Intended controller

The drafts describe AXI4 ingress, programmable address mapping and channel routing, per-channel read/write queues, page-aware scheduling with QoS and bandwidth control, command timing enforcement, DFI command/data paths, and APB control/status registers. Refresh, power-down, self-refresh, frequency changes, inline ECC, and link ECC are in the stated scope; patrol scrub, RFM, and deep sleep are described as optional or subject to verification. No decision has removed the stated ECC, power, or frequency-change capabilities.

The intended defaults include two AXI ports, two independent x16 channels, one rank per channel, and 32-entry read and write queues. PHY circuitry and PHY training algorithms are outside the base scope; controller-side initialization, training hooks, and handover still need a complete contract.

## Findings requiring refinement

### Burst size, timing units, and DFI issue width

Base section 2 defaults `BURST_BYTES` to 32; addendum A.1 recommends 64, and C.2 resets `MODE.BURST_SEL` to BL32. Select one default and clarify how a compile-time buffer parameter interacts with a software-selectable burst mode. Supporting both lengths requires explicit buffer, splitter, mask, and data-return behavior.

Base section 5.5 uses phase-aware CK counters; addendum A.1 changes timing accounting to two-CK slots. Addendum A.1 says 1:4 issues two slots per core cycle, whereas B.1 limits v0.1 to one command per core cycle. The owner's reduced-throughput decision resolves the scope choice, but counter progression, command placement, and data timing still need one consistent model. A single command lane must not imply incorrectly halving the passage of DRAM time at 1:4.

Per-command CK occupancy and phase alignment remain explicitly marked `[VERIFY]`. Check them against the authoritative standards before treating the slot-budget calculations or two-CK command representation as an implementation contract.

### End-to-end bandwidth

Base section 2.1 gives 12.8 GB/s raw bandwidth per x16 channel, or 25.6 GB/s across two channels. **If** both default 128-bit AXI ports run at the proposed 400 MHz core clock, their aggregate payload limit is only `2 * 128 / 8 * 400 MHz = 12.8 GB/s` per direction, before protocol overhead. The drafts allow independent AXI clocks, so this is a configuration constraint rather than an unconditional design defect.

Define a configuration that can exercise full controller throughput. Two 256-bit ports at 400 MHz provide 25.6 GB/s of theoretical aggregate bandwidth per direction; faster independent AXI clocks are another possibility. These are arithmetic examples, not measured performance or a selected default. Record traffic patterns, read/write mix, row locality, refresh and ECC settings, and latency/efficiency targets when defining acceptance. Full-rate DFI issue alone cannot establish sustained end-to-end bandwidth.

### Age thresholds and scheduler progress

Addendum A.7 specifies 8-bit age counters, which saturate at 255. C.3 resets `RD_URGENT_AGE` to 1024 and `WR_AGE_MAX` to 4096 age ticks, so those thresholds cannot be reached with the stated counter width. Widen the counters or change the thresholds and units consistently, including `AGE_STEP` and `AGE_TICK_DIV`.

The R/W transition rules also need an escape when the current direction has no work before its minimum batch count is reached. Otherwise the stated residency condition can prevent service to the opposite direction. Strict bandwidth caps need a defined interaction with urgency, zero weights, empty buckets, and maintenance so that progress guarantees are meaningful.

### Pipeline replay and command state

Base section 5.5 uses parallel bank-table lookup for every entry; addendum A.3 selects cached state updated by broadcast. Treat the latter as the proposed refinement and reconcile the base text.

The addendum sets `inflight` at P3 but tests it at P1. Define how an entry already passing P1/P2 is invalidated or rechecked when an older copy reaches P3; the P1 test alone does not prove that duplicate picks are impossible. Also define ownership across ACT-1/ACT-2, auto-precharge, refresh preemption, replay, and state-feedback latency. Exact legality must include the live row and bank state, command-pair sequencing, data availability, and timing.

Base section 5.7 makes blocking an illegal command optional, and C.3 exposes `BLOCK_ON_VIOL`. Proposed correction: keep illegal command issue blocked in supported operation, and distinguish expected predictive-readiness replay from a real timing or protocol fault. A debug setting should not silently invalidate the controller's correctness guarantee.

### AXI ordering, write completion, and ECC lifetime

Base sections 4.1 and 5.4 describe hazards, forwarding, merging, and posted B responses but do not fully specify their interaction across IDs and ports. Define acceptance order for overlapping accesses, eligibility for merging, dependencies on multiple pending writes, and the lifetime of forwarded data. Addendum A.3 retires queue entries at CAS issue; write data and return tracking may still be needed after that point.

For ECC read-modify-write, decide when a posted write may be acknowledged and how an error detected after acknowledgement is reported. Define data/ECC consistency during eviction, scrubbing, reset, quiesce, and frequency change, including flushing dirty ECC cache contents before declaring the controller drained. Clarify the reserved-capacity formula: 64 data bits plus 8 check bits means check storage equal to one eighth of protected data, or one ninth of the combined data-plus-check capacity, before allocation granularity.

The unsupported-exclusive-access behavior in base section 4.1 also needs a precise AXI contract. Validate the response and memory side effects against the relevant AXI specification rather than relying on the shorthand “treated as normal.”

### Register and configuration completeness

The addendum supplies a useful proposed register map, but several points remain unresolved. `TRAIN_DONE` is described as software-set or PHY-set while its access type is `RW1C`; define how completion is set and cleared. `ECC_ERR_INFO.AXI_ID` has only 8 bits although the base allows `AXI_ID_W` up to 16; specify full-width capture or documented truncation. DFI timing values used in B.2, including `tinit_complete`, `tlp_data_wakeup`, and `tdram_clk_enable`, lack explicit counterparts in the timing map shown in C.3.

Freeze APB version and byte-write behavior, reset values marked `impl.`, supported parameter combinations, address-map validity rules, and cross-clock update semantics. Runtime changes to scheduler controls need atomicity or specified intermediate behavior; locking timing registers alone does not define this.

### Refresh, maintenance, and timeout recovery

Base section 5.7 puts injection and power/frequency control ahead of urgent refresh, while section 5.10 calls forced refresh highest priority. Define an arbitration policy that protects refresh deadlines across training, PHY updates, power transitions, and frequency changes.

Addendum B.5 allows update pauses to consume refresh credit, then forces refresh afterward. A credit limit alone does not prove a deadline is met if the pause can exceed the remaining allowance. Establish maximum pause bounds, admission conditions, and failure recovery from the standards and interface contract. Timeout IRQs also need a defined post-timeout state and treatment of outstanding AXI requests; an interrupt alone is not a recovery policy.

## Inputs and decisions still needed before freezing implementation

- Exact authorized JEDEC LPDDR5, DFI, AXI, and APB references, with revisions. The supplied `[VERIFY]` markers remain open until their claims are checked.
- A generic DFI/DRAM verification strategy and accessible models or VIP. The repository currently contains none. Define supported DFI options, configurable latency bounds, and responsibility for PHY training without assuming a vendor-specific model.
- Quantified throughput, latency, fairness, and PPA evaluation conditions. Preserve technology independence; state the limits of any technology-independent or generic-library estimate.
- A verified configuration matrix and decisions on the optional features. Choose queue/ECC-cache sizes and other reversible implementation details through analysis within that agreed scope.

The next specification revision should consolidate the drafts and these confirmed directions, resolve contradictions, and keep any remaining assumptions explicit. RTL implementation and an acceptance-ready Definition have not been started by this repository setup.
