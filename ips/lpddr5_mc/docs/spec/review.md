# LPDDR5 draft review and scope decisions

Reviewed on 2026-11-05 against the [base specification](lpddr5_mc_microarch_spec.md) and [v0.2 addendum](lpddr5_mc_microarch_addendum_v0.2.md). Their original digests are recorded in [SHA256SUMS](SHA256SUMS). The repository baseline for this update is `240f9fdd13e8394c0b41ea092477188413816fe2`.

This is a source-document review and a record of the owner's direction. There is no controller implementation to verify yet. Supplied JESD209-5C and DFI 5.0 references have been inspected in the targeted sections listed below. The DFI copy resolves several questions but exposes clocking contradictions and incomplete timing entries that prevent freezing the interface. The owner accepted the recommended corrections on 2026-11-05, with the throughput and release-scope clarifications below. Acceptance of that direction does not establish a complete interface specification or verified hardware behavior. The imported drafts remain unchanged references; this page records how they must be refined.

## Owner decisions

| Topic | Confirmed direction on 2026-11-05 | Consequence for refinement |
| --- | --- | --- |
| PHY | Generic PHY-facing controller using the standard DFI interface | Retain DFI 5.0 as the intended basis while reconciling the supplied copy and its incomplete entries; define configurable timing and supported options without coupling the controller to one PHY vendor. Vendor qualification remains separate evidence. |
| Technology and PPA | Keep the implementation independent of technology node and pursue the best practical power, performance, and area (PPA) | Optimize architecture and portable RTL. Assess resource costs and critical paths; report any later synthesis measurements with their tool, target, and constraints. No absolute physical PPA number or technology-node sign-off is agreed. |
| DFI throughput | Full throughput at 1:2; reduced throughput at 1:4 is acceptable, using the original draft terminology | Preserve this performance intent, but correct the clock-domain terminology: the draft uses core:CK, whereas supplied DFI 5.0 uses DFI:WCK for LPDDR5 and keeps DFI clock equal to CK. A slower internal scheduler plus a CK-rate DFI boundary is a proposal pending contract and throughput analysis. |
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

**Recommended correction:** Use CK/edge-aware timing and reserve completion of multi-command operations. Start with adjacent ACT-1/ACT-2 rather than independent re-arbitration. Table 201 note 4 and section 7.3.1 differ in their lists of permitted intervening commands; adjacent pairing avoids depending on that difference. A single slower-scheduler selection may need to reserve multiple external command cycles. The supplied DFI copy describes one 14-bit CA word per CK-rate DFI cycle, as detailed below; the slower-core bridge contract remains unresolved.

### Split BL32 data and completion tracking

**Observed standard rule:** Section 2.2.3.4, Figure 5, pp10–11 / PDF pp48–49 specifies split BL32 transfers in bank-group (BG) mode at WCK:CK 4:1. The first 16 beats are followed by an eight-WCK gap and then the remaining 16 beats. Another bank group's data can occupy the gap. A read issued three CK after a preceding Read32 is prohibited because of data collision; the corresponding write relationship is the same.

**Required refinement:** Keep the 64-byte default, but model payload occupancy separately from command-to-final-beat duration. Interleaving must preserve each command's data, mask, ECC and completion identity. The draft's two-core-cycle BL32 description cannot mean two contiguous cycles for every bank mode. Popping a single read-tracking FIFO entry on an unspecified `dfi_rddata_valid` event is not a complete contract. Determine whether the supported DFI/PHY options expose or reshape the split transfer, then define beat tracking, buffer lifetime and reassembly. Recalculate bandwidth with a legal interleaving schedule; no sustained-throughput result is established by this review.

### WCK synchronization forms

**Observed standard rule:** Section 7.2.1.2, p194 / PDF p232 distinguishes CAS with WS_WR, WS_RD and WS_FS. Write-sync CAS must be immediately followed by WRITE; read-sync CAS must be immediately followed by READ. Fast-sync CAS is standalone and has separate timing. Section 7.2.1.3, p197 / PDF p235 explicitly forbids an intervening command for the write-sync pair.

**Required refinement:** Replace the universal “sync, wait for WCK ready, then issue RD/WR” model in addendum B.3/B.4 with separate sequences for paired read/write sync and standalone fast sync. Preserve pair adjacency even in reduced-throughput 1:4 mode, and track WCK synchronization validity across power transitions and burst completion. The physical WCK rules alone do not establish DFI ownership. The supplied DFI copy confirms the three signal names and their encoding below, but leaves timing bounds incomplete.

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

