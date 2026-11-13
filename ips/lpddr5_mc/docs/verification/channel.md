# Channel owning checks

The channel runner checks actual component RTL and a connected channel with a CA-driven memory fixture. The [architecture](../architecture/channel.md) describes implementation and integration obligations. These checks provide owning evidence; combined host/CDC behavior, independent system qualification, broad coverage closure and final measured throughput remain downstream.

## Reproduce

Use the [pinned foundation environment](contracts.md). Run from the repository root with an explicit disposable tool-storage directory:

```sh
export DHENARA_AGENT_TOOL_STORAGE_ROOT=/absolute/path/to/disposable-tools
make -f ips/lpddr5_mc/Makefile prepare
python3 ips/lpddr5_mc/dv/channel/check.py --report /absolute/path/to/evidence/channel.json
python3 ips/lpddr5_mc/dv/channel/check.py --report /absolute/path/to/evidence/refpb.json --build-name perbank --family maintenance --family channel --channel-case 6 --per-bank-refresh
make -f ips/lpddr5_mc/Makefile check REPORT=/absolute/path/to/evidence/foundation.json
```

The owning runner uses prepared Verilator 5.006-3, Python 3.13.18 and the installed C++ compiler. It stores generated vectors, C++ output and temporary compiler files under the tool-storage root, propagates subprocess failure, refuses existing reports and records commands, durations, source commit/dirty state, seed, tools and file hashes. The beginning/end manifests must match. Do not edit source while a check runs. Final evidence must name the clean committed source; a development pass with untracked files cannot establish committed-source reproduction.

Source exclusions are relative to the IP root. A checkout beneath an ancestor named `evidence` or `__pycache__` retains its complete manifest. The runner first executes `check_test.py`, which checks synthetic exclusions and relocates the actual owned source beneath `evidence` to compare every path and hash. Earlier reports with empty local manifests remain historical evidence; this correction does not rewrite them.

The manifest includes the independent `dv/system` models used by fairness replay, `dv/integration` adapters used by channel stream replay, channel RTL, stimuli and contract models. Fairness reports retain each workload's measured grant gap, eligible wait, suspension counts and direction/port/class grants. Active mutations must fail at their intended identity or final-tail boundary after a passing baseline.

`--build-name` selects an isolated build directory. Concurrent runs need different names. `--family` selects one or more rows below; omission runs all sixteen. Every component and the channel top receive warning-fatal lint; behavior tests use assertions. Channel lint covers storage depths 16/32/64 and channel-one elaboration; this checks sizing only, while the connected behavior fixture uses depth 16. The data fixture reads cases at runtime, and the connected fixture samples its CA-driven memory model in a separate process. This keeps generated simulator functions bounded without removing assertions. Connected compilation can take several minutes in the qualified environment.

## Independent expectations and coverage

`make_vectors.py` reads unchanged canonical profile JSON and accepted Python pin/integrity oracles. Profile assignments are generated separately from the RTL fixture table. Data expectations retain each DFI word/byte, mask/DBI transform, parity bit, enable and continuous CS slot; returned streams are preflighted by `ReadBusOracle`. Production completion signals do not generate expected data or timing. The connected memory fixture decodes emitted CA and checks DFI transfers against those commands. Read words become available only after the declared command path, device read latency, DQ output path, second beat and deserializer path, rounded up to the CK sampling edge. A persistent return pointer covers MRR and both split groups. Within a sampled CK, global DFI enable order determines returned word identity across interleaved BL32 halves; command order alone is insufficient when a snapshot straddles two bursts. The independent `dv/system/return_timing.py` checks the physical lower bound, DFI deadline, per-slice rotation and complete return tail in connected integration replays; its positive and mutation tests cover all 32 profiles, BL16/32 and MRR. Earlier fixture reports using read-enable plus two CK do not establish physical read-return timing.

