# LPDDR5 controller contract investigation

This is an incomplete engineering contribution, not an interface freeze or a simulation-qualified controller. It preserves the original drafts and adds executable reference experiments. The protocol basis, frozen interfaces and performance acceptance remain unconfirmed. Dependent production RTL must wait for the missing evidence below and the corrected implementation agreement.

## Evidence and remaining work

| Topic | Present evidence | What still prevents qualification |
| --- | --- | --- |
| Source | Exact documentation/scaffold history integrated through `252648f79786d740c6518abfdb58a325e70af500`; three accepted document digests checked | No controller source exists in that baseline |
| Numeric WCK | Three explicit reference cases; paired read/write event boundaries, an ideal trained edge pattern, BL16/BL32 completion, half-WCK postamble and invalid representation checks | Fast-sync sequences, synchronization reuse/expiry, static restart, chip-select changes, complete PHY command/WCK/data path qualification, and ownership-transfer waveforms |
| Controller update | Executable sampled-edge oracle for ignored requests, early acknowledgement, boundary/late/stuck faults, release margin and admission | An actual handshake implementation, competing PHY requests, physical write drain, timed refresh return and fault recovery in a multi-clock system |
| Link ECC | Tables 331/332 transcribed and visually checked; matrix reference, all single/double column faults, seeded payloads and parity beat placement | DBI/masking transformations, full x16 split-burst transport, actual PHY extension, write error reporting and end-to-end acknowledgement lifetimes |
| Read completion | Independent word/slice expected streams; split halves interleaved across two tags, actual returned word-lane rotation, skew, gaps, DNV, resynchronization and missing/extra/early/late valid checks | Derive enables from a qualified PHY/data path; a supplied expected timeline is not a qualified DFI adapter |
| DVFS | Sampled low-then-high acknowledgement, declined request, stable settings, registered release, WCK-off transfer and deliberate handshake faults | JEDEC FSP changes, actual clock switching/gating, elapsed retention budget, atomic profile publication and end-to-end outstanding-transaction disposition |
| Bandwidth | Finite pre-opened same-direction BG BL32 schedules checked for spacing, pin collisions and duplicate identity | A complete periodic schedule with refresh, row boundaries, synchronization expiry, admission/feedback buffering, actual AXI/DFI traffic and measured tolerances |
| Internal representation | Draft SystemVerilog types and representation testbench | Complete consumer interfaces, clock/reset/CDC implementation, configuration/register source and generated RTL/software/docs |
| Host/software protocols | Official Arm references retrieved; relevant burst, error, exclusive and APB rules inspected | Executable host/CSR models and complete clause-to-contract coverage |

Passing the owning check command means these available experiments pass. It cannot close the work above or any final controller requirement. The detailed [reproduction guide](../verification/contracts.md) describes the exact scope of each check.

## Source basis

The working source began at `b46072841f1a8e89bb110ee2ed578af6fc80dd0a`, whose only tracked file was an empty README. The exact retained shaping commit was present in its Git object database. After verifying ancestry, the 18-file diff and the accepted document digests, it was integrated with `git merge --ff-only 252648f79786d740c6518abfdb58a325e70af500`. This preserves all five shaping commits and uses no moving remote branch. The two original specifications remain byte-for-byte intact under [SHA256SUMS](SHA256SUMS); the retained review has digest `d5b1e4d85631a755f0b47134c2b8b42aeea55ee52d45d1ce2fb15677968c3c4c`.

The external references and hashes are recorded in [reference-sources.json](../../config/reference/reference-sources.json). PDF page numbers below are one-based. The supplied standards remain external references; they are not redistributed in this repository. These are targeted inspections, not exhaustive conformance reviews.

## Numeric WCK experiments

[wck_profiles.json](../../config/reference/wck_profiles.json) is the input to the DFI-offset experiment. It contains all ten Table 39 offsets as unsigned 16-bit WCK-cycle values. Half-WCK periods are the common model time unit. A CK equals `2 * WCK:CK` such units. Missing fields, nonintegers, negative values, overflow, unsupported bank/ratio combinations and inconsistent event ordering reject the experiment. There are no runtime defaults or clamping.

