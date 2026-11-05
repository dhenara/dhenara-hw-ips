# LPDDR5 draft review and scope decisions

Reviewed on 2026-11-05 against the [base specification](lpddr5_mc_microarch_spec.md) and [v0.2 addendum](lpddr5_mc_microarch_addendum_v0.2.md). Their original digests are recorded in [SHA256SUMS](SHA256SUMS).

This is a source-document review and a record of the owner's direction. There is no controller implementation to verify yet, and the cited JEDEC and DFI standards were not supplied or inspected. The owner accepted the recommended corrections on 2026-11-05, with the throughput and release-scope clarifications below. Acceptance of that direction does not establish a complete interface specification or verified hardware behavior. The imported drafts remain unchanged references; this page records how they must be refined.

## Owner decisions

| Topic | Confirmed direction on 2026-11-05 | Consequence for refinement |
| --- | --- | --- |
| PHY | Generic PHY-facing controller using the standard DFI interface | Retain the draft DFI 5.0 target pending standard validation; define configurable timing and supported interface options without coupling the controller to one PHY vendor. Vendor qualification remains separate evidence. |
| Technology and PPA | Keep the implementation independent of technology node and pursue the best practical power, performance, and area (PPA) | Optimize architecture and portable RTL. Assess resource costs and critical paths; report any later synthesis measurements with their tool, target, and constraints. No absolute physical PPA number or technology-node sign-off is agreed. |
| DFI throughput | Full throughput at 1:2; reduced throughput at 1:4 is acceptable | A single command-issue lane is a reasonable starting proposal. The contradictory dual-slot promise in addendum A.1 needs correction, and reduced-mode limits must be documented and tested. |
| Meaning of full throughput | DFI interface bandwidth must match the combined bandwidth of two AXI ports | Match payload capacity in a declared configuration and verify sustained transfer under conditions that can legally sustain that rate. Do not equate this with unconditional saturation of both DRAM channels or sum peak AXI read and write rates as if DRAM were full duplex. |
| First delivery | RTL verified against simulation models first | Functional and protocol evidence is required; technology-specific synthesis/timing closure and physical sign-off are not prerequisites for this first release. |
| First-release exclusions | Skip patrol scrubbing and deep power-down | Retain normal power-down, self-refresh, DVFS, inline/link ECC, correction-triggered scrub, and RFM support. RFM applicability and command details remain subject to the selected standard. |
| Engineering corrections | Proceed with the recommendations | Use the reconciled choices below and resolve detailed correctness contracts from authoritative references and engineering analysis. No previously marked standard assumption becomes confirmed by this general direction. |

## Intended controller

The first release includes AXI4 ingress, programmable address mapping and channel routing, per-channel read/write queues, page-aware scheduling with QoS and bandwidth control, command timing enforcement, DFI command/data paths, and APB control/status registers. Refresh, normal power-down, self-refresh, frequency changes, inline ECC, link ECC, and RFM remain in scope. Patrol scrubbing and deep power-down are excluded from this release. Scrubbing a corrected ECC word remains distinct from the excluded periodic patrol engine.

The intended defaults include two AXI ports, two independent x16 channels, one rank per channel, and 32-entry read and write queues. PHY circuitry and PHY training algorithms are outside the base scope; controller-side initialization, training hooks, and handover still need a complete contract.

## Findings requiring refinement

### Burst size, timing units, and DFI issue width

Base section 2 defaults `BURST_BYTES` to 32; addendum A.1 recommends 64, and C.2 resets `MODE.BURST_SEL` to BL32. The agreed correction is a 64-byte default while retaining 32- and 64-byte transfers. Size buffers for the supported maximum and distinguish that static size from the active burst mode. Mode changes must occur only while disabled or safely quiesced. The standard must still validate the exact BL16/BL32 command and transfer rules before freezing the splitter, mask, and data-return contracts.

Base section 5.5 uses phase-aware CK counters; addendum A.1 changes timing accounting to two-CK slots. Addendum A.1 says 1:4 issues two slots per core cycle, whereas B.1 limits v0.1 to one command per core cycle. The owner's reduced-throughput decision resolves the scope choice, but counter progression, command placement, and data timing still need one consistent model. A single command lane must not imply incorrectly halving the passage of DRAM time at 1:4.

Per-command CK occupancy and phase alignment remain explicitly marked `[VERIFY]`. Check them against the authoritative standards before treating the slot-budget calculations or two-CK command representation as an implementation contract.

### End-to-end bandwidth

