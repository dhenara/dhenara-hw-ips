# Controller qualification status

As of 2026-11-12, controller qualification is **incomplete**. All six required CK/CK2/CK4 read/write throughput milestones have reviewed support. That result does not close the complete bandwidth/fairness requirement or the release checkpoint. This page is the current requirement-to-evidence matrix; older status reports and the environment coverage map retain their historical wording.

The implementation baseline is `98e8ca41879eae9c2f1754ef6c5a1c94f8dc7da7`. The accepted Definition v2 supplies the six requirement checks, release checkpoint and source-integrity check below. The [implementation description](../architecture/implementation.md) describes that hardware. Later changes made to adopt these documents do not change RTL, configurations, fixtures or checkers. Recorded report manifests retain their original source revisions; a documentation-only descendant does not retroactively become the commit tested by an earlier run.

## Evidence states and source binding

**Proved/reusable** means an executed, retained bounded check with applicable hardware and declared limits. **Stale** means evidence exists but a specific source, checker or configuration difference still needs reconciliation. **Absent** means the required capability or observation is not demonstrated by the inspected retained proof. **Failed** identifies an actual unsuccessful check or an observed implementation shortfall. A requirement containing reusable subcases and open subcases remains incomplete.

The 2026-11-12 evidence audit records 52 raw reports, their hashes/configurations, changed source paths and archive digests. The audit is retained as `lpddr5/qualification/closure-20261112/evidence-audit.json` in the engineering evidence store, alongside its script. It establishes no new simulation result. All 295 members of `completed-checks-ef41f25.tar.gz` were checked against its supplied manifest. Other archives were parsed and selected reports inspected; a whole-archive member-manifest check is not claimed for those archives.

Git comparison finds no RTL/configuration differences between this baseline and `812d562`, `ff1775a`, `132a892`, `7e1db1d` or `735a724`. This supports hardware reuse for the parameter, fairness, concurrent-cap and overlap cases below. Testbench/checker changes remain separately recorded: stronger read-padding, repeated-FIXED-effect, physical-retirement and WCK replays are retained in the evidence store. Hardware equivalence alone does not establish that a newer checker was executed. Earlier `e8dfd7e` zero-cap and `c7e9805` reset results predate scheduler changes to predictive ACCESS selection and live pick rechecking; their current scheduler boundary remains stale.

The confirmed loss of some historical detailed task outputs remains a reproducibility limitation. Successful terminal receipts for later AXI128 replay and 13 selected connected steps establish those processes' recorded outcomes; unavailable detailed reports/traces cannot establish current behavior coverage. These receipts do not convert the earlier failed aggregate into a pass. No further recovery search is selected.

## Requirement-to-evidence matrix

Evidence identifiers refer to the exact reports and archives in the registry below. Each row describes its complete check, including open portions; it is not a passing result submission.

