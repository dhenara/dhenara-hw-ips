# LPDDR5 draft review and scope decisions

Reviewed on 2026-11-05 against the [base specification](lpddr5_mc_microarch_spec.md) and [v0.2 addendum](lpddr5_mc_microarch_addendum_v0.2.md). Their original digests are recorded in [SHA256SUMS](SHA256SUMS). The repository baseline for this update is `2f9f360dd7da0bdce37a17887f8282970c0ed1d3`.

This is a source-document review and a record of the owner's direction. There is no controller implementation to verify yet. The supplied JESD209-5C standard has now been inspected in the targeted sections listed below; DFI remains unavailable. The owner accepted the recommended corrections on 2026-11-05, with the throughput and release-scope clarifications below. Acceptance of that direction does not establish a complete interface specification or verified hardware behavior. The imported drafts remain unchanged references; this page records how they must be refined.

## Owner decisions

| Topic | Confirmed direction on 2026-11-05 | Consequence for refinement |
| --- | --- | --- |
| PHY | Generic PHY-facing controller using the standard DFI interface | Retain the draft DFI 5.0 target pending standard validation; define configurable timing and supported interface options without coupling the controller to one PHY vendor. Vendor qualification remains separate evidence. |
| Technology and PPA | Keep the implementation independent of technology node and pursue the best practical power, performance, and area (PPA) | Optimize architecture and portable RTL. Assess resource costs and critical paths; report any later synthesis measurements with their tool, target, and constraints. No absolute physical PPA number or technology-node sign-off is agreed. |
| DFI throughput | Full throughput at 1:2; reduced throughput at 1:4 is acceptable | A single scheduler-selection lane remains a starting proposal, but its serializer must support legal one-CK commands and mandatory command pairs. DFI packing and reduced-mode limits must be documented and tested. |
| Meaning of full throughput | DFI interface bandwidth must match the combined bandwidth of two AXI ports | Match payload capacity in a declared configuration and verify sustained transfer under conditions that can legally sustain that rate. Do not equate this with unconditional saturation of both DRAM channels or sum peak AXI read and write rates as if DRAM were full duplex. |
| First delivery | RTL verified against simulation models first | Functional and protocol evidence is required; technology-specific synthesis/timing closure and physical sign-off are not prerequisites for this first release. |
| First-release exclusions | Defer patrol scrubbing, optional RFM extensions, and deep power-down | The latest clarification includes optional RFM in the deferral, with an explicit exception for standard-required behavior. Retain normal power-down, self-refresh, DVFS, inline/link ECC, correction-triggered scrub, and any RFM required by a supported device/operating configuration. |
| Engineering corrections | Proceed with the recommendations | Use the reconciled choices below and resolve detailed correctness contracts from authoritative references and engineering analysis. No previously marked standard assumption becomes confirmed by this general direction. |

## Intended controller

The first release includes AXI4 ingress, programmable address mapping and channel routing, per-channel read/write queues, page-aware scheduling with QoS and bandwidth control, command timing enforcement, DFI command/data paths, and APB control/status registers. Refresh, normal power-down, self-refresh, frequency changes, inline ECC, and link ECC remain in scope. Patrol scrubbing, optional RFM extensions, and deep power-down are excluded from this release. Mandatory refresh management for supported devices remains in scope; the deferral does not authorize operating outside JESD209-5C requirements. Scrubbing a corrected ECC word remains distinct from the excluded periodic patrol engine.

The intended defaults include two AXI ports, two independent x16 channels, one rank per channel, and 32-entry read and write queues. PHY circuitry and PHY training algorithms are outside the base scope; controller-side initialization, training hooks, and handover still need a complete contract.

## Supplied JEDEC reference and review limits

The owner supplied *Low Power Double Data Rate (LPDDR) 5/5X*, JESD209-5C, June 2023: `scratchpad/uploads/cd6d9f71-884f-4f5f-a8c5-2383b8c6cede/JESD209-5C.pdf`. The 676-page PDF is readable and has SHA-256 `76457d4019dc40c57b550c10dc3416cfae4d6ea1984de8f5c42e3e34d00ed031`. It remains an external supplied reference, not a redistributed repository file. Successors with Space access can retrieve that exact Scratchpad reference. Its coverage of LPDDR5X does not expand this controller's LPDDR5 scope.

This pass checked the command truth-table notes, ACT timing, bank modes and BL32 scheduling, WCK synchronization, refresh limits, RFM applicability, and link-ECC responsibilities cited below. Figures 5 and 94 and Table 201 notes were also visually inspected. It is not a complete 676-page compliance review: initialization, all mode registers and speed/density combinations, FSP/DVFS, training, ZQ, electrical timing, and the complete ECC matrices still need systematic checking. Printed page numbers below refer to the standard; PDF page numbers are one-based file pages.

