# Numeric PHY profiles and traceability

[profiles.json](../../config/contracts/profiles.json) is the complete input for the restricted digital fixtures. `profiles.admit` checks representation, devices, MRs, capabilities and timing minima; `waveform_oracle.check` independently checks the emitted DRAM-side waveform and DFI data path. Admission alone never authorizes traffic. An integration must qualify its actual path and publish the checked profile atomically while quiesced.

## Operating matrix

All fixtures have CK=1,250 ps, full x16, maximum two identical ranks, MR8=0x10 (8 Gb x16), MR27=0x45 and paired-bank MR57=0. MR27 admits mandatory RFM; no exemption is assumed. Read and write data mask is enabled, MR13=0x20. MR28=0 selects background ZQ calibration. NZQ is at most four. ODT, training mode, enhanced NT-ODT, data copy and DVFSC are off.

The RTL preserves these exact timing/MR fixtures while allowing the canonical discovered paired-bank RFM capability variants: MR27 must have a nonzero threshold when its mandatory-RFM bit is set, and MR57 bits7:4 must be zero with bits3:2 limited to zero/one. MR57 bits1:0 select the existing maintenance decrement factors. The integration supplies these discovered facts, independent of the programmed MR image. Unsupported geometry, timing changes and single-bank extensions still reject admission. Owning tests enumerate all 256 MR57 bytes; the connected wrapper uses nonzero MR57 values three and two across destructive recovery so a zero-filled readback cannot pass. This does not qualify a new PHY timing point.

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

## Data chip-select duration and rank transitions

DFI 5.0 Tables 12/15 (PDF pp42/46) define read/write `tphy_*csgap` in DFI data-clock cycles. They add rank-change transaction spacing and require continuous CS assertion for at least `csgap + dfirw_length`. These reference profiles support **zero only** for both gap parameters; any nonzero value rejects profile admission, waveform checking and canonical configuration activation. The 16-bit register representation is retained, with this restriction documented in its canonical field descriptions. Supporting a PHY with nonzero gaps requires extending and qualifying both duration and transaction-spacing behavior first.

CS asserts at the command plus `tphy_*cslat`, independently of read/write enable. The selected rank remains continuously asserted through the final word slot. `Waveform.data_cs` records every asserted data-clock slot, including idle slots between split halves; `data_cs_end` is the explicit first deasserted edge in half-WCK units. BL16 holds eight data clocks, unsplit BL32 sixteen, and BG split BL32 twenty-four, including its eight-clock split gap. This conservative split hold exceeds the ordinary sixteen-word minimum; sparse valid-word samples do not describe a continuous CS level. The independent checker rejects early/late deassertion, missing split-gap slots, shifted assertions and wrong ranks.

`check_data_cs_sequence` checks shifted standalone waveforms in DFI CAS order. Before changing rank, all earlier CS holds must end and the previous rank's WCK must be fully disabled before the fresh CAS synchronization. Same-direction and cross-direction changes obey both conditions. This temporal CS check complements the JEDEC synchronization oracle; it is not a complete bank/command legality checker. The qualification report includes all four direction combinations for each profile. The same-rank performance witnesses retain their existing command/data schedules and bandwidth accounting.

## Numeric timing basis

| Contract | Source, one-based PDF pages | Representation / check |
| --- | --- | --- |
| CA truth table, ACT operands/pairing | JEDEC Table 201, pp227–235; DFI Table 7, p37 | `commands.py`, literal CA vectors and independent operand reconstruction |
| WL/RL/nWR and MR images | JEDEC pp171, 173–176; WCK Tables 204/205/208/209 pp236/238/241/244 | Complete MR image checks; read DBI/link latency sets |
| WCK synchronization, rank changes, expiry | JEDEC 7.2, pp232–264; Tables 211/212/214 pp249/250/252; Table 339 p520 | Independent half-WCK waveform and `SyncState`; deliberate early/late/missing edges |
| DQ path offsets | JEDEC pp277, 294, Table 462 p622 | Pin offset bounds; write-path equality, read sample/deadline checks |
| Core timings | JEDEC AC tables pp557–559 | RCD15, RPpb15/RPab17, RAS34, RC51, RRD4, FAW16 CK; WR28 or31 with write link; WTR S/L5/10 or9/13 CK |
| Refresh / RFM / thermal | JEDEC pp178, 200, 320–325, 490–494, 566 | RFCab168/pb96, RFMab168/pb136, REFI≤3124 CK; discovered RFM counters, thermal interval/derating and extra SRX refresh |
| ZQ / MR / FSP / VRCG | JEDEC pp72–77, 212, 377–387, 454; Tables255–257/298 and Figures200/242/243 | ZQCAL1200, ZQLAT24; MRD12, MRR4/8, MRW minimum8 (fixtures12); long tFC201; six-CK entry/exit within continuous-CK switch interval; VRCG enable120/disable80CK from each MRW2 |
| Link ECC, DBI/DM | JEDEC pp311–314, 490–510, Tables 331/332 pp507/508 | Source matrices, per-byte/group transport, correction before inverse transforms |
| DFI timing, rotation and ownership | DFI pp33, 37–46, 48–55, 64–77, 105–109, 116–129, 138–154 | Per-word/slice completion, low/high frequency acknowledgement, sampled management policy and owner transfer |
| Data CS duration/rank gap | DFI Tables 12/15, pp42/46 | Zero-gap admission; continuous split-gap hold with explicit deassertion; separate rank-transition checks |
| Host protocols | Arm IHI0022H A3.4/A6/A7; IHI0024E Chapters 2/3 | Burst, error, exclusive/order and APB strobe/error models |

The exact external identities and inspected coverage are retained in [reference-sources.json](../../config/reference/reference-sources.json). These references support the stated restricted contract. This is not an exhaustive conformance audit or authorization to redistribute the standards.

The fixed VRCG intervals derive from CK=1250ps and Table298; they need no new register fields or generated profile ABI. Existing `TFC`, `TCKFSPE` and `TCKFSPX` fields remain validated, and complete MR16 normal images remain zero before the engine overlays dynamic FSP controls. Profile inputs, register generation, packed profile decoders and software images remain unchanged. The [FSP control sequence](control.md#dfi-management-and-fsp) defines current-versus-target interval ownership, pin origins, restore and admission accounting.

## Qualification boundary

Every admitted fixture is checked for both directions, both burst lengths, both ranks and paired/standalone synchronization. Independent returned-word tests connect profile-generated enables and physical completion to DFI word rotation and complete tag retirement. Negative tests cover missing/overflowing fields, incompatible MRs/capabilities, pin/path offsets, split identity and WCK boundaries. Reuse/expiry, rank stop and ownership checks are separate temporal tests. Reports retain fixture hashes, representative half-WCK traces and actual counts.

The checker and generator share source input data, but the checker has separately transcribed JEDEC timing rows and independent constraints; it imports no waveform-generation algorithm. The link oracle uses the standard's matrix as shared reference data. Later verification must compare real RTL against these contracts and qualify the selected actual PHY, returned-state semantics and training evidence.