| Family | Owning coverage | Limits |
| --- | --- | --- |
| `notifications` | Three simultaneous producers, repeated event bits, immutable stalls, full consume/arrival, retained occurrence epochs across recovery, exact-version rearm, clear/set races and overflow report | CK component; mailbox/CDC/APB delivery remains combined work |
| `ecc` | Eight deterministic independent payloads; every single and double fault for inline72, link137 and DMI22; correction/data/status | No triple-error guarantee; transaction behavior is separate |
| `data` | 128 transfers across 32 profiles, read/write BL16/32, both ranks, slice skew/gaps/rotation, split interleaving, CS/path/pin completion, DNV/early/wrong-lane/missing valid, stalls and destructive abort | Bounded tracking fixture; full capacity/randomized system matrix remains pending |
| `wck` | 128 independent half-WCK traces for static/half/fast toggle, postamble and disable in normal/always-on profiles | Full reuse/rank/direction/ownership matrix belongs to combined qualification |
| `config` | 32 snapshots; immutable prepare/commit, CRC, mutation, stale/retired epochs, boot/drain/CS-gap rejection, invalid ranks/ratio/capabilities/MRs/timing/service controls, normal decline and recovery sequence fences | One CK consumer; actual all-channel coordinator and CDC remain pending |
| `scheduler` | CK/CK2/CK4 pairs and urgent read/write startup cadence at every selection phase, row changes, finite queues, mixed directions/classes/ports/ranks/generations, physical-credit replay, exact-once issue, DQ collisions, actual byte caps, zero/borrowing rules, maintenance with selection off and PREpb-to-PREab recovery for REF/RFM/rank closure | Bounded progress; does not establish final issue-gap/fairness or throughput |
| `fairness` | Thirty-six depth-64 scheduler streams: read/write/mixed, CK/CK2/CK4, both port identities/all four classes, 32/64-byte grants, explicit response/ownership/maintenance pauses, measured issue gaps and the conditional 4360 eligible-CK bound; independent replay rejects duplicate grants, unserved/missing final tails and a starved eligible tail | Fixed row without older overlaps, caps or ECC; physical readiness/WCK are explicit fixture inputs, so combined AXI/PHY fairness and throughput remain separate |
| `epochs` | Both ranks and sixteen members each, strict/posted physical/response stages, stalls, exposure/handshake history, stale feedback, late errors and destructive reset | Component witnesses supply physical/MR43 events; connected fixture separately decodes actual MR43 |
| `inline` | Protected mapping, both 32-byte halves, partial RMW, lock/stalls, correction scrub with a waiting same-granule rewrite and physical/response backpressure, UE, physical data/check failure poison and full rewrite | Serial backend with one locked check line and no deferred scrub queue; broad link/host/reset stress remains pending |
| `update` | 55 independent sampled-edge traces, ignored/valid/boundary/late/stuck acknowledgements, registered release, drain/budget denial and competing PHY request | Component ownership witnesses; actual host/CDC binding remains pending |
| `maintenance` | Paired RAA/RFM, stable requests, complete REFpb rounds/order and rank interleave, staggered-rank all-bank grouping, hot all-bank fallback, thermal derating/invalidity, MR4/retention faults, long SR/SRX extra refresh and retained diagnostics through reset | Scheduler timing and real MR4 service are checked by the connected fixture |
| `fsp` | Frozen target MRs, paired programming/readback oracle, long guard, fresh low/high acceptance/completion, normal decline, invalid/late/early/stuck/WCK faults and epoch publication | Constant-CK digital points only; host all-channel commit remains pending |
| `lifecycle` | Fresh boot/token/profile fences, host settlement, normal power-down/wake/tXP, SR update-before-exit/tXSR, FSP/update reserve boundaries, actual per-rank PHY refresh execution, missing execution fault, fresh master reports and destructive recovery | Host settlement/reset and PHY state/execution are explicit fixture attestations |
| `register_io` | Actual MRR/returned opcode, MR4/MR43–45 command-origin guards, defined UI, rank, WCK/stop, ZQ latch/recovery, stalls, DNV and missing-return fault | Both WCK ratios/policies; exclusive pin owner, with combined arbitration checked separately |
| `granule_adapter` | Eighty requests through four credits, masked MWR16 halves, distinct internal tags, staged outcomes, failure/stalls/stale results and invalid column rejection | Bounded slot fixture; complete host acceptance/width/queue matrix remains pending |
| `channel` | Connected streams and management cases below | One channel with explicit host/PHY seams; independent system environment remains downstream |