| Requirement / check | Proved or reusable evidence | Stale, absent or failed portion | Minimum next action |
| --- | --- | --- | --- |
| `functional_controller / axi_memory_correctness` | Q2 four parameter/region/ID/address cases; Q3 AXI64 seeded stress; Q4 actual overlapping AW/read/write and response-credit exhaustion; Q5 host/CDC owning cases | **Failed capability comparison:** `host_core.blocked_by_older()` holds overlapping work until older integrity completion; no cross-transaction merger/read-forwarding datapath is present. Serial-equivalent traffic does not prove the explicitly required merging/forwarding. Later AXI128 correction details have the recorded evidence-loss limitation. Arbitrary region permutations are outside the four samples. | Lead disposition of the capability gap before an RTL or contract change. Bind existing corrected replay evidence where available; identify only the unproved assertions before selecting new traffic. R1 is not a forwarding test. |
| `lpddr_protocol / protocol_legality_progress` | Q1 six benchmark cases with independent source/pin/data/WCK/maintenance checking; Q2 both WCK ratios and rank traffic; Q5 channel/REFpb and sensitivity fixtures; Q8 hot refresh; Q9 clean inline prefix and recovery segment | **Absent:** independent continuous protocol observation across the intervening fault/reset/ownership boundary. Owning/model sensitivity is not exhaustive combined-controller transition coverage. Several complete connected-family details are unavailable as described above. | Map retained owning assertions and available combined traces to each boundary before developing a focused cross-boundary observer/fixture. R3 is only the existing reset fixture and cannot fill this observation gap unchanged. |
| `ecc_reliability / ecc_fault_recovery` | Q5 exhaustive component codeword faults on eight vectors; Q9 corrected inline lifecycle replay, partial RMW, shared check line, scrub/version overlap, exposed B stability and fresh service; outgoing write parity/MR43 owning evidence | **Stale:** Q10 read-link correction at dirty `4b697bd`, before 14 later RTL changes. **Absent:** full combined dirty-state management/fault cross-product and early-posted MR43 exposure path. Forwarding-dependent ECC behavior shares the capability gap above. | R1 focused clean read-link refresh after the source/contract disposition. Review conservative posted completion against the accepted semantics; do not infer a need for early posting solely from an old cache/posted label. New cross-boundary fixtures are required for observations not present in Q9. |
| `control_lifecycle / management_transitions` | Q5 actual APB/CDC/publication/status/C-helper cases and owning update/FSP/PM/master boundaries; Q6 ten clock/reset recovery cases; Q8 thermal maintenance; Q9 inline quiesce/fault/recovery | **Stale:** Q6 predates scheduler changes. **Absent:** complete independently observed pin/refresh/ownership behavior through fault/reset, plus source-bound reconciliation of all specified management failure boundaries. Final clean all-channel reproduction is absent. | Reconcile owning boundary assertions with the accepted contract; refresh only a source-affected case if that inspection cannot settle it. R3 exposes the existing reset command and time, without claiming it adds the missing observer. |
| `host_bandwidth / throughput_and_fairness` | Q1 six reviewed throughput milestones; Q7 all 18 fairness cases and current-hardware concurrent borrowing; Q2/Q3/Q8/Q9/Q11 separate finite workload measurements | **Stale:** six historical cap variants have binding-level evidence, and zero-cap cases predate scheduler changes. **Failed historical case:** row-churn write G64; corrected conditional G96 has separate passing evidence. Cost reporting is bounded, not an exhaustive workload cross-product. | Reconcile cap source impact and retained raw reports before selecting R2 or any simulation. Preserve Q7 all-class proof and Q1 results. No repeated full benchmark or fairness run is selected. |
| `reusable_delivery / reproduce_delivery` | Exact manifests, commands, logs and pinned installed tools for the bounded reports; preserved originals; generated sources and examples have prior owning checks; portable storage/critical-path discussion in implementation document | **Absent:** separate clean-checkout reproduction of the final delivered commit covering the complete agreed matrix and resolved behavior/failure obligations. Historical clean integration reproduction is earlier-source evidence. Fresh-host setup, remote CI and physical qualification are not established. | After source and closure selection stabilize, create a separate checkout at the exact delivered commit, isolate builds, record hashes/tools and execute only the lead-selected missing/changed checks. R1 can supply one such new result; it cannot alone pass this requirement. |
| `verified_controller / release_evidence` | Above bounded results and their retained corrections | **Absent:** a complete passing combined release review; unresolved capability/observation/reproduction gaps remain | Keep the checkpoint pending until all six checks and source integrity are resolved. No passing Candidate is asserted by this page. |
| Integration `available_source_integrity` | Q0 accepted combined integration provenance; source-manifest relocation repair is already implemented in `dv/channel/check.py` with regression coverage; current document/hash/link/generated checks are recorded with this adoption | **Absent:** final all-requirement source qualification. Documentation checks cannot substitute for it. | Retain the documentation-only diff and the original hardware bindings. Recheck affected owners only after an actual hardware/checker change. |

## Evidence registry

Paths in this registry are evidence-store names under `lpddr5/qualification/` unless another prefix is explicit. They name retained reports, rather than repository prerequisites. Full report hashes and archive digests are in the audit. Tool commands/configuration/seed/logs in an original report remain authoritative for that run.

