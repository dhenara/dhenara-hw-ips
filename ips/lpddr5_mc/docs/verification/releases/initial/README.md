# LPDDR5 controller: final verification report

**Status: accepted, simulation-qualified first release. All eight acceptance checks passed.** The Mission completed on 16 November 2026 at accepted source [`ba8f5142fd9f76809a78badfd2a7c06736e2f187`](https://github.com/dhenara/dhenara-hw-ips/commit/ba8f5142fd9f76809a78badfd2a7c06736e2f187). The published merge [`f324c09`](https://github.com/dhenara/dhenara-hw-ips/commit/f324c093dba01debfc393744301e4d2fd18805aa) preserves the identical source tree, `6994755d9edb6799b949e20348a16537ccf64fbf`.

The release delivers an integrated, technology-independent LPDDR5 memory controller with AXI4/APB4 interfaces, channel scheduling, inline and link ECC, refresh, power management and recovery. This report connects the accepted specification, executed qualification and independent review. The original results remain available alongside it and in the [downloadable evidence](https://github.com/dhenara/dhenara-hw-ips/releases/tag/lpddr5-initial-2026-11-16).

## Qualification at a glance

| Check | Completed result | Original record |
| --- | --- | --- |
| Exact-source reproduction | **22 of 22 steps passed**, from a separate clean Git checkout; source and tool maps unchanged | [Combined report](records/combined-report.json), [execution](records/combined-execution.json) |
| Independent integration review | **394 integration tests passed** on the accepted source; complete read/CK4 and write/CK4 traces independently replayed | [Lead behavior audit](records/lead-behavior-audit.json), [original lead review](records/lead-review.txt) |
| Final benchmark reconciliation | **Six of six complete benchmark replays passed** with original trace hashes and baseline equality | [Replay execution](records/benchmark-replay-execution.json), [benchmark reports](records/benchmarks/) |
| Report and source audit | 44 substantive nested reports and 319 execution records checked; 332-file source snapshot reconstructed against the accepted Git tree | [Lead report audit](records/lead-report-audit.json), [source identity](records/accepted-source-tree.json) |
| Evidence integrity | All **4,176 final archive members** verified against recorded sizes and SHA-256 values | [Package audit](records/lead-package-audit.json), [member manifest](records/package-manifest.json) |
| Mission acceptance | Six requirement checks, release evidence and source integrity **accepted**; Mission completed | Requirement disposition below and completed-Mission snapshot in the [repository README](../../../../../../README.md) |

## Specification and review

The work began with the preserved microarchitecture specification and addendum. A targeted specification review identified and resolved implementation-critical questions around command timing and clock ratios, split-burst accounting, AXI ordering and completion, ECC ownership, refresh and recovery. The resulting [reviewed controller contracts](../../../spec/contracts.md), [supported profiles](../../../spec/profiles.md), [interfaces](../../../spec/interfaces.md), [host/ECC behavior](../../../spec/host-ecc.md), [lifecycle](../../../spec/control.md) and [performance criteria](../../../spec/performance.md) form the accepted implementation basis.

The controller supports the documented one/two AXI-port, one/two x16-channel and one/two-rank options, 64/128/256-bit host interfaces and 32/64-byte granules. Numeric digital PHY profiles define the supported LPDDR5/DFI timing and capability combinations. Owning checks and representative integrated configurations exercise these contracts; the acceptance scope is the documented first-release profile set.

The verification uses separate protocol, data, integrity and lifecycle checkers, deliberate invalid-input and corruption probes, source-bound regression evidence and a separate lead review. The lead reconstructed the delivered source and evidence, audited the retained reports, and independently ran the 394 integration tests plus the two complete CK4 replays. The [joint behavior review](records/joint-behavior-review.txt) maps the required behaviors to their owning and integrated checks.

## Requirement-to-result disposition

| Accepted check | Result | Evidence supporting acceptance |
| --- | --- | --- |
| AXI memory correctness | **Passed** | Host ordering, error/response settlement, overlap and credits; representative parameter and stress cases; inline composition; fresh host-foundation/controller/datapath reproduction. |
| LPDDR protocol legality and progress | **Passed** | Command, pin, WCK, profile and maintenance checks; selected continuous fault/reset observations; timing/protocol mutation sensitivity; complete benchmark trace checks. |
| ECC fault handling and recovery | **Passed** | Inline/link arithmetic and raw transport checks, corrected reads, scrub/rewrite and poison handling; selected composed fault, lifecycle and recovery cases. |
| Management transitions | **Passed** | APB configuration/publication, clock/reset transport, refresh/RFM, power-down/self-refresh, FSP decline/timeout and PHY ownership cases. |
| Throughput and fairness | **Passed within the accepted workload and policy conditions** | Six full original benchmark simulations and final checker replays; thirteen cap and eighteen fairness selections, with retained turnover, age, credit and sensitivity checks. |
| Reproducible delivery | **Passed** | Exact-final clean-checkout 22-step suite, unchanged source/tool maps, all six complete benchmark replays, original-specification hashes and generated-source consistency. |
| Release evidence | **Passed** | Final worker review and independent lead review jointly support the six requirement checks against the delivered source. |
| Available source integrity | **Passed** | Exact committed source, source snapshot, generated artifacts, report bindings and published merge tree agree. |

The [worker review](records/worker-review.txt), [lead review](records/lead-review.txt) and [source applicability review](records/source-applicability-review.json) contain the detailed reasoning. The [historical qualification ledger](../../qualification.md) retains every recorded check and correction without rewriting original failures.

## Measured throughput

| Scheduler selection | Read GB/s | Write GB/s | Reference GB/s | Result |
| --- | ---: | ---: | ---: | --- |
| CK | 12.8000000 | 12.7926044 | 12.8000 | Passed |
| CK/2 | 12.8000000 | 12.7935526 | 12.8000 | Passed |
| CK/4 | 12.8000000 | 12.7918459 | 11.5712 | Passed |

The workload uses two 128-bit AXI ports at 400 MHz, two channels, one rank per channel and plain data. CK is 800 MHz; CK/2 and CK/4 select scheduler intervals while the command engine continues at CK. Each original HDL benchmark ran 100 periods, excluded ten warm-up periods and measured 90 complete periods without tail-drain credit. All six meet the accepted 99% threshold. The final verification replayed the complete original traces with the accepted checker; those replays are distinct from new HDL benchmark runs.

The reports retain the measured backlog, credit and complete-AW-turnover checks. The original row-write G64 fairness failure remains a failed historical case; the accepted row-write policy uses its separately verified G96 condition. Other workloads and ECC modes have their own recorded coverage rather than inheriting these throughput values.

## Reproduce and inspect

The [repository README](../../../../../../README.md#run-the-checks) gives the exact checkout and full 22-step command. The qualified environment is Linux amd64, Python 3.13.18, Ruff 0.17.4, Verilator 5.006-3, GCC/G++ 12.2.0 and Make 4.3. The original [selection](records/reproduction-selection.json) and execution records retain commands, tools, seeds and source hashes.

Download the [release assets](https://github.com/dhenara/dhenara-hw-ips/releases/tag/lpddr5-initial-2026-11-16):

- `lpddr5-initial-ba8f514-evidence.tar.gz`: the original final archive, assembled without altering its bytes from the thirteen retained parts. Includes the exact source snapshot, final reports/logs/raw observations, original benchmark inputs, launchers and prior reviews.
- `lpddr5-initial-independent-review.tar.gz`: the original independent review checks and logs.
- `lpddr5-initial-supporting-evidence.tar`: earlier qualification packages, component evidence, source reconciliation and review records, preserving their original paths and bytes. This includes historical failures and their separately recorded corrections.
- `verification-runtime-notices.zip`: the project and third-party licenses accompanying the original simulator executables; see [runtime notices](../../../../../../docs/verification-runtime-notices.md).
- `SHA256SUMS`: checksums for the published assets. The [record hashes](record-hashes.json) cover the originals copied into this repository; the [final member manifest](records/package-manifest.json) covers the 4,176 final archive members.

The [supporting-record index](supporting-records.json) maps 1,379 original evidence files to their preserved archive paths and hashes.

After downloading the three evidence assets, runtime-notices ZIP and `SHA256SUMS` into the same directory, check the transfer before extracting:

```sh
sha256sum -c SHA256SUMS
```

The final archive SHA-256 is `53cf115163d26333aa97ef38fa1be54324029d95b3541506658d26a8106c8b6e`. Asset paths such as `reproduction/combined/`, `reproduction/benchmarks/`, `original-benchmark-inputs/` and `prior-reviews/` make the original reports and traces discoverable. Recorded absolute runtime paths describe the original environment and can be relocated for inspection.

## Qualification scope and next stage

The accepted result establishes digital simulation behavior within the documented profiles and representative configuration/fault families. The detailed reports retain selected raw double-fault coverage, conditional fairness and recovery-observation boundaries; a supported parameter range is not an exhaustive Cartesian test claim. Physical CDC/timing sign-off, vendor-PHY integration, synthesis/PPA and silicon validation are the next qualification stages. Fresh-host installation and remote CI qualification are also separate from the completed clean-checkout run in the installed environment.

Original worker and lead reviews are preserved byte-for-byte as text records. They were written before the administrative acceptance/closeout completed; their then-pending acceptance statements are historical. The Mission subsequently accepted the same source and results on 16 November 2026, without changing the tests, thresholds or controller implementation. Some older detailed outputs were not retained; later source-bound evidence and its explicit limits determine acceptance. Post-execution runtime snapshots establish packaging-time custody, not additional execution-time instrumentation.

## Development metrics

The dashboard snapshot through publication records **3d 17h 54m of execution** and **US$1,172.14 of recorded model usage**. Dhenara Engineering coordinated OpenAI models through Codex across implementation, regression and review. See [measurement notes](run-metrics.md) for the API-equivalent usage valuation, initial/publication split and accounting completeness.