The table describes assertions present in source. Passing claims additionally require a retained execution report for the stated source and selection. A narrowed pass does not establish the complete family or the final controller requirements.

## Connected selections

`--channel-case 0` runs the complete owning fixture:

- Six non-link stream cases: ratios two/four with CK/CK2/CK4, both ranks, masked writes/readback, response stalls, periodic MR4/ZQ/REF and stale-epoch rejection.
- Write-link and link-plus-inline streams with actual decoded MR43 settlement.
- Normal-WCK streams at both ratios and malformed rank/channel ingress with inline off/on.
- FSP success and normal decline, nineteen frozen paired MR writes, common commit and preserved readback across ratio four to two.
- An 8,000-CK self-refresh hold, actual update release before SRX, tXSR and extra REF before reentry.
- PHY update and IDLE/SR master windows with fresh rank reports.
- Destructive recovery after an actual late update acknowledgement, explicit host settlement/reset confirmation, retained old-boot diagnostics, stale initialization/token rejection and fresh traffic.
- Mandatory RFM from row churn followed by thermal codes 13/15, refresh service and MR4 progress. The thermal restart observes ENABLE acceptance, checks that urgent-maintenance rejection keeps QUIET admission closed, and retries for at most 2000 CK before starting traffic.

A quiesce wait requires both QUIET and actual drained status, because background register work can start while the lifecycle remains QUIET. The CA-driven memory fixture independently counts ACT and REF/RFM decrements and rejects an ACT beyond its paired-bank bound. A component lifecycle check separately supplies actual rank-specific PHY refresh execution within the admitted two-CK bound and verifies a missing-execution fault retains PHY acknowledgement.

The register fixture extends the recovery sum beyond physical return time and checks no intervening CA or result can bypass it. It also checks that a completed guard does not restart after data and WCK handback: the result arrives within two CK of both independent prerequisites. Result stalls preserve identity and opcode. These owning checks do not establish final combined throughput.

Use `--channel-case 7` for exactly one MR4/ZQ visit per populated rank in each of two successive sweeps, including changed returned temperature. Both one- and two-rank cases check the quiet interval after completion. The write-link case with REFpb also checks periodic MR4 through staggered two-rank refresh rounds; a missed 3000-CK watchdog remains a failure.

The maintenance fixture checks two successive normal-interval REF/MR4/ZQ groups with one/two ranks, exact REF membership and suppression of repeated pull-in while ZQ remains pending. The existing REFpb, hot fallback, thermal/retention faults, self-refresh and destructive recovery cases remain active.

Use `--channel-case 8` for the MR4-to-due-ZQ handoff under row-changing traffic with one and two ranks. The monitor arms on final-rank MR4 completion only when ZQ is still pending, requires a nonempty checked window, and rejects ACT at the CA pins until the first ZQ latch. With all-bank refresh it also samples actual CA REF membership before MR4, requires at least one complete observed group and rejects repeated or partial REF membership. This scheduling regression uses internal pending/request indications to select its window; it is separate from independent protocol legality and final throughput checks.

Use `--channel-case 1` for write link, `2` for link plus inline, `3` for normal WCK, `4` for malformed ingress, `5` for management/recovery and `6` for RFM/thermal stress. `--per-bank-refresh` elaborates the channel's REFpb preference; thermal admission may select the documented all-bank fallback. `--trace-epochs` adds physical member/register diagnostics.

## Dirty-management stream preparation