| ID | Source and configuration | Exact report/archive | Scope and limitations |
| --- | --- | --- | --- |
| Q0 | Combined `89564ab99c9ccd2043339bf16ddd96ef1ee37ba6`; representative integration | `lpddr5/reviews/reconciliation-89564ab/conclusion.md`, `independent-report.json`, `independent-evidence.tar.gz` | Accepted earlier integration, 21 steps; not final source or broad release proof. Original failed cold/status checks remain under `reconciliation-011bd33`. |
| Q1 | `98e8ca4`; P2/C2/R1, AXI128/400, CK800/WCK3200, depth64, aligned 64-byte plain BL32, profile4, service1/2/4 | Six reports in the throughput table; review `lpddr5/reviews/qualification-continuation-20261111.md` | Reviewed milestone support. CK4 write reuses explicitly hash-bound simulation/pin/data prerequisites. Executable bytes are not in the benchmark archives; executable rehash is worker evidence. |
| Q2 | Clean `812d562`; parameter samples below | `parameters-smoke-c2ceb09.tar.gz`, members `parameters-812d562/{min,max,one-port-two-channel,two-port-one-channel}/report.json` | Four passing actual traffic cases, source/error/region checks and finite costs. Previous region failures remain in `completed-checks-turn25.tar.gz` and `completed-checks-turn26.tar.gz`. |
| Q3 | Clean `80afe9229b47571831932cec8e544b315593f9b8`; seed1234567, P1/C1/R1, AXI64, ID4/A32, depth16, granule32, profile0, service4 | `stress-gate-735a724.tar.gz`, member `axi-stress-80afe92/min-seed1234567/report.json` | Passed 64 AW/68 AR; B=48 OKAY/12 SLVERR/4 DECERR, reads=64 OKAY/4 DECERR. Earlier AXI128 `axi-stress-c2ceb09/seed2095/report.json` remains failed: `unexpected physical write byte`; later correction receipts/replay accounts do not replace missing details. |
| Q4 | `735a724`; P2/C2/R1 AXI128, depth16, independent clocks | `axi-overlap-735a724/report.json`, `binding.json`; `overlap-inline-be41210.tar.gz` | Passed 42 AW/46 AR, 12 overlapping admissions, 16 live responses per port/direction; physical-retirement binding and held-response checks. Cross-port conflicts are serialized before READY. |
| Q5 | Clean `812d562`; channel/host/foundation/system owning configurations, plus representative connected checks | `stress-gate-735a724.tar.gz`, members `combined-gate-812d562/{channel,channel-refpb,system,decoder,notification,publication,control-example}/report.json`, `combined-host-foundation/report.json` | These retained steps pass; parent `combined-gate-812d562/report.json` failed by APB-family timeout. Its child `apb_publication/report.json` is a historical running snapshot. Environment passes explicitly retain `controller_qualified:false`. |
| Q6 | `c7e9805`; P2/C2/R1 AXI128, depth16, ten runtime reset/stopped-clock cases | `completed-checks-turn26.tar.gz`, member `reset-ready-matrix-c7e9805/report.json` | Passed complete surviving AXI settlement, stale-confirmation rejection, new epochs/boot/service. Memory model is aborted at reset; only final recovery traffic has independent clean pin replay. No preservation claim. |
| Q7 | `7e1db1d` / `132a892` fairness, `ff1775a` concurrent caps; P2/C2/R1 AXI128, depth64, granule64, profile4 | `completed-checks-ef41f25.tar.gz`; `settled-fairness-snapshot-f2fe912.json`, actual `fairness-7e1db1d/` and `fairness-132a892/` reports; `concurrent-caps-ff1775a/borrow64/report.json` | All 18 final fairness reports pass with the conditions below. Borrow64 covers all 32 active channel/direction/port/class groups and six mutations. Zero caps are separate `completed-checks-turn27.tar.gz`, `zero-caps-e8dfd7e/{mode1-read,mode1-write,mode2-read,mode2-write}/report.json`. |
| Q8 | `4f655b8` hot traffic, sensitivity at `ccb4528`/`812d562`; P1/C1/R1 AXI128, profile0/mode3 | `completed-checks-ef41f25.tar.gz`, members `hot-traffic-width/run/report.json`, `hot-thermal-sensitivity-ccb4528/report.json`, `hot-wck-sensitivity-812d562/report.json` | Complete MR4=13 precedes 32 writes/32 reads, 2048 bytes each; 781-CK refresh and derated timing checked. Sixteen hot/thermal/WCK mutations reject; earlier failures retained. |
| Q9 | Simulation `ee1e46fc30f06f2ceae0cea057c0b75f78cb595e`, corrected replay `89a2f3a0f8f37d99ecd8e118879bb9e28abfc97b`; P2/C2/R1 AXI128 inline | `inline-rank-correction-evidence.tar.gz`, members `new-turn40-inline-ee1e46f/report.json`, `new-turn41-inline-replay/report.json` | Simulation 153437 checks; first report fails rank-mismatched refresh replay. Corrected replay passes: 11 writes/6 reads before recovery, one write fails as intended; fresh 1/1, seven images, four shared granules, 57 clean-prefix+6 recovered accesses, four rejected mutations. |
| Q10 | Dirty `4b697bd`; P1/C1/R1 AXI128, ID16, depth16, profile16, mode4, service2 | `read-link-development-report.json` | Development pass: 558 reads/2232 codewords, 548 corrected singles, eight UEs, two clean reads, active raw/response mutations. Requires clean current-source refresh. |
| Q11 | Evaluator `132a892`; mixed/read/write/backpressure traces; Q2 parameter/rank cases; new accounting at `98e8ca4` for Q9 traces | `completed-checks-ef41f25.tar.gz`, `finite-costs-132a892/report.json`; Q2/Q3/Q8 `workload_costs` fields; `closure-20261112/inline-costs.json` | Finite first-admission-to-last-completion intervals, successful addressed/strobed bytes, latency, stalls and command costs. These do not prove correctness or a sustained feature-specific bandwidth penalty. Failed input cases in the cost report remain failed. |