| Case | WCK:CK | CK / WCK MHz | RL / WL CK | Read / write / fast enable CK | Static CK | Read / write toggle CK |
| --- | --- | --- | --- | --- | --- | --- |
| 6400 BG, link off | 4:1 | 800 / 3200 | 17 / 9 | 7 / 4 / 2 | 4 | 7 / 2 |
| 6400 BG, read link on | 4:1 | 800 / 3200 | 19 / 9 | 9 / 4 / 2 | 4 | 7 / 2 |
| 3200 16B, link off | 2:1 | 800 / 1600 | 18 / 10 | 5 / 3 / 2 | 4 | 10 / 4 |

The values are from JESD209-5C Tables 204, 205, 208 and 209, printed pp198, 200, 203 and 206 / PDF pp236, 238, 241 and 244. The no-link cases use RL Set 0 and WL Set A, full x16, no read DBI, byte mode, data copy or voltage-scaling mode. Read link ECC uses Table 208 Set 0. Table 208 has no 2:1 row; this experiment rejects read link ECC at 2:1. The MR fields included in the JSON identify part of the intended setup; they do not constitute a complete qualified MR image.

Both model command and WCK paths are explicitly assigned two CK of latency. This equality is an illustrative model choice. It makes the DFI enable offsets equal to the selected pin enable interval times the ratio. Changing only the WCK path by half a WCK fails the independent pin-boundary check. A second test shortens the WCK path by one WCK and increases the read/write/fast-enable and disable offsets by one WCK; both derived pin timelines remain identical. This checks the path-compensation equation for the paired cases, without qualifying a full PHY data path. The checker has its own standard-derived rows and does not import the DFI derivation algorithm.

For paired synchronization, CAS is followed by READ or WRITE exactly one CK later. The ideal trained fixture starts half-rate toggling after the selected static preamble, stays at half rate for one CK, then toggles at full rate through the final data half and postamble. The fixed read/write preamble durations follow sections 7.2.1.2–7.2.1.4. The edge checker checks an ideal zero trained phase; it does not establish analog setup/hold, jitter or real training behavior.

MR10 OP[3:2]=00 selects 2.5 WCK of postamble (Table 82). The experiment rounds this minimum upward to three whole DFI data clocks. Its oracle retains the original five half-WCK bound and rejects a two-WCK postamble. A later disable is represented separately from the last toggle. `twck_dis` covers the experiment's largest read/write latency and BL32 span; it is not a universal value for any PHY or operating point.

`twck_toggle_cs`, `twck_toggle_rd` and `twck_toggle_wr` are explicitly zero because the experiment has one rank and never restarts toggling from a synchronized static state. Those transitions are outside its coverage. `twck_en_fs` has a sourced numeric row but no fast-sync sequence qualification. None of these zeroes establishes an implementation contract for an unsupported transition.

All profiles have `integration_qualified=false`. `require_integration_qualification` always rejects ordinary traffic in this contribution. The remaining qualification must include complete data-path delays, read enable/valid timing, CS ownership, reset, mode changes and independent complete waveforms. A user-provided boolean must never substitute for that evidence.

## Update and ownership contract