## Supplied DFI reference and review limits

Ajith Jose supplied *DDR PHY Interface, DFI 5.0 Specification*, cover date April 27, 2018, as `scratchpad/uploads/ea823e67-d6fb-47f7-841a-3bc10b90b924/774634393-DDR-PHY-Interface-Specification-v5-0.pdf`. The readable 160-page PDF has SHA-256 `ffe2e764fce70dae4ec8e82ccdcb762acb5e3ea0cb3fe776eaf3fd0df56a84ec`. A companion text file at the same upload path with `.txt` extension has SHA-256 `0879f290f1f8f86ca7d44125e6730e134348f4316974bbdb84db03a008956362`. The PDF was the review source; its tables and figures on pages 33, 38, 72 and 148 were also visually inspected. Both uploads remain external references, not redistributed repository files. Printed and PDF page numbers coincide.

This pass inspected command packing and clock definitions, data timing/validity/rotation, update and ownership handshakes, initialization, frequency change, low power and WCK control. It did not qualify a PHY or exhaustively validate all 160 pages. The cover and page footers identify version 5.0; embedded PDF metadata still says 3.1. That metadata discrepancy alone does not invalidate the contents, but Table 39 on pp72–73 visibly contains `?` minimum/maximum WCK timing entries. Section 3.1.1 calls LPDDR5 CA DDR while the glossary on p158 calls it SDR; JESD209-5C and the explicit 14-bit mapping support the former. Table 17 and section 4.9.1 also differ on the update-acknowledgement window, as recorded below. Treat this as an inspected supplied reference with unresolved entries, not a fully reconciled normative baseline.

### LPDDR5 clock ratio and command packing

**Observed in the supplied DFI copy:** Section 3.1.1, Table 7, p33 packs one LPDDR5 command into a single-phase 14-bit `dfi_address`: rising-edge CA in bits [6:0], falling-edge CA in bits [13:7]. Table 10, p38 explicitly defines LPDDR5 `dfifreq_ratio` as DFI clock:WCK and DFI clock:CK as 1:1. Section 4.10, p108 distinguishes command and data clocks and repeats that separate-clock memories keep the command clock equal to DFI clock. Generic multi-phase command examples for other memory clock arrangements must not override that LPDDR5-specific rule.

**Consequence:** The base's 400 MHz core for CK=800 MHz and its 200 MHz alternative cannot directly be the DFI boundary described by this copy. At the illustrative LPDDR5-6400 point, WCK=3200 MHz and CK=800 MHz imply an 800 MHz DFI clock and DFI:WCK=1:4. Calling a 400 MHz CK/2 boundary “DFI 1:2” is not supported by these clauses. This is a correction to the earlier provisional review as well as to addendum B.1.

**Recommended architecture, not a frozen agreement:** Separate internal scheduler:CK ratios from external DFI:WCK ratios. Evaluate a 400 MHz scheduler (and a reduced-service 200 MHz profile) feeding a shallow 800 MHz command/timing and data adapter. Reserve complete ACT and sync pairs, transfer sufficient look-ahead command information, recheck live legality at issue, and return state/credits across the boundary. A FIFO alone does not establish throughput or resolve feedback latency. Also evaluate a single CK-rate memory engine if the split adds unnecessary complexity. Preserve the two-port AXI payload target and quantify each profile before confirming the clock contract; no 800 MHz physical timing closure is claimed.

For x16 and DFI:WCK=1:4, the ordinary two-beat DFI data word is 32 payload bits; four such words give 128 payload bits per DFI cycle (sections 3.2.4 and 4.10.3; glossary p158). At 800 MHz that is 12.8 GB/s per channel, matching the raw pin calculation. Two 128-bit AXI ports at 400 MHz offer 12.8 GB/s combined; two 256-bit ports at that clock offer 25.6 GB/s. These are capacity calculations, not sustained results; BL32 interleaving, ECC and maintenance still constrain the legal schedule.

### Timing units and read completion

Tables 9, 12, 15 and 18, pp37, 41–42, 46 and 50–51 distinguish command-clock, data-clock and DFI-clock units and identify whether the MC, PHY or system defines each parameter. Addendum B.2's blanket “DFI cycles” and “PHY supplies these” rules are insufficient. Preserve unit and owner metadata in the canonical timing schema; convert with explicit phase/edge semantics, including the `tcmd_lat` relationship where supported. Data parameters cannot simply be counted in slower scheduler cycles.