The split ef41f25 archive reconstructs to SHA256 `8ed3ef2ac7db40fad3304777b353c50e5d424062ad78f6c9d615682cfde52fc9`. Inline archive SHA256 is `10fccffd169f96a055ba4c6ef7a1b16a083f6b72a7c946b0e8b0edfca0703bc3`. The evidence-store snapshot used for this reconciliation is `0f64eeb90f8be6e4962e253618f2fde6ed831921`; the separately requested implementation document came from `79000994b59994cf44527ec9e2971ccab0994800`.

### Functional, parameter and clock boundaries

Q2 exercises real last-region-granule traffic, representable next-bit ID/address rejection, region-start/crossing errors and unchanged valid-prefix readback. It covers these representative boundaries, rather than every Cartesian combination:

| Sample | Ports/channels/ranks | AXI data / ID / address | Depth / granule | Profile / selection |
| --- | --- | --- | --- | --- |
| Minimum | 1/1/1 | 64/4/32 | 16/32 bytes | 0/CK4 |
| Maximum | 2/2/2 | 256/16/40 | 64/64 bytes | 0/CK |
| One port, two channels | 1/2/2 | 128/8/36 | 32/32 bytes | 24/CK2, WCK ratio2 |
| Two ports, one channel | 2/1/1 | 256/12/36 | 32/32 bytes | 4/CK4 |

Q3 covers FIXED/INCR/WRAP, narrow/unaligned/sparse strobes, delayed W, offered B/R stalls and malformed writes. Q4 covers AW-before-W, same-ID old-read snapshots, younger buffered writes, cross-port exclusion and complete response-credit exhaustion. Q5 adds owning invalid-profile/capability, publication and CDC tests. Q6 addresses core/APB and either AXI reset with stopped CK/APB/source clocks. These are digital asynchronous-clock tests; physical metastability, netlist CDC and arbitrary clock-frequency qualification remain outside their demonstrated scope.