The executable update oracle implements the [selected conservative policy](review.md#controller-update-window). Request edge zero is the edge after which request becomes high. Inputs are sampled at subsequent numbered edges. With minimum `m` and maximum `M`, a valid acknowledgement rises at `1 <= a < m`, falls at `a < b <= M-2`, and request falls after edge `max(m, b+1)`. With no acknowledgement, request falls after edge `m`. The reference requires `m >= 2` and `M >= m+2` for registered release margin.

An acknowledgement at edge `m`, any later unsolicited acknowledgement, acknowledgement high at edge zero, reassertion, or completion after `M-2` is a fault. A fault result never allows traffic. An ignored opportunity is normal. These are chosen integration restrictions resolving the source discrepancy between DFI Table 17/Figure 33 and section 4.9.1; they are not publisher corrections.

Admission requires completed DFI and DRAM writes, disabled WCK, and enough refresh budget for the maximum pause plus return-to-refresh. The oracle tests these as supplied predicates and numbers; it does not prove that hardware computed them correctly. DFI sections 3.4 and 4.9 require actual DRAM write completion, including `twrdata_delay`. They forbid normal ACT/PRE during ownership transfer. Section 4.19 requires WCK disabled for initialization, frequency changes, PHY-master, PHY-update and controller-update transfer.

The proposed fault behavior is to latch the fault, stop ordinary admission/commands, retain accepted-transaction metadata, and keep DFI idle while ownership is uncertain. Timer expiry is not ownership recovery. A retention deadline that is lost marks memory untrusted. Recovery requires an explicit external reset/reinitialization sequence; it must not promise preservation. These failure semantics still need executable lifecycle and host-response checks before freeze.

## DFI read return and DVFS experiments

The read-return oracle now accepts actual CK snapshots with two or four returned word lanes. Each lane contains two independently valid byte slices of 16 bits, corresponding to two memory beats. For each slice, asserted lanes must form a contiguous prefix in cyclic order starting at its previous pointer. Every word consumes its independently supplied expected enable identity and deadline, including interleaved split-BL32 halves. All lanes are sampled at the CK edge; the checker does not pretend that successive returned lanes arrived at separate WCK edges inside that cycle. Enable/deadline timestamps use WCK units and convert the CK sample explicitly. DNV consumes the identity but records unavailable data for later host-response checks. A rejected snapshot consumes nothing.

DFI sections 4.10.4.1–4.10.4.2, PDF pp116–117 and 127 require rolling word order across gaps and transaction boundaries and pointer reset on initialization, `dfi_init_start`, `dfi_ctrlupd_req` or `dfi_phyupd_ack`. The model checks two- and four-word buses, skewed slices, wraparound, partial-word returns and a single-word fixture representing the short-return case needed for mode-register reads. The fixture does not qualify JEDEC MRR pin/data formatting. Resynchronization requires draining the model first; an actual fatal abort must explicitly settle or invalidate every outstanding identity. Timer expiry is rejected as a resynchronization event. The model still needs independently qualified read-enable generation and full PHY timing before it can qualify a transport.

The frequency-change oracle checks DFI section 4.11, PDF pp127–129. Edge zero asserts `dfi_init_start` while completion is high. The selected sampled profile requires a low acknowledgement at `1 <= a < tinit_start`, holds request for at least `tinit_start`, and retains the same frequency/ratio/FSP settings throughout request assertion. The clock-change-complete predicate may become true only after acknowledgement. The request releases after that predicate, and completion must remain low through release and return high on a subsequent sampled edge within `tinit_complete`. An unchanged high completion declines the request and never activates the target profile. Invalid release, changed settings, early high, late/stuck acknowledgement, request reassertion or enabled WCK at transfer rejects target activation. This profile chooses a registered low-then-high sequence; it is not an analog clock-switch or retention model. The `drained` and clock-change predicates are test inputs, not proved implementations.

## ECC transport and acknowledgement proposal

The link code reference operates independently on each byte and each 16-beat group. Its data code is 128 payload bits plus nine check bits, and its DMI code is 16 bits plus six check bits. It follows JESD209-5C sections 7.7.8.1–7.7.8.4: lower check bits are calculated before overall parity. A nonzero syndrome with overall parity set is a single-bit error; a nonzero syndrome without it is a double-bit error. Guarantees are limited to single-bit correction and double-bit detection; higher multiplicity is outside SECDED guarantees.

On writes, parity beat zero is low; DMI C0–C5 occupy beats 1–6 and data C0–C8 occupy beats 7–15. On reads, parity beats 0–6 are low and data C0–C8 occupy beats 7–15. A BL32 command has two groups per byte, retaining its original beat numbering across the split. The matrix tests exercise the one-group code and pin packing; they do not exercise a full two-byte transport or DBI/masked-write transformation.

The recommended controller-facing contract is explicit raw parity transport: the controller owns write encoding and read checking/correction, while a capable PHY serializes/deserializes the parity pins without changing codeword identity. This requires a documented extension carrying per-word, per-byte write RDQS_t parity and read DMI parity with the exact data timing. This is an integration extension, not a signal claimed to exist in DFI 5.0. Its widths, enable/valid semantics and partial-word handling remain to be frozen. A PHY without the capability must reject link mode before enabling traffic. Read DBI, read data copy and nonzero read CAS B3 are incompatible with read link ECC under the inspected rules.

The supplied standard reports DRAM write link errors through MR43–MR45. It does not provide a per-write DFI acknowledgement. Generic `dfi_error` must not be treated as a correlated ECC completion. A delayed DRAM error cannot retract an earlier AXI B response or be assigned to a guessed AXI ID. The proposed late-error record is channel/rank scoped and explicitly uncorrelated; it latches an integrity fault and blocks new traffic pending recovery. The exact polling bound and treatment of outstanding/previously acknowledged writes remain qualification blockers.

For inline ECC, the proposed default is 64 data bits plus eight check bits. Check storage is one eighth of protected data or one ninth of combined storage before allocation rounding. Partial writes need corrected old data and a successful data/check commit before a strict B response. Correction-triggered scrub retains a version dependency so that a younger write cannot be overwritten by stale corrected data. Dirty ECC metadata prevents drain completion. The ECC cache mapping, atomicity/failure recovery and optional posting policy are not encoded or tested in this contribution.

## Host and APB refinement

The official Arm AXI basis is IHI 0022H, with AXI4 semantics. Targeted inspection covered A3.4.1 (4KB boundary, INCR up to 256 transfers, other bursts up to 16, WRAP lengths 2/4/8/16), A3.4.4–A3.4.5 (byte strobes and complete error responses), and A7.2 (exclusive access). An implementation without exclusive monitors may ignore AxLOCK, return OKAY for successful access, and perform the write. It must not report EXOKAY. This is supported by A7.2.3 and A7.2.5; the shorthand “exclusive failed” must not be mistaken for suppressing memory side effects on a slave that has no exclusive support.

The proposed host order is a monotonically assigned admission sequence across ports, with a fixed same-edge tie rule and per-ID response order. AW must reserve its hazard before accepting a younger overlapping read, even if W data has not arrived. Queue retirement at CAS cannot release write data, response tracking, ECC dependencies or pending split-read words. Complete burst address validation must precede memory side effects for an address-decode error. Exact tie order, overlap forwarding, resource reservation, malformed traffic policy and per-channel/global ordering remain to be modeled and frozen.

The proposed fatal-recovery response completes accepted reads with the original beat count and IDs, zero data and SLVERR for unavailable beats. Already delivered responses cannot be retracted. Accepted writes still consume their remaining W beats and produce one B response. Bounded completion is conditional on the master supplying W and the response sink accepting R/B. A timeout cannot fabricate those handshakes. No new AW/AR is accepted while in the fatal state. These are proposals awaiting host model tests.

The APB proposal selects the APB4 signal subset in IHI 0024E: 32-bit data, PSTRB, PPROT, PREADY and PSLVERR; no APB5 wakeup/user/parity additions. Section 3.2.1 specifies byte strobes and requires read PSTRB low. Section 3.4 makes PSLVERR valid only on the completing access and permits either side-effect behavior on an error. For this controller choose no side effects for unmapped, misaligned, locked or invalid writes, with zero read data on errors. These stricter peripheral semantics must be checked in the eventual CSR implementation.

A canonical CSR/configuration source remains missing. Required corrections include full 16-bit AXI IDs and 40-bit error addresses, separate TRAIN request/completion/clear semantics, all DFI/WCK timings with units and owners, atomic quiesced profile publication, byte enables, broadcast validation, locks, reset values and CDC acknowledgements. A register-address constants file alone would not close these obligations.

## Maintenance and supported modes

No complete device/speed/density/capability matrix is admitted by these experiments. The test fixtures cover one rank, one full x16 byte-pair, BG4/6400 and 16B2/3200, with 32/64-byte bursts. They do not remove the intended one/two-port and one/two-channel capability or required inline/link ECC, PM and DVFS outcomes from scope. Unsupported or unqualified runtime profiles must remain disabled.

JESD209-5C section 7.7.5.1 reports mandatory RFM through MR27 OP[0], specifies nonnegative ACT counts, saturating protection at RAAMMT, REF/RFM decrements and preservation across self-refresh. Table 317 specifies bank-pair versus single-bank granularity. This inspection does not establish a valid exemption for a device. Device discovery, thresholds, granularity, timing and thermal transitions must be validated; no blanket no-RFM profile is delivered.

Refresh qualification still needs Table 237 endpoints/bank pairs, Table 239 thermal debt transitions, temperature-dependent limits and the extra refresh between self-refresh exit and reentry. Boot must choose the PHY-independent DFI ownership flow and validate `dfiboot_state` before starting refresh with zero debt/credit. DVFS needs a low-then-high `dfi_init_complete` sequence, declined requests, clock switching, refresh budget and atomic target-profile activation. Normal power-down/self-refresh and runtime PHY-master handover require their own timed ownership and preservation checks. None is implied by the isolated update oracle.

## Next engineering result

Complete and independently qualify the missing profile/lifecycle/host/register contracts on this foundation, then return a corrected Definition for owner review. Keep `protocol_basis`, `frozen_contracts` and `performance_evidence` unconfirmed until that evidence exists. The [clocking analysis](../architecture/clocking.md) is a starting recommendation, not an accepted implementation restriction. No final functional, protocol, ECC, lifecycle, throughput or release check has been completed by this contribution.