### One-CK commands and bounded ACT pairing

**Observed standard rule:** Section 7.1.1, Table 201 note 1, printed p191 / PDF p229 defines each command over one CK cycle, using CS at the rising edge and CA at both edges. An operation such as ACT or MRW can require multiple commands. The addendum's assumption that every command occupies two CK is therefore incorrect; its slot-budget conclusions and phase mapping cannot be retained as protocol facts.

Table 201 note 4 and section 7.3.1, p226 / PDF p264 require ACT-2 within eight CK of ACT-1 and prohibit issuing another ACT-1 before completing the pair. The addendum A.5 example that sends ACT-2 through the scheduler again needs an explicit deadline guarantee. Figure 94 shows ACT-2 as the origin for tRCD, tRAS, tRC and tRRD, and the destination of the illustrated tRP interval. Timing checks must name the actual command edge, not a generic ACT issue event.

**Recommended correction:** Use CK/edge-aware timing and reserve completion of multi-command operations. Start with adjacent ACT-1/ACT-2 rather than independent re-arbitration. Table 201 note 4 and section 7.3.1 differ in their lists of permitted intervening commands; adjacent pairing avoids depending on that difference. A single scheduler selection may need multiple commands in a DFI cycle. Exact DFI phase representation remains unresolved until the DFI specification is inspected.

### Split BL32 data and completion tracking

**Observed standard rule:** Section 2.2.3.4, Figure 5, pp10–11 / PDF pp48–49 specifies split BL32 transfers in bank-group (BG) mode at WCK:CK 4:1. The first 16 beats are followed by an eight-WCK gap and then the remaining 16 beats. Another bank group's data can occupy the gap. A read issued three CK after a preceding Read32 is prohibited because of data collision; the corresponding write relationship is the same.

**Required refinement:** Keep the 64-byte default, but model payload occupancy separately from command-to-final-beat duration. Interleaving must preserve each command's data, mask, ECC and completion identity. The draft's two-core-cycle BL32 description cannot mean two contiguous cycles for every bank mode. Popping a single read-tracking FIFO entry on an unspecified `dfi_rddata_valid` event is not a complete contract. Determine whether the supported DFI/PHY options expose or reshape the split transfer, then define beat tracking, buffer lifetime and reassembly. Recalculate bandwidth with a legal interleaving schedule; no sustained-throughput result is established by this review.

### WCK synchronization forms

**Observed standard rule:** Section 7.2.1.2, p194 / PDF p232 distinguishes CAS with WS_WR, WS_RD and WS_FS. Write-sync CAS must be immediately followed by WRITE; read-sync CAS must be immediately followed by READ. Fast-sync CAS is standalone and has separate timing. Section 7.2.1.3, p197 / PDF p235 explicitly forbids an intervening command for the write-sync pair.

**Required refinement:** Replace the universal “sync, wait for WCK ready, then issue RD/WR” model in addendum B.3/B.4 with separate sequences for paired read/write sync and standalone fast sync. Preserve pair adjacency even in reduced-throughput 1:4 mode, and track WCK synchronization validity across power transitions and burst completion. The physical WCK rules do not establish the draft's proposed DFI signal names or controller/PHY ownership.

### Refresh granularity, thermal transitions and self-refresh

**Observed standard rule:** Section 7.5.1, Table 237 note 2, p282 / PDF p320 treats the relevant same/different-bank timing relationships as bank-pair relationships in BG/16B mode. Figure 134 note 3, p283 / PDF p321 identifies ACT-2 as the endpoint of the illustrated refresh-to-ACT timing. The refresh owner and timing tracker must use the selected architecture's refresh granularity and exact endpoints.

Sections 7.5.1–7.5.2, pp284–287 / PDF pp322–325 specify rate-dependent postponed/pulled-in limits, including eight at the normal 1× refresh interval but lower limits at some extended intervals. Table 239 also requires postponed debt to decrease monotonically to the new limit during a thermal transition and invalidates old pulled-in credit on entering a new thermal phase. Section 7.5.2 requires at least one extra refresh between self-refresh exit and reentry, in addition to periodic refresh.

**Required refinement:** Replace an unconstrained fixed postpone limit with validated limits for the active refresh policy. Check thermal changes, per-bank sequencing, self-refresh credit persistence, the extra refresh obligation, and refresh protection during PHY updates/DVFS. Test changing rates while debt exists, rather than only steady-state refresh.

### Optional versus required RFM