### Throughput and fairness

All Q1 runs retain 100 periods of 3000 CK, ten warm-up and 90 measured periods, no tail payload credit, ready responses, finite rows and REF/MR4/ZQ. Benchmark caps are strict 48000 bytes per 3000-CK window. The independent source/pin/data/protocol/maintenance checks and complete tail settlement are prerequisites. The write recurrence/turnover method is documented in [performance.md](../spec/performance.md); it preserves the original false endpoint diagnostic and does not claim scheduler fairness.

| Direction / selection | Exact report under qualification evidence | Measured/reference bytes | Result and timing |
| --- | --- | --- | --- |
| Read CK | `new-turn55-read-ck/report.json` and its split `evidence.tar.gz` | 4320000/4320000 | 12.8 GB/s; backlog62/channel at every measured boundary; simulation3982.32s, replay66.37s |
| Read CK2 | `new-turn51-read-ck2/report.json` and its split archive | 4320000/4320000 | 12.8 GB/s; read endpoint gate passes; simulation3988.24s, replay69.33s |
| Read CK4 | `new-turn53-read-ck4/report.json` and its split archive | 4320000/3905280 | 12.8 GB/s; read endpoint gate passes; simulation4120.31s, replay66.08s |
| Write CK | `new-turn57-write-ck/report.json` and its split archive | 4317504/4320000 | 99.94222222%, 12.79260444 GB/s; simulation3923.94s, replay67.93s |
| Write CK2 | `new-turn49-write-ck2/report.json` and its split archive | 4317824/4320000 | 99.94963%; simulation3923.14s, replay68.45s |
| Write CK4 | `new-turn47-write-stability/derived-report.json` and `evidence.tar.gz` | 4317248/3905280 | Passed with hash-bound retained prerequisites; changed analysis 15.28s, retained full simulation 3947.43s |

Write CK settles all 74951 writes; maximum AW-to-B age is 846 CK/channel. Every measured REF interval passes floor 6 and full prior-cut AW turnover under the 9372-CK guard. Original endpoint `[47,47]` to `[54,53]` stays false. CK4 write preserves `[50,50]` to `[62,62]` and age 874/870 CK. None of those endpoint differences is silently relabelled. The lead review and raw AW/B recount support the CK result; read CK's rejected descriptive assumption of identical per-channel periods is retained with its correction to exact integral burst/window totals.

Q7 is separate all-class fairness evidence, not an inference from Q1. Nine balanced plus nine row-churn cases cover read/write/mixed × CK/CK2/CK4. Balanced read/write use A32/G64 and bound 4360 eligible CK; some groups never reach urgent age. Balanced mixed uses G128/bound 8456, measured maximum 123. Row-churn uses A1 and obtains aged grants for every active channel/direction/port/class group: read G64/bound 4112/max 64; write G96/bound 6160/max 80; mixed G128/bound 8208/max 87. The original row-write G64 counterexample remains failed; G96 is a distinct conditional claim, not a universal G64 promise.

`fairness.py` accounts for eligibility every CK through the final trace tail. Row recovery, timing and WCK waiting remain eligible; named maintenance intervals suspend the conditional bound. Lost ownership with live work cannot be treated as a harmless suspension. The retained reports enumerate issue gaps, oldest-first grants and suspended intervals; the hardware has not changed since these tests.

Concurrent borrow64 passes 64 issues, 2048 read+2048 write bytes and 512 borrowed bytes/channel. Independent opposite-eligibility probes cover ready-opposite witnesses and timing-blocked borrowing; six active mutations reject. Its scope is disjoint plain one-rank traffic. `caps-7089a68-binding.json` records the six strict32/64, mixed32/64 and borrow32/64 historical reports, hashes and mutations, but six later channel RTL files differ. Those binding-level records and four older zero-cap passes require source-impact reconciliation before full cap closure. No new cap simulation is selected merely because the old coverage map says pending.

### Integrity, lifecycle and sensitivity boundaries