Channel case9 contains eight component stimuli: controller update, FSP, power-down and PHY master, each with successful drain or error between the physical data result and shared-check result. Successful cases wait for an actual offered original completion and then hold it for eight further ticks. Entry requires exactly one successful original completion, released inline lock, independent data/check/return drain and WCK disable. Failed cases permit no successful original completion or ownership entry and require confirmed destructive reset, invalidated configuration and lost trust. The first run at clean `a70dadf` retains its failed aggregate and 2161-tick raw stream; seven cases pass its corrected per-case replay, while its FSP-OP change without high VRCG remains illegal. A separate run at clean `bfc10f96cac3625100149b0a9f31e73f1b988c83` passes compilation, all eight fixture outcomes and the complete independent aggregate replay, with 2227 pin samples and 2227 later handshake samples. See Q18–Q20 in the [qualification registry](qualification.md#evidence-registry) for the separate source bindings and limits.

The C2 source review at `790381e` demonstrated three checker misses on the retained synthetic fixture: removing the last PRE before update or master entry, and inserting PRE during master ACK. These are checker counterexamples, with no claim that RTL emitted them. The correction checks all independently decoded banks closed and ready at the first lifecycle PHY transfer as well as the external management entry. Readiness uses the last replayed DRAM CK, including the declared command delay, bank PRE/AP/REF/RFM recovery, last ACT plus thermally adjusted tRC, rank/FSP recovery, paired-refresh spacing and ZQ latch recovery. DUT drain flags remain separate status witnesses. Reports retain the observed CK, readiness deadline and open-bank list at each checked boundary.

Controller-update setup, active request and lifecycle return retain PHY ownership with no controller CA commands. Master ACK requires a previously sampled master request and PHY ownership; handback requires sampled request withdrawal and a fresh low-then-valid rank report before ACK falls. PHY-update ACK similarly requires a sampled request and registered withdrawal. These exclusive phases reject ACT, PRE and other non-NOP controller commands before physical replay. Owner 1 also spans FSP: paired MRW is allowed only for the selected FSP operation with busy/init_start asserted and init_complete low. The independent model still checks MRW adjacency/recovery. Boot and post-ACK controller/quiescing return commands remain subject to normal physical legality. Synthetic legal fixtures now exercise PHY ownership rather than leaving the controller as owner through management.

The sampled update-release edge still belongs to the PHY; changing owner to controller on that edge cannot authorize a CA command. Fault ownership may retain an asserted ACK without being a healthy handback. It does not discard the physical model or its obligations; an already reserved FSP MRW2 still completes under the pair oracle, and only witnessed reset cancels outstanding work.

The first actual replay exposed a write-tail calculation error: `twrdata_delay` was added to the final DRAM pin edge. DFI 5.0 section 3.2, Table 12 and section 4.9.3 define its origin at DFI write enable. The adapter now takes the maximum of the independently decoded pin endpoint, DFI data/CS endpoint and the existing conservative DFI data-end-plus-delay flush. It retains every byte, return, CS and WCK obligation and rejects a result one sample before the required endpoint. Both ratios have focused exact-boundary and missing-byte tests. This checker correction does not change production timing or the original report.

Original FSP-success replay rejected because the shared DRAM model started tFC on an MR16 FSP-WR-only update. The connected correction applies tFC only to an FSP-OP change and enforces VRCG and DES-only recovery independently. Original case2 at `a70dadf` changes FSP-OP from zero to one with MR16=`0x05`, leaving VRCG bit6 clear; section7.6.3.1 requires high-current VRCG at that change. Those bytes/results remain failed. The corrected production FSP owner and admission budget at `bfc10f9` add the normal-VRCG restore pair and pin recovery. Separate owning and connected C2 runs now support that correction within their stated scopes.

`dv/system/tests/test_fsp.py` covers staging/non-changing writes, ordinary MR spacing, independent FSP-WR/OP, missing VRCG, reserved operands, before/at/after switch and VRCG boundaries, selected NOP versus DES, rank independence, reset freshness and admission arithmetic. Its committed `fixtures/fsp-original-vrcg.json` is a CA-only projection of the original hardware trace with original ticks/cycles and raw digest; it preserves the MR16=`0x05` rejection after legal staging. It is not a complete channel replay. The revised synthetic management fixture independently emits staging, all target MRs, MR16=`0x45`,201 idle CK, MR16=`0x05`, then80CK plus command delay before readiness. The previous synthetic fixture remains in Git history and retained reports; its abbreviated MR1-only path did not prove a legal FSP switch.

The selected FSP/lifecycle owning run at clean `bfc10f9` passed on 2026-11-12 in 25.430701 seconds. Executed assertions cover twenty-pair value/order, switch/restore DES windows, pin-tail readiness, admission below/at/above 679 CK engine and 681 CK lifecycle reserves, maximum-field arithmetic, unchanged profile admission, reserved MRW2 under faults and destructive cancellation during both recoveries. Independent DFI low/high replay accepts the successful and declined traces at returned edges 546 and 4 respectively; the other fault/reset cases rely on executed RTL assertions. Its ten seven-column DFI traces are not raw channel CA/data evidence.

The separate C2 run at that same clean commit passed in 306.163991 seconds. Raw MR16 staging is `0x01` at tick657, OP/high-VRCG switch is `0x45` at909, and normal restore is `0x05` at1112. Restore MRW1 at1111 is 202 CK after switch MRW2, with DES and disabled WCK throughout recovery. Restore recovery ends1192; request release1196, returned high1198, ready1199, publication1200 and controller ownership1201 follow in order. The stream has no direct `clock_change_done` field and no traffic after that FSP publication before the case ends. The owning assertions cover direct clock-done ordering. Component host settlement is still an input attestation; reset cancellation establishes neither physical completion nor retained data.

The following are the recorded commands; their existing output/build paths must not be reused:

```sh
python3 ips/lpddr5_mc/dv/channel/check.py --family fsp --family lifecycle \
  --build-name fsp-vrcg-owning-bfc10f9 \
  --report ips/lpddr5_mc/evidence/fsp-vrcg-owning-bfc10f9/report.json
python3 ips/lpddr5_mc/dv/channel/check.py --family channel --channel-case 9 --channel-observer \
  --build-name c2-fsp-vrcg-bfc10f9 \
  --report ips/lpddr5_mc/evidence/c2-fsp-vrcg-bfc10f9/report.json
```

Commit `3c52ef8` corrects the connected channel0/5 assertions to twenty pairs, exact staging/high-VRCG/normal-restore values, separate MRW2 recovery origins, DES intervals and the final command-path tail. Warning-fatal shared channel TB lint passes; those unselected hardware cases have not executed on the new FSP owner. Original failures and prior-source results remain historical. This does not broaden the selected Q19/Q20 owning/case9 results. Q22 separately records a failed real-wrapper invocation with no complete management case; component settlement remains insufficient for wrapper AXI claims.

Run just the affected parser/adapter units after a checker-only correction, using a new evidence path and the documented Python environment:

```sh
PYTHONPATH="$PWD/ips/lpddr5_mc/dv/channel:$PWD/ips/lpddr5_mc/dv:$PWD/ips/lpddr5_mc/dv/integration" \
  python3 ips/lpddr5_mc/dv/channel/observations_test.py \
  --report "$PWD/ips/lpddr5_mc/evidence/c2-correction-new/units.json"
```

Create the report directory first. The fixtures challenge missing last PRE, too-recent PRE, one-CK-early recovery and forbidden CA during setup/active/return phases, alongside legal FSP and registered handback. They execute no RTL. Checker-only changes need no repeated channel-TB lint; observer/TB changes require that affected lint.

Prepare only the parser fixtures and warning-fatal channel testbench lint:

```sh
python3 ips/lpddr5_mc/dv/channel/check.py --family channel --channel-case 9 \
  --prepare-observer \
  --report "$PWD/ips/lpddr5_mc/evidence/c2-source-preparation-new/report.json" \
  --build-name c2-source-preparation-new
```

This selection returns before binary compilation or simulation. A later selected hardware command uses `--channel-observer` instead of `--prepare-observer`. Observer selection requires exactly channel family/case9 and avoids unrelated component gates. Output paths must be new. The runner binds the canonical profile digest, retains raw JSONL and invokes [observations.py](../../dv/channel/observations.py); the source manifest includes its imported checkers. Synthetic positive/negative fixtures in [observations_test.py](../../dv/channel/observations_test.py) qualify adapter behavior only.

### Version-two observation contract

[channel_observer.svh](../../dv/channel/channel_observer.svh) records every `memory_sample` invocation. On a normal tick the existing order is `memory_model.prepare()`, raw capture, `memory_model.sample()`, then the later clock edge. Returned buses have therefore been driven before capture; combinational completion can settle before the clock-handshake record. Reset samples record the memory-model reset branch. The observer preserves this order. The header requires version2, both exact phase names, picosecond units, `one_tick_one_profile_CK`, the `dirty-management-v1` binding and its SHA-256. Case boundaries require all eight cases in order; a normal terminal marker requires `complete=1`.

Each tick has a globally increasing `tick`, explicit case-local `cycles`, actual simulator `time_ps`, profile index and packed image. The procedural bench includes extra delays: actual timestamps are retained verbatim and must not be interpreted as a uniform CK clock. Independent protocol time is one logical CK per tick, converted using the bound profile's `ck_ps`; each physical reset interval has an explicit origin. Profiles bind one rank, always-on WCK and write DBI at ratios four/two with constant CK and no link/read DBI. An SV assertion checks the full nonreset profile image against the exact fixture image. The parser checks its stability and requires a fresh committed epoch for a profile change. Profile switches preserve memory, row recovery and maintenance state, and require closed rows, independent drain and WCK disable.

Raw ticks retain CA/CS, packed WCK, DFI write/read enables and CS, write masks/data/parity, returned data/valid/DNV/parity, MR4/MR43 fixture inputs, reset/confirmation, trust/owner, boot/configuration/training, the configuration mailbox and external management requests/ACKs. `host_fault` is a fixed absent component seam. A later `handshakes` record has its actual timestamp and matching tick; explicit offer/admit/physical/result/complete flags require exactly corresponding records. Original and inline records retain identities, addresses, direction, length, masks, data and full packed payloads. Original-to-inline admission must match completely, and held/transferred original completions must preserve the complete offered payload and stages. Missing/duplicate ticks, phase/profile bindings, handshake identities, cases or terminal tails fail admission; records are never sorted or repaired.

[physical_stream.py](../../dv/channel/physical_stream.py) connects independent CA/row/maintenance, pin-byte/DBI/continuous-CS, returned-slice/rotation/deadline and half-WCK checks. Internal physical requests supply expectations; independently decoded CA and every physical write byte or read return must establish them before a successful internal result. Dirty state comes from independently checked data/check results, not the sticky `data_committed_q` flag or an empty status. A failed internal result retains physical obligations. Fault/trust loss leaves row, data, return deadlines and WCK checks active; only retention deadlines suspend, with exact half-open intervals reported. Resumed trust requires witnessed reset, invalidation, fresh configuration publication and matching boot/training epochs.

Destructive cancellation requires observed settlement/confirmation and disabled service. Memory-model reset requires an explicit cold/destructive reset witness. Reports retain every pre/post-reset physical interval, ownership transitions and reset ticks; `wck_total` sums all intervals and FSP profile epochs. Reset cancellations separately count backend requests, pin beats, return slices, CA pairs and active WCK epochs. Cancelled tails establish no physical completion or memory preservation. A normal end must independently finish all ordinary obligations and WCK disable.

Focused mutations cover missing check writes, shared-check neighbor corruption, pin data/returns/CS/WCK/CA, early original completion or management entry, altered original/inline payloads, stale boot/configuration/training/scrub, missing periodic refresh, continued missing returns after fault and truncated tails. Original completion accounting and held-completion drain remain distinct from physical and reset-cancellation counts. Exact controller-update ACK timing continues to use its existing owning contract tests; C2 checks external entry/ACK/return and epoch composition without replacing that oracle.

The channel `host_settled` input is a **component settlement attestation**. These stimuli cannot establish surviving wrapper AXI settlement or dirty APB concurrency. The [wrapper follow-up plan](system.md#reachable-management-and-host-settlement-plan) identifies actual gates and missing source obligations. Actual-channel/ECC forwarding/merging is a separate fixture gap; C2 does not close it. Current execution status and reuse limits are in the [qualification matrix](qualification.md).

## Evidence and remaining checks

Development reports retain the original MR43 priority/drain/refresh interference, MR4 starvation, REFpb thermal failures and the corrected quiesce assertion. They are historical observations with their own source manifests. Final reports and logs are retained with the contribution; compare their source commits/manifests before reusing them. The foundation runner also checks unchanged contract regressions, generated products, accepted document hashes, relative documentation links, Python lint/format, C11 and SystemVerilog representation smoke.

Clean reproduction uses a separate full-history checkout with independent Git metadata and isolated compiler output. Reusing installed tools establishes source reproduction in that OS/tool environment; it does not establish fresh-host preparation, remote CI, synthesis or physical qualification.

Reconciliation must test actual host/channel publication, response lifetimes, fault settlement, clock/reset crossings and occurrence delivery after recovery. In particular, an old-boot notification must retain its occurrence metadata without replacing current live status or being discarded by its freshness filter. Qualification retains the supported matrix, independent checker sensitivity, coverage closure, 100-period throughput criterion and conditional fairness bounds. Component compilation or bounded streams cannot close those obligations.


## Normal periodic completion reserve

At the normal all-bank interval, the REF/MR4 group reserves each rank's existing maintenance drain/recovery budget plus an exclusive MRR completion bound. For BL16, the last returned DFI word is bounded by `ceil((trddata_en + 7 + tphy_rdlat) / ratio)` CK, including slice gaps. The physical read/postamble bound is checked separately. The stronger command-origin `tMRR + tMRW` guard overlaps those prerequisites; full WCK disable follows their maximum. Sixteen CK cover request, SYNC/MRR, sampled completion, STOP/DISABLE/result and caller handoff. No 3000-CK watchdog or independent transaction-admission reserve changes. Hot and per-bank policies keep their earlier conservative lead.

The register fixture exercises the bound with both WCK ratios, normal/always-on WCK, long command recovery and last-permitted DFI return timing. The maintenance fixture rejects an unnecessarily early normal benchmark poll before CK2700: this profile's derived full reserve is 292 CK. Connected maintenance and full benchmark results are separate evidence; the new reserve alone does not establish throughput.

The normal single-rank maintenance regression also keeps MR4/ZQ freshly discharged to isolate the REF issue deadline. It rejects charging post-command tRFC before that deadline, holds the REF request until CK3124 and checks that only the actual handshake discharges retention. Connected channel and real-wrapper pin replay must separately establish actual command timing and progress; this owning scheduler check alone does not prove backend drain bounds or the final rate.

Due or pending mandatory RFM adds one complete maintenance visit per rank to the normal polling lead. The connected row-churn handoff case (`--channel-case 8`) exercises this preemption with RFM enabled; its MR4 watchdog remains 3000 CK. Without that visit the delayed single-rank REF policy can leave MR4 waiting behind RFM and fail the watchdog. The performance benchmark retains mandatory RFM protection. Its low-ACT traffic does not reach the RFM threshold, so it uses the 292-CK polling reserve. Early requests stay asserted after RFM discharges the triggering debt, until their respective MR4/ZQ done pulses.

The bounded active-IDLE master refresh repair and dedicated actual-channel/register fixture are documented in [system verification](system.md#active-refresh-repair-and-the-pending-zq-entry-counterexample). Active refresh succeeds at later naturally selected one/two-rank windows, while coincident MR4 publication with pending ZQ exposes a separate entry race. That counterexample remains a qualification blocker; owning component passes do not close it.