**Observed standard rule:** Section 7.7.5.1, pp452–454 / PDF pp490–492 makes RFM applicability device- and operating-condition-dependent. MR27 OP[0] reports the requirement; device thresholds and the refresh interval determine whether RFM is needed. The standard defines a refresh-rate exemption using `RFMTH = RAAIMT * tRC absolute min`; this is not a blanket exemption for every device. Where activation counting is required, RAA must not exceed RAAMMT, further ACTs are blocked at the limit, counts cannot become negative, and self-refresh does not clear them. RFM supplements periodic refresh. BG/16B monitoring granularity can be bank pairs or a supported single-bank mode.

**Scope consequence:** The owner's latest answer defers optional RFM extensions, not mandatory protection. Supported device/operating profiles must satisfy the required RFM behavior or a verified standard exemption. An unsupported profile must be rejected before normal traffic; silently disabling required RFM is unacceptable. Freeze capability discovery, thresholds, refresh-rate transitions, granularity and status/error behavior in the configuration contract. Detailed RFM command timing and decrement rules remain to be traced and tested.

### Link ECC needs an explicit datapath owner

**Observed standard rule:** Section 7.7.8, pp468–470 / PDF pp506–508 describes write check bits for each 128-bit data block and separate protection of 16 DMI bits, carried on RDQS_t. Read check bits arrive on DMI. Coding is independent per byte and per 16-beat group, so BL32 has two code sets per byte. Read Link ECC conflicts with Read DBI and Read Data Copy, and requires CAS B3 to be zero. Beat numbering stays associated with the original command when BL32 is split.

**Required refinement:** Base section 5.9's mode-enable/status description is insufficient. Freeze encoder/decoder ownership, DFI transport of check bits or checked data/status, mask/DBI ordering, split-burst identity, capability/speed restrictions and invalid-mode rejection. Do not assume a generic PHY performs all of this. The matrices and their encoding/decoding algorithms require separate validation against independent vectors before ECC correctness can be claimed.

### Bank-mode changes are constrained

Section 2.2.3.3, p8 / PDF p46 gives 16B as the DRAM reset default, selects other organizations through MR3, permits BG↔16B changes through FSP while idle, and requires reset for transitions to or from 8B. Controller configuration and initialization must distinguish the controller's preferred mode from the DRAM reset state and reject unsupported live changes.

## Findings requiring refinement

### Burst size, timing units, and DFI issue width

Base section 2 defaults `BURST_BYTES` to 32; addendum A.1 recommends 64, and C.2 resets `MODE.BURST_SEL` to BL32. The agreed correction is a 64-byte default while retaining 32- and 64-byte transfers. Size buffers for the supported maximum and distinguish that static size from the active burst mode. Mode changes must occur only while disabled or safely quiesced. The inspected JEDEC rules above establish the BG-mode split-transfer constraint; the full mode matrix and DFI mapping must still be checked before freezing the splitter, mask, and data-return contracts.

Base section 5.5 uses phase-aware CK counters; addendum A.1 changes timing accounting to two-CK slots. Addendum A.1 says 1:4 issues two slots per core cycle, whereas B.1 limits v0.1 to one command per core cycle. The owner's reduced-throughput decision resolves the scope choice, but counter progression, command placement, and data timing still need one consistent model. A single command lane must not imply incorrectly halving the passage of DRAM time at 1:4.

JEDEC now resolves the command-duration question: commands are one CK, with separate rules for multi-command operations. DFI phase alignment remains unresolved. Rebuild the slot-budget calculation from those separate facts rather than retaining the two-CK representation. Reduced throughput never permits violating command-pair adjacency or maximum spacing.

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

- An authorized DFI 5.0 copy or access, plus inspection of the relevant AXI/APB references and revisions. JESD209-5C is supplied; complete the remaining protocol traceability against it. Only the specific findings above are checked, not every `[VERIFY]` item.
- A generic DFI/DRAM verification strategy and accessible models or VIP. The repository currently contains none. Define supported DFI options, configurable latency bounds, and responsibility for PHY training without assuming a vendor-specific model.
- Quantified throughput, latency, fairness, and PPA evaluation conditions. Preserve technology independence; state the limits of any technology-independent or generic-library estimate.
- A verified configuration matrix. The owner has resolved the optional-feature scope above. Choose queue/ECC-cache sizes and other reversible implementation details through analysis within that scope.

The [DFI publisher's site](https://ddr-phy.org/) was inspected on 2026-11-05. Its [specification download link](https://ddr-phy.org/pages/2351641-Page-301) redirected to sign-in, and no authorized credential profile was available in this session. The public announcement is useful context but cannot validate the detailed protocol. An authorized standard copy or access is still needed. JESD209-5C is now supplied as recorded above. AXI/APB normative references and an independent LPDDR5 model have not yet been inspected; the repository contains no such model.

These confirmed directions can be captured in a working Definition. It must remain incomplete until the required protocol references are inspected, the verification source is established, and the outstanding interface and acceptance details above are resolved. RTL execution has not started.