Q5's component ECC fixture exhausts all single/double positions for eight independent vectors in the 137-bit link, 72-bit inline and 22-bit DMI codewords. This establishes component mathematics, not every split/mask/DBI/management composition. Actual outgoing write parity and MR43 failure settlement are separate from raw returned read-parity correction. Q10 is the specific stale read-link observation.

Q9 corrects two independent fixture/checker defects without changing production RTL: actual APB publication now sets enabled-region inline attributes; replay binds refresh to the one instantiated rank instead of the reusable profile's two ranks. The failed APB publication and original rank-mismatched report remain preserved. Corrected replay retains partial RMW, neighboring check bytes, correction-scrub version ordering, quiesce drain, failed unoffered work, held exposed success and fresh protected service. Clean independent protocol checking ends at the real PHY fault; source/image assertions span the fault and a separate clean replay covers recovery. It does not demonstrate continuous protocol legality during that intervening interval.

The actual inline owner is one serialized locked data/check operation per channel, with a poison CAM; there is no integrated multi-entry dirty check cache or deferred scrub queue. Historical cache-pressure/eviction labels must be evaluated against shared-check preservation, reserved capacity, complete physical commit, repair version and drain invariants. They cannot be declared passed as nonexistent cache operations. Host `POSTED_EARLY=0` and backend `posted_i=0` conservatively await final integrity. The standalone epoch owner tests early-posted mechanics; a composed early-posted MR43 exposure path is absent. Conservative delayed posting can satisfy the documented timing eligibility; absence of early exposure alone is not a newly established contract violation. Q9 does demonstrate stable already-exposed success during a subsequent fault.

Protocol sensitivity is layered: foundation/model timing/edge/pair/profile violations; actual-trace data/CS/rank/timestamp/identity/tail and WCK mutations; all-class fairness/cap mutations; hot missed-refresh/timing mutations; component ECC vectors; Q9 source/image mutations. Existing positive owning update/FSP/PM/master/reset assertions and these negative tests remain useful. They do not prove a combined cross-boundary observer that is not present. Missing qualification includes independent observation of the specified dirty-integrity and management/ownership failure boundaries; a new fixture must retain fault, epoch, drain, refresh and fresh-service witnesses without invoking memory preservation.

The specific combined observations still missing are dirty inline data/check work at update/FSP/power/master entry and failure, rather than only quiesce and a general PHY error; before/at/after physical write and MR43 failure with actual host exposure; and continuous CA/WCK/refresh/ownership observation across destructive reset with explicit epoch invalidation. The owning update fixture already checks early/minimum/late/stuck ACK edges and drain/budget denial. The owning lifecycle fixture already checks partial low-power ACK, update-before-SRX, FSP/update reserve boundaries, master return/refresh attestations and missing attestation faults. Those are reusable component results. They use supplied drain/ownership witnesses; they do not observe these conditions with dirty inline work and real AXI/APB crossings in one run. There is no existing selection that adds all these missing witnesses, so a concrete fixture/checker change must precede any proposed command or runtime estimate for them.

### Reconciliation of the environment coverage labels

The following accounts for all 32 keys in `dv/system/coverage.json`. That file remains an environment test inventory with `controller_qualified:false`; its old pending prose is not a current controller verdict.