Base section 2.1 gives 12.8 GB/s raw bandwidth per x16 channel, or 25.6 GB/s across two channels. **If** both default 128-bit AXI ports run at the proposed 400 MHz core clock, their aggregate payload limit is only `2 * 128 / 8 * 400 MHz = 12.8 GB/s` per direction, before protocol overhead. The drafts allow independent AXI clocks, so this is a configuration constraint rather than an unconditional design defect.

The owner's clarification makes combined AXI payload bandwidth the reference, rather than requiring every configuration to saturate both DRAM channels. Retain the draft's 128-bit default. Two 256-bit ports remain an allowed higher-bandwidth configuration; widening is not required merely to compensate for a configuration that already meets its host rate.

For same-direction traffic, calculate `B_AXI = sum(AXI_DATA_W[p] / 8 * f_AXI[p])` and compare it with the usable DFI payload rate across the enabled channels. DFI widths must count actual payload transferred in one controller cycle, without double-counting phases. Exact DFI packing remains unverified. At the illustrative 400 MHz AXI clock, two 128-bit ports require 12.8 GB/s; two 256-bit ports require 25.6 GB/s. These are calculated interface rates, not measured sustained memory bandwidth. The chosen channel count, rank/bank distribution, and clock ratios must make a claimed full-rate configuration feasible.

Proposed verification procedure: run saturated, aligned, full-burst, same-direction streams on both ports, balanced across enabled channels with enough outstanding requests and row locality to support the rate. Warm up the pipeline, disable optional throttling, and declare ECC and maintenance settings. Require no avoidable controller-induced bubbles against an independent legal-command reference schedule. Run refresh-enabled, mixed-direction, random, ECC, backpressure, and 1:4 cases separately and account for their legitimate overhead. Retain the owner's full-rate requirement; quantitative windows, tolerances, and the reference schedule remain to be qualified against the actual protocol before acceptance. Do not claim equal read-plus-write aggregate throughput on the shared DRAM data bus.

### Age thresholds and scheduler progress

Addendum A.7 specifies 8-bit age counters, which saturate at 255. C.3 resets `RD_URGENT_AGE` to 1024 and `WR_AGE_MAX` to 4096 age ticks, so those thresholds cannot be reached with the stated counter width. Use 16-bit saturating counters to match the threshold register width and define threshold comparison at saturation. Express aging and escalation in consistent age ticks, with `AGE_TICK_DIV` explicitly converting core cycles to ticks; reconcile `AGE_STEP` accordingly.

The R/W transition rules also need an escape when the current direction has no work before its minimum batch count is reached. Otherwise the stated residency condition can prevent service to the opposite direction. Strict bandwidth caps need a defined interaction with urgency, zero weights, empty buckets, and maintenance so that progress guarantees are meaningful.

### Pipeline replay and command state

Base section 5.5 uses parallel bank-table lookup for every entry; addendum A.3 selects cached state updated by broadcast. Use the addendum's event-broadcast approach as the starting architecture and reconcile the base text; queue-depth timing and area tradeoffs remain to be evaluated.

The addendum sets `inflight` at P3 but tests it at P1. Define how an entry already passing P1/P2 is invalidated or rechecked when an older copy reaches P3; the P1 test alone does not prove that duplicate picks are impossible. Also define ownership across ACT-1/ACT-2, auto-precharge, refresh preemption, replay, and state-feedback latency. Exact legality must include the live row and bank state, command-pair sequencing, data availability, and timing.

Base section 5.7 makes blocking an illegal command optional, and C.3 exposes `BLOCK_ON_VIOL`. Keep illegal command issue blocked in supported operation, and distinguish expected predictive-readiness replay from a real timing or protocol fault. Remove the production bypass semantics from the corrected register contract; a debug setting must not invalidate the controller's correctness guarantee.

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
- A verified configuration matrix. The owner has resolved the optional-feature scope above. Choose queue/ECC-cache sizes and other reversible implementation details through analysis within that scope.

The [DFI publisher's site](https://ddr-phy.org/) was inspected on 2026-11-05. Its [specification download link](https://ddr-phy.org/pages/2351641-Page-301) redirected to sign-in, and no authorized credential profile was available in this session. The public announcement is useful context but cannot validate the detailed protocol. An authorized standard copy or access is still needed. JEDEC, AXI, and APB normative documents and an independent LPDDR5 model are also absent from the selected repository and supplied uploads; their availability has not yet been established elsewhere.

These confirmed directions can be captured in a working Definition. It must remain incomplete until the required protocol references are inspected, the verification source is established, and the outstanding interface and acceptance details above are resolved. RTL execution has not started.
