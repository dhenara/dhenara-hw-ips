# Numeric PHY profiles and traceability

[profiles.json](../../config/contracts/profiles.json) is the complete input for the restricted digital fixtures. `profiles.admit` checks representation, devices, MRs, capabilities and timing minima; `waveform_oracle.check` independently checks the emitted DRAM-side waveform and DFI data path. Admission alone never authorizes traffic. An integration must qualify its actual path and publish the checked profile atomically while quiesced.

## Operating matrix

All fixtures have CK=1,250 ps, full x16, maximum two identical ranks, MR8=0x10 (8 Gb x16), MR27=0x45 and paired-bank MR57=0. MR27 admits mandatory RFM; no exemption is assumed. Read and write data mask is enabled, MR13=0x20. MR28=0 selects background ZQ calibration. NZQ is at most four. ODT, training mode, enhanced NT-ODT, data copy and DVFSC are off.

| Rate / mode | Ratio | RL / WL CK | Read/write/fast WCK enable CK | Static / half-rate CK | MR1 / MR2 / base MR3 / base MR18 |
| --- | --- | --- | --- | --- | --- |
| 6400 BG, read link off | 4 | 17 / 9 | 7 / 4 / 2 | 4 / 1 | B0 / BB / 06 / 08 |
| 6400 BG, read link on | 4 | 19 / 9 | 9 / 4 / 2 | 4 / 1 | B0 / BB / 06 / 08 |
| 3200 16B, link off | 2 | 18 / 10 | 5 / 3 / 2 | 4 / 1 | 50 / 55 / 16 / 80 |

Values in the last column are hexadecimal. Read DBI adds one CK to RL and read-enable at ratio four, two CK at ratio two; MR3 bits 6/7 enable read/write DBI. Read link and read DBI cannot coexist. MR22 bits 6/4 select read/write link. MR18 bit 4 selects always-on WCK. Combining the admitted link, DBI and always-on options gives 32 explicit fixtures. MR1's WL field is bits 7:4; the complete image is checked, including all controller-owned zero-valued registers.

## Time and path model

A half-WCK is the common integer model time. One CK is eight such units at ratio four and four at ratio two. Pin command and WCK path delays are 16/8 half-WCK respectively (two CK). Write serialization paths are 18/13, read deserialization paths 16/8. Pin DQ input offsets are 2/1 half-WCK (312.5 ps), output offsets 6/3 (937.5 ps). These digital choices fall within the inspected Table 462 ranges; they do not establish analog margins. The trained phase is explicitly zero.

All ten DFI Table 39 fields are unsigned 16-bit WCK units. Read/write enables and returned words use DFI data-clock units. Management and DRAM timings use CK. Retention is checked separately in absolute picoseconds. `twck_toggle_post=3` WCK rounds up the 2.5-WCK minimum; the independent oracle retains the five half-WCK bound. In normal mode `twck_dis` is explicitly long enough for the fixture's largest read/link/split transfer. In always-on mode WS_OFF follows the true final postamble and `twck_dis=5*ratio` WCK delays disable by five CK from WS_OFF. These distinct origins are independently checked; a large normal-mode value cannot silently be reused as a short always-on restart delay.

A paired CAS is followed by its access exactly one CK later. The standalone fast-sync fixture waits twelve CK before its access. The checker evaluates static preamble, one CK half-rate toggle, full-rate edges, pin data offsets, split BL32, postamble, CS stability, disable and every DFI enable/data/return word. At BG BL32, beats 16–31 begin after a sixteen-half-WCK gap. The pin span is six CK; Table 339's array-cycle maximum is eight CK. Normal synchronization expiry uses the latter. BL16 uses four CK in BG mode. MRR BL16 reserves Table 214's four CK at ratio four.

Reused synchronization is allowed only while its maximum window is live, on the same rank and direction. Always-on mode maintains continuous fast toggling until WS_OFF and at least five CK before full disable. Rank transitions use method 1: full stop, fresh synchronization on the new rank. Zero `twck_toggle_cs/rd/wr` means these synchronized-static/multi-CS restart paths are unsupported; it never means they are instantaneous.

## Numeric timing basis

| Contract | Source, one-based PDF pages | Representation / check |
| --- | --- | --- |
| CA truth table, ACT operands/pairing | JEDEC Table 201, pp227–235; DFI Table 7, p37 | `commands.py`, literal CA vectors and independent operand reconstruction |
| WL/RL/nWR and MR images | JEDEC pp171, 173–176; WCK Tables 204/205/208/209 pp236/238/241/244 | Complete MR image checks; read DBI/link latency sets |
| WCK synchronization, rank changes, expiry | JEDEC 7.2, pp232–264; Tables 211/212/214 pp249/250/252; Table 339 p520 | Independent half-WCK waveform and `SyncState`; deliberate early/late/missing edges |
| DQ path offsets | JEDEC pp277, 294, Table 462 p622 | Pin offset bounds; write-path equality, read sample/deadline checks |
| Core timings | JEDEC AC tables pp557–559 | RCD15, RPpb15/RPab17, RAS34, RC51, RRD4, FAW16 CK; WR28 or31 with write link; WTR S/L5/10 or9/13 CK |
| Refresh / RFM / thermal | JEDEC pp178, 200, 320–325, 490–494, 566 | RFCab168/pb96, RFMab168/pb136, REFI≤3124 CK; discovered RFM counters, thermal interval/derating and extra SRX refresh |
| ZQ / MR / FSP | JEDEC pp72–77, 212, 377–387 | ZQCAL1200, ZQLAT24; MRD12, MRR4/8, MRW8; long FSP guard201 and six CK clock guards |
| Link ECC, DBI/DM | JEDEC pp311–314, 490–510, Tables 331/332 pp507/508 | Source matrices, per-byte/group transport, correction before inverse transforms |
| DFI timing, rotation and ownership | DFI pp33, 37–46, 48–55, 64–77, 105–109, 116–129, 138–154 | Per-word/slice completion, low/high frequency acknowledgement, sampled management policy and owner transfer |
| Host protocols | Arm IHI0022H A3.4/A6/A7; IHI0024E Chapters 2/3 | Burst, error, exclusive/order and APB strobe/error models |

The exact external identities and inspected coverage are retained in [reference-sources.json](../../config/reference/reference-sources.json). These references support the stated restricted contract. This is not an exhaustive conformance audit or authorization to redistribute the standards.

## Qualification boundary

Every admitted fixture is checked for both directions, both burst lengths, both ranks and paired/standalone synchronization. Independent returned-word tests connect profile-generated enables and physical completion to DFI word rotation and complete tag retirement. Negative tests cover missing/overflowing fields, incompatible MRs/capabilities, pin/path offsets, split identity and WCK boundaries. Reuse/expiry, rank stop and ownership checks are separate temporal tests. Reports retain fixture hashes, representative half-WCK traces and actual counts.

The checker and generator share source input data, but the checker has separately transcribed JEDEC timing rows and independent constraints; it imports no waveform-generation algorithm. The link oracle uses the standard's matrix as shared reference data. Later verification must compare real RTL against these contracts and qualify the selected actual PHY, returned-state semantics and training evidence.
