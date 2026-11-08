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

The manifest includes the independent `dv/system` models used by fairness replay as well as channel RTL, stimuli and contract models. Fairness reports retain each workload's measured grant gap, eligible wait, suspension counts and direction/port/class grants. Active mutations must fail at their intended identity or final-tail boundary after a passing baseline.

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
| `scheduler` | CK/CK2/CK4 pairs, row changes, finite queues, mixed directions/classes/ports/ranks/generations, physical-credit replay, exact-once issue, DQ collisions, actual byte caps, zero/borrowing rules, maintenance with selection off and PREpb-to-PREab recovery for REF/RFM/rank closure | Bounded progress; does not establish final issue-gap/fairness or throughput |
| `fairness` | Thirty-six depth-64 scheduler streams: read/write/mixed, CK/CK2/CK4, both port identities/all four classes, 32/64-byte grants, explicit response/ownership/maintenance pauses, measured issue gaps and the conditional 4360 eligible-CK bound; independent replay rejects duplicate grants, unserved/missing final tails and a starved eligible tail | Fixed row without older overlaps, caps or ECC; physical readiness/WCK are explicit fixture inputs, so combined AXI/PHY fairness and throughput remain separate |
| `epochs` | Both ranks and sixteen members each, strict/posted physical/response stages, stalls, exposure/handshake history, stale feedback, late errors and destructive reset | Component witnesses supply physical/MR43 events; connected fixture separately decodes actual MR43 |
| `inline` | Protected mapping, both 32-byte halves, partial RMW, lock/stalls, correction scrub, UE, physical data/check failure poison and full rewrite | Serial backend; broad cache-pressure/link/host/reset stress remains pending |
| `update` | 55 independent sampled-edge traces, ignored/valid/boundary/late/stuck acknowledgements, registered release, drain/budget denial and competing PHY request | Component ownership witnesses; actual host/CDC binding remains pending |
| `maintenance` | Paired RAA/RFM, stable requests, complete REFpb rounds/order and rank interleave, staggered-rank all-bank grouping, hot all-bank fallback, thermal derating/invalidity, MR4/retention faults, long SR/SRX extra refresh and retained diagnostics through reset | Scheduler timing and real MR4 service are checked by the connected fixture |
| `fsp` | Frozen target MRs, paired programming/readback oracle, long guard, fresh low/high acceptance/completion, normal decline, invalid/late/early/stuck/WCK faults and epoch publication | Constant-CK digital points only; host all-channel commit remains pending |
| `lifecycle` | Fresh boot/token/profile fences, host settlement, normal power-down/wake/tXP, SR update-before-exit/tXSR, FSP/update reserve boundaries, actual per-rank PHY refresh execution, missing execution fault, fresh master reports and destructive recovery | Host settlement/reset and PHY state/execution are explicit fixture attestations |
| `register_io` | Actual MRR/returned opcode, defined UI, rank, WCK/stop, ZQ latch/recovery, stalls, DNV and missing-return fault | Exclusive pin owner; combined arbitration is checked separately |
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
- Mandatory RFM from row churn followed by thermal codes 13/15, refresh service and MR4 progress.

A quiesce wait requires both QUIET and actual drained status, because background register work can start while the lifecycle remains QUIET. The CA-driven memory fixture independently counts ACT and REF/RFM decrements and rejects an ACT beyond its paired-bank bound. A component lifecycle check separately supplies actual rank-specific PHY refresh execution within the admitted two-CK bound and verifies a missing-execution fault retains PHY acknowledgement.

Use `--channel-case 1` for write link, `2` for link plus inline, `3` for normal WCK, `4` for malformed ingress, `5` for management/recovery and `6` for RFM/thermal stress. `--per-bank-refresh` elaborates the channel's REFpb preference; thermal admission may select the documented all-bank fallback. `--trace-epochs` adds physical member/register diagnostics.

## Evidence and remaining checks

Development reports retain the original MR43 priority/drain/refresh interference, MR4 starvation, REFpb thermal failures and the corrected quiesce assertion. They are historical observations with their own source manifests. Final reports and logs are retained with the contribution; compare their source commits/manifests before reusing them. The foundation runner also checks unchanged contract regressions, generated products, accepted document hashes, relative documentation links, Python lint/format, C11 and SystemVerilog representation smoke.

Clean reproduction uses a separate full-history checkout with independent Git metadata and isolated compiler output. Reusing installed tools establishes source reproduction in that OS/tool environment; it does not establish fresh-host preparation, remote CI, synthesis or physical qualification.

Reconciliation must test actual host/channel publication, response lifetimes, fault settlement, clock/reset crossings and occurrence delivery after recovery. In particular, an old-boot notification must retain its occurrence metadata without replacing current live status or being discarded by its freshness filter. Qualification retains the supported matrix, independent checker sensitivity, coverage closure, 100-period throughput criterion and conditional fairness bounds. Component compilation or bounded streams cannot close those obligations.