Sections 3.3.3–3.3.4, pp43–45 permit independent per-slice read returns and nonconsecutive valid cycles within `tphy_rdlat`, with one-to-one enable/valid transfer accounting. Section 4.10.4, p117 requires rotating read-word handling when transfers do not fill the full DFI bus, and resets the word pointer on `dfi_init_start`. Retire a request only after all of its expected words and slices are assembled. Qualify this together with JEDEC split BL32: the generic examples do not themselves specify whether an LPDDR5 PHY reshapes interleaving. A full-bus return profile may simplify rotation only when the supported transfer matrix proves that assumption.

### Updates require idle data paths and correct acknowledgement order

Sections 3.4 and 4.9, pp47–51 and 105–107 require a DFI idle window, including completed writes at the DRAM boundary; `twrdata_delay` participates in the drain condition. Normal ACT/PRE traffic is not permitted during an asserted controller-update window or acknowledged PHY-update window. Any preparatory traffic must complete before ownership transfer. A controller update is an invitation: the PHY may ignore it. If acknowledged, the MC holds its request while acknowledgement is high and waits for acknowledgement to drop before normal completion. The draft's “wait for ack, deassert request, resume” ordering is incorrect for this handshake.

Table 17, p48 requires acknowledgement before `tctrlupd_min`; section 4.9.1, p105 instead describes acknowledgement before `tctrlupd_max`. This is an unresolved inconsistency in the supplied copy. A conservative proposed integration profile requires a participating PHY to acknowledge within the earlier window, but that proposal is not evidence that every PHY does so. Obtain release/errata clarification before claiming generic compliance for this corner.

For PHY updates, use `tphyupd_resp` and the selected `tphyupd_type0..3` bound, include simultaneous-request arbitration, and do not assert `dfi_ctrlupd_req` and `dfi_phyupd_ack` together. Section 3.4 also requires a controller-update request immediately before self-refresh exit. Refresh admission must account for the complete pause, not just detect exhausted credits afterward. Fault recovery and negotiated disconnect behavior still need a complete contract.

### Boot, training and frequency-change ownership

Sections 4.1–4.2, pp75–77 describe PHY-independent training: the PHY performs memory boot/training before asserting `dfi_init_complete`, and transfers known memory state specified by `dfiboot_state`. Refresh starts without credit or debt. The draft must not unconditionally replay a DRAM reset/init sequence after that handover. Keep controller configuration, command hooks and management in scope, but choose and document one coherent boot/refresh ownership contract. Section 4.15, pp138–140 separately describes runtime PHY-master handover; the MC still supplies required refresh commands unless memory was handed over in self-refresh, with the PHY honoring the corresponding forwarding/timing bound.

Section 4.11, pp128–130 requires the PHY to acknowledge frequency change by taking `dfi_init_complete` low within `tinit_start`. Only then does an accepted change proceed; the MC holds the settings stable, completes the clock change, drops `dfi_init_start`, and waits for completion to return high within `tinit_complete`. A request may be ignored and must then be withdrawn. A stale high completion signal is not a new completion. Replace the addendum's simplified sequence and test declined, delayed and failed transitions without losing refresh or queued work.

Section 4.13, p133 similarly permits the PHY to decline a low-power opportunity. Control/data wakeup hints and handshakes are distinct; the hints are not by themselves a hard failure timeout. Define controller recovery bounds separately instead of treating every ignored opportunity as a PHY fault.

### WCK signals are present; some limits remain unresolved

Section 3.12, Tables 38–39, pp72–73 confirms `dfi_wck_en`, `dfi_wck_cs` and `dfi_wck_toggle`, with phase replication and a two-bit toggle encoding for static low, static high, toggle and fast-toggle. This resolves the prior signal-name uncertainty, but not the `?` timing bounds in Table 39. Parameterize only after documenting permitted ranges and their JEDEC/PHY basis; do not translate question marks into invented constants.

Section 4.19, pp147–148 requires WCK disabled at DFI ownership transfer, including initialization, frequency change, PHY master and updates, even for free-running operation. The controller's always-on policy must yield to those transitions and resynchronize when needed. Figure 81 illustrates adjacent sync CAS and READ, consistent with the JEDEC pairing correction.

### Link ECC remains an integration gap

The inspected read/write signal tables define data, mask/DBI, valid and data-not-valid behavior. Generic ECC mentions on pp55 and 62 do not define the LPDDR5 link-ECC transport and encoder/decoder ownership needed by JESD209-5C. A text search found no explicit link-ECC protocol in this copy. Do not equate the optional `dfi_error` interface with command-correlated ECC completion or assume DBI signals may carry ECC under an undocumented convention. Preserve link ECC in scope, but establish a documented PHY-facing capability contract or obtain the applicable DFI clarification before freezing this part of the interface.