| Coverage keys | Current evidence state |
| --- | --- |
| `axi_bursts`, `write_validation` | Proved/reusable Q2/Q3 bounded traffic; AXI128 failed replay/correction history retains its detailed-evidence limit. |
| `global_order`, `preacceptance`, `response_lifetime` | Proved/reusable owning Q5 and actual Q4 overlap/credits/stalls; not forwarding implementation proof. |
| `forward_merge`, `merged_original_dependencies` | Failed capability comparison / absent combined datapath proof; reference-model tests do not supply it. |
| `ca_pairs`, `wck_paths`, `split_cs`, `read_return`, `continuous_wck_cs` | Proved/reusable Q1/Q2/Q5 and actual-trace sensitivity within their declared profiles; intervening fault/reset observation absent. |
| `live_scheduler` | Proved/reusable Q1/Q7 legal issue and progress cases; conditional bounds and cap limits remain as stated. |
| `maintenance`, `admitted_timing_recovery` | Proved/reusable owning REFpb, Q1 normal and Q8 hot traces; complete ownership/reset interval observation absent. |
| `profile_admission` | Proved/reusable owning Q5 validation and Q2 geometry/width rejection; not every possible invalid field permutation. |
| `inline_ecc` | Proved/reusable Q5 component and corrected Q9 combined subset; dirty management boundaries above absent. |
| `link_ecc` | Proved/reusable component matrices and outgoing parity; returned-link combined Q10 stale. |
| `posted_strict`, `mr43_observed_phases`, `physical_write_link` | Proved/reusable standalone epoch/MR43 and outgoing parity checks; conservative composed completion documented, early-posted MR43 exposure absent. |
| `transfer_recovery` | Proved/reusable Q9 source/image/settlement and fresh service; continuous independent failure/reset protocol observation absent. |
| `apb_schema`, `atomic_config` | Proved/reusable canonical owning Q5 and accepted Q0 combined publication; later selected-family terminal success has limited retained details. |
| `boot_recovery` | Proved/reusable Q9 dirty recovery, earlier accepted Q0 and Q6 reset cases; Q6 scheduler boundary stale. |
| `update`, `pm_fsp_master`, `live_management_ports` | Proved/reusable Q5 owning boundary checks and representative Q0 integration; real dirty-state/crossing failure observations above absent. |
| `benchmark` | Proved/reusable six reviewed Q1 milestones with CK4-write retained prerequisites. |
| `caps_fairness` | Proved/reusable Q7 all-class fairness/concurrent cap subset; historical zero/six-variant source impact stale. |
| `nonbenchmark` | Proved/reusable Q2/Q3/Q8/Q9/Q11 finite measurements; any further unrepresented workload must be identified before a run. |
| `reproduction` | Prior source-bound reproductions reusable for their exact source; complete final separate-checkout reproduction absent. |

### Nonbenchmark measurements

Q11 already records mixed, row-changing, response-backpressured and maintenance-bearing finite traffic. Q2 maximum and ratio-two cases observe real rank switches (5/1 and6/1 by channel) and report separate rates, latency, stalls and commands. Q3 includes seeded random/narrow/partial/error traffic costs. Q8 has a separate completed hot segment after MR4=13. These measurements include source pacing and are not sustained benchmarks or isolated feature penalties.

New bounded accounting of Q9's existing two traces records 384 read+552 successful strobed write bytes before recovery, eleven writes with one expected failure,109 B-stall samples and a 6.341-us shared interval. Recovery records 64 bytes in each direction over 0.6085 us. Failed-write bytes earn no payload credit. `closure-20261112/inline-costs.json` binds both trace hashes and the unchanged accountant; no simulation or successful correctness/protocol replay was repeated. No new nonbenchmark simulation is selected until a specific unrepresented workload is identified from the remaining boundary review.

## Minimum remaining work and runnable selections

These are proposals for closing specific gaps. No command in this section has been launched by the documentation reconciliation. Existing successful hardware simulations and production replays are not selected for repetition. Use fresh evidence directories and the prepared pinned tool environment described in [contracts.md](contracts.md#tools-and-commands). Build output remains under `DHENARA_AGENT_TOOL_STORAGE_ROOT`; retain reports/logs/traces separately.

1. **Resolve the capability comparison first.** The lead must disposition required forwarding/merging against the observed serialized implementation. Also reconcile conservative posting and serialized-inline invariants with the accepted contract. An implementation change or agreement amendment requires its proper authority; there is no current runnable command that can prove missing forwarding hardware. Development time is unknown; no retained comparable implementation establishes an estimate.
2. **Finish bounded source/evidence reconciliation.** Compare the cap/reset scheduler changes and surviving management assertions before requesting another simulation. Produce per-file impact and exact unproved assertions. Reuse Q2/Q4/Q5/Q7/Q8/Q9 wherever those boundaries are unchanged. This is source/report inspection, with no hardware-run time promised. If the historical cap inputs are insufficient, label that exact limit instead of repeating unrelated cases.
3. **R1: one clean current-source read-link case.** This is the minimum proposed expensive run after source stability. Prefer to execute it from the eventual separate final checkout so it also contributes fresh reproduction evidence. Development comparison was 64.64s compilation+114.09s simulation; allow roughly 3–6 minutes on a comparable environment, plus any observed new replay overhead. Retain clean commit/dirty check, source manifest, literal command, all logs, raw parity/source traces, fault counts, independent mutations and costs. This covers mode4, not the entire ECC requirement.

```sh
EVIDENCE="$PWD/ips/lpddr5_mc/evidence/closure-read-link-new"
python3 ips/lpddr5_mc/dv/integration/check.py --family datapath \
  --ports 1 --channels 1 --ranks 1 --integrity-mode 4 --profile-number 16 \
  --report "$EVIDENCE/report.json" --build-name closure-read-link-new
```

4. **R2: cap fallback only if source impact cannot close it.** For one identified zero-cap direction/mode, the existing command below retains full cap observations, independent legality/data replay, mutations and the settled tail. Vary only the identified `--cap-mode 1|2` and `--zero-cap-direction read|write`; do not assume one case proves all four. Retained mode2-write took 123.96s compilation+115.30s simulation, so roughly 4–6 minutes per fresh comparable case. A required 32-byte strict or borrowing case uses the supported `--cap-granule-bytes 32 --cap-traffic phase|mixed|concurrent` options after choosing the exact missing invariant; historical six-case durations were 185–308s each. Neither a four-case matrix nor six-case rerun is currently selected.

```sh
EVIDENCE="$PWD/ips/lpddr5_mc/evidence/closure-zero-write-new"
python3 ips/lpddr5_mc/dv/integration/check.py --family datapath \
  --cap-mode 2 --zero-cap-direction write \
  --report "$EVIDENCE/report.json" --build-name closure-zero-write-new
```

5. **R3: reset fallback only for an identified scheduler interaction.** `--runtime-reset-case N` isolates one of 0–9 cases with surviving AXI settlement and fresh epochs/service. The ten-case retained run took 234.34s compilation+774.66s simulation; no isolated-case timing is inferred by dividing that total. One case is bounded above by that historical matrix workload only as a planning comparison, not a guaranteed runtime. Its unchanged fixture still aborts memory tracking at reset. Independent cross-boundary protocol qualification needs an observer/fixture correction first; no current command claims that absent observation. The new fixture's implementation/runtime cannot be estimated from an unbuilt design.

```sh
EVIDENCE="$PWD/ips/lpddr5_mc/evidence/closure-reset-case8-new"
python3 ips/lpddr5_mc/dv/integration/check.py --family controller \
  --runtime-reset-case 8 \
  --report "$EVIDENCE/report.json" --build-name closure-reset-case8-new
```

6. **Final reproduction and release review follow closure.** Create an ordinary separate checkout of the exact final committed source with a unique isolated build root. First compare unchanged RTL/config/checker/fixture hashes to retained reports, then run the specifically approved missing/changed selections there. The existing aggregate supports repeated `--step` names and `--require-clean`; it does not automatically include the broad matrix or benchmarks. A selected run proves only those steps. The complete agreed-matrix reproduction obligation remains open until its source/reuse/new-execution evidence is reviewed together. No full-suite rerun is selected here. The accepted final source, correction plan and selection determine time; adding six approximately 4000-second simulations without an affected-source reason is not the proposed minimum.

For an inventory of available aggregate steps without launching simulation:

```sh
python3 ips/lpddr5_mc/tools/check_controller.py \
  --evidence "$PWD/ips/lpddr5_mc/evidence/closure-selection-new" \
  --build-name closure-selection-new --dry-run
```

Documentation adoption uses relative-link/anchor/source-reference checks, preserved specification hashes, generated-source consistency and `git diff --check`. Mermaid fences and source syntax are inspected; graphical rendering has not been checked. These checks preserve source integrity and navigation, while the release remains pending for the specific gaps above.