## Findings requiring refinement

### Burst size, timing units, and DFI issue width

Base section 2 defaults `BURST_BYTES` to 32; addendum A.1 recommends 64, and C.2 resets `MODE.BURST_SEL` to BL32. The agreed correction is a 64-byte default while retaining 32- and 64-byte transfers. Size buffers for the supported maximum and distinguish that static size from the active burst mode. Mode changes must occur only while disabled or safely quiesced. The inspected JEDEC rules above establish the BG-mode split-transfer constraint; the full mode matrix and DFI mapping must still be checked before freezing the splitter, mask, and data-return contracts.

Base section 5.5 uses phase-aware CK counters; addendum A.1 changes timing accounting to two-CK slots. Addendum A.1 says 1:4 issues two slots per core cycle, whereas B.1 limits v0.1 to one command per core cycle. The owner's reduced-throughput decision resolves the scope choice, but counter progression, command placement, and data timing still need one consistent model. A single command lane must not imply incorrectly halving the passage of DRAM time at 1:4.

JEDEC now resolves the command-duration question: commands are one CK, with separate rules for multi-command operations. The supplied DFI copy also changes the assumed clock boundary and packs both CA edges into one address word. Rebuild the command budget and internal-to-DFI timing from those facts rather than retaining the two-CK representation. Reduced throughput never permits violating command-pair adjacency or maximum spacing.

### End-to-end bandwidth

Base section 2.1 gives 12.8 GB/s raw bandwidth per x16 channel, or 25.6 GB/s across two channels. **If** both default 128-bit AXI ports run at the proposed 400 MHz core clock, their aggregate payload limit is only `2 * 128 / 8 * 400 MHz = 12.8 GB/s` per direction, before protocol overhead. The drafts allow independent AXI clocks, so this is a configuration constraint rather than an unconditional design defect.

The owner's clarification makes combined AXI payload bandwidth the reference, rather than requiring every configuration to saturate both DRAM channels. Retain the draft's 128-bit default. Two 256-bit ports remain an allowed higher-bandwidth configuration; widening is not required merely to compensate for a configuration that already meets its host rate.

For same-direction traffic, calculate `B_AXI = sum(AXI_DATA_W[p] / 8 * f_AXI[p])` and compare it with the usable DFI payload rate across the enabled channels. Calculate payload transferred per DFI cycle and per internal core cycle separately, without double-counting phases. The supplied DFI copy establishes the basic CA/data width conventions above; the slower-core bridge and LPDDR5 burst/capability profile remain unqualified. At the illustrative 400 MHz AXI clock, two 128-bit ports require 12.8 GB/s; two 256-bit ports require 25.6 GB/s. These are calculated interface rates, not measured sustained memory bandwidth. The chosen channel count, rank/bank distribution, and clock ratios must make a claimed full-rate configuration feasible.

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

- Resolve the supplied DFI copy's WCK timing placeholders and update-acknowledgement inconsistency through a released reference/errata or an explicitly justified compatible integration contract. Complete JEDEC/DFI traceability and inspect relevant AXI/APB references; receipt of both PDFs does not close every `[VERIFY]` item.
- Freeze distinct scheduler, DFI, CK and WCK clock contracts and qualify the proposed adapter. Preserve the original higher-throughput/reduced-throughput intent without treating core:CK and DFI:WCK as interchangeable.
- A generic DFI/DRAM verification strategy and accessible models or VIP. The repository currently contains none. Define supported DFI options, configurable latency bounds, and responsibility for PHY training without assuming a vendor-specific model.
- Quantified throughput, latency, fairness, and PPA evaluation conditions. Preserve technology independence; state the limits of any technology-independent or generic-library estimate.
- A verified configuration matrix. The owner has resolved the optional-feature scope above. Choose queue/ECC-cache sizes and other reversible implementation details through analysis within that scope.

Both requested PDFs are now supplied. The [DFI publisher's download page](https://ddr-phy.org/pages/2351641-Page-301) was rechecked on 2026-11-05 and still redirected to sign-in; no authorized credential profile was available to inspect a released copy or errata there. The inspected upload supports the findings above but does not resolve its own incomplete entries. Relevant AXI/APB normative references and an independent LPDDR5 model have not yet been inspected; the repository contains no such model.

These confirmed directions can be captured in a working Definition. It must remain incomplete until the required protocol references are inspected, the verification source is established, and the outstanding interface and acceptance details above are resolved. RTL execution has not started.
