# Independent system verification environment

This environment qualifies independent observation models, adapters, bus agents and their sensitivity fixtures. The supplied source baseline contains the [accepted reference foundation](../spec/contracts.md), with no production controller. A passing environment report sets `controller_qualified: false`. Actual combined controller correctness, CDC/reset behavior, coverage closure and throughput require later execution against that controller.

The [coverage map](../../dv/system/coverage.json) accounts for every accepted requirement family, identifies executable environment and foundation tests, and records the remaining combined tests. The runner validates every cited test identity and the result of each environment test. A foundation reference is not an extra controller pass. The full runner executes the existing foundation flow unchanged and records its separate result.

## Prepare and run

Use the [foundation tool preparation](contracts.md#tools-and-commands): Python 3.13.18, Ruff 0.17.4, checksum-pinned Debian Verilator 5.006-3, GCC/G++ 12.2 and make 4.3 on Linux amd64/Debian 12. The system fixture additionally qualifies Verilator's timed tasks, independent clocks, canonical packed payloads, assertions and deliberate assertion failures. No licensed simulator, Docker daemon or vendor model is required.

Run from the repository root, retaining full Git ancestry:

```sh
export DHENARA_AGENT_TOOL_STORAGE_ROOT=/absolute/path/to/disposable-tools
make -f ips/lpddr5_mc/Makefile prepare
python3 ips/lpddr5_mc/tools/rtl/check.py \
  --evidence /absolute/path/to/new/evidence-directory \
  --build-name system-primary \
  --require-clean
```

The evidence directory must not exist. Compiler output, bytecode and tool caches stay under the absolute tool-storage root. Use a different build name for a separate clean reproduction. `--skip-foundation` is an iteration aid and does not establish the full available-source check. The non-mutating flow runs Ruff lint/format checks, environment tests and coverage-reference validation, the timed SV fixture, host JSONL replay, deliberate unstable-B rejection, whitespace and the complete existing foundation flow. Each command has a 300-second timeout; the SV fixture also has a two-microsecond simulation watchdog. Failures propagate and remain in the new evidence directory.

The fixture manifest is [files.f](../../dv/system/rtl/files.f). Its two AXI clocks have different periods/phases; APB and CK have separate periods/phases. AXI agents stall W and B/R; the toy APB endpoint inserts wait states and checks strobes and errors. The buffered memory endpoint emits its actual byte effects only after full write validation. It is a small environment fixture, with four-beat INCR transfers and separate per-port memories. It contains neither an LPDDR5 controller nor asynchronous CDC transports. Reset-phase variation, overlapping asynchronous controller traffic and reset propagation remain combined tests.

The SV compile uses `--timing --assert -Wall`. It waives imported-package timescale differences and intentionally unused fixture metadata only. Width/latch/assertion and other warnings remain failures. `+bad_stability` deliberately changes B while stalled; success requires both a failing process and the expected stability assertion diagnostic. The reusable [observer](../../formal/system/lpddr5_mc_observer.sv) also rejects VALID withdrawal. It is an executed simulation assertion, with no formal proof claimed.

## Models and adapters

| Owner | Observations and checks | Current limits |
| --- | --- | --- |
| [host.py](../../dv/system/host.py) | Independent byte ranges, complete AW/W validation, physical byte witnesses, serial ordering, forwarding dependencies, physical/integrity prerequisites, stable response exposure and actual handshakes, fatal settlement | Global source-edge ordering is supplied by the adapter. Merged physical writes need the additional observer described below. |
| [dram.py](../../dv/system/dram.py) | Literal 14-bit CA decode; adjacent ACT/MRW/CAS pairs; selected bank, column, activation, thermal and refresh constraints; split pin beats; masked/write-DBI memory effects | Partial protocol model, not a complete JEDEC model. Full read-to-write/rank turnaround, MR/MPC/ZQ and power-state timings need extension before protocol closure. |
| [phy.py](../../dv/system/phy.py) | Explicit command/data path delays; pin-memory reads; independently skewed/gapped/rotated byte slices; DNV and deadlines; raw read parity or read DBI; isolated waveform capture | Ideal digital phase. Callers must bind real enables, WCK/CS observations and management pins. `write_word` consumes wire data/masks but does not itself perform write-link correction. |
| [ecc.py](../../dv/system/ecc.py) | Complete raw link groups, independent JEDEC-matrix checking; physical inline data/check outcomes, RMW locks, poison and versioned correction scrub | The existing foundation covers reserved check capacity/cache pressure. Actual physical mapping and dirty-cache eviction still need controller bindings. |
| [ownership.py](../../dv/system/ownership.py) | Reserved channel-to-host transfer, generation/boot/config identity, distinct exposure/handshake, per-rank sixteen-write MR43 epochs, retained posted history and late errors, destructive settlement | Whole-granule lifetime seam. Production burst-to-granule identity correlation and feedback crossings must be attached. |
| [control.py](../../dv/system/control.py) | All-channel prepare/ACK/commit, abort/recovery epochs, retained first diagnostic; controllable update/PM/FSP/master response scripts | CRC/epoch/drain observations are explicit inputs. Scripts are sampled stimuli, not a live vendor PHY or proof of actual drain. |
| [admission.py](../../dv/system/admission.py) | Preacceptance range reservations, disjoint grants, credit pressure, acknowledged revocation races, source fences/reset | Conservative observable grant contract, independent of a particular CDC algorithm. It does not prove metastability or actual grant implementation. |
| [adapters.py](../../dv/system/adapters.py) | Canonical `lpddr5_mc_return4_t` field unpacking, ratio-two inactive-valid rejection and independent return-oracle binding; sampled APB setup/wait/completion checked against canonical `RegisterBank` | APB caller supplies decoded channel/register, owner status and publication events. Final producer module wiring is not present in this source. |
| [measurement.py](../../dv/system/measurement.py) | Completed-payload windows, occupancy/credits/backlog, stall causes, byte caps and conditional fairness through the trace tail | Measurement alone does not establish legal memory/protocol traffic or actual RTL throughput. |

These models import independent algorithms from `dv/contracts`, never production controller algorithms. Shared JEDEC matrices and numeric profiles remain canonical read-only inputs. The command decoder is a separate literal decoder rather than the command packer's inverse implementation. `WaveCapture` reconstructs actual edge observations and passes them to the independent half-WCK waveform oracle; generated waveform fixtures establish its sensitivity only.

`DigitalPhy.command` applies the explicit CA path delay. Its decoded `Burst` retains physical rank/bank/row/column/beat times. `write_word` uses the explicit write serialization delay; `schedule_read` samples pin memory, emits raw read parity from the standard matrix when enabled, or performs read DBI, and adds configured slice skew/gaps. Read link excludes read DBI. Each `ReturnedSlice` carries two memory beats for one byte slice, with matching two-bit parity, DMI and DNV. `returned` controls quotas and rotation per slice; a delayed word cannot silently pass its deadline. `reset_pointer` refuses to discard live words unless explicitly invoked as a fatal abort.

A canonical return snapshot contains integer field values `data` (128 bits), `valid` (8 bits), `dnv` and `link_parity` (16 bits each), with word zero in the least significant bits. Each 32-bit data word contains two x16 beats; slice zero takes bits 7:0 and 23:16. Parity/DNV ordering is two beats by two byte lanes. `returned_snapshot` preserves that interleaving and `observe_return` feeds the independent enable/deadline/rotation oracle. Capture all active lanes on every CK, including valid gaps. Do not fabricate word identities from completion messages.

## Source order and response ownership

Observe AW/AR only at actual source AXI `VALID && READY` edges. Assign a testbench correlation key there, and preserve chronological order across independent clocks. At an exact common sample instant, port zero precedes one and AW precedes AR per port. CDC FIFO insertion/import is not that order. The trace adapter buffers one bounded sampled instant and sorts its admissions by these ties; it never uses crossing arrival to reorder source times. A full AW range exists before its first W. Observe each port's W handshakes in AW order.

The source-order sensitivity test rejects an overlapping read overtaking an earlier write. The grant monitor tests revocation racing source acceptance, credit exhaustion, disjoint concurrent reservations and stale reset epochs. These are synthetic monitor tests; actual phase/skew/reset variation and a deliberately faulty CDC implementation remain pending. A centralized or partitioned admission implementation is free to optimize disjoint traffic while preserving the observable order and the unchanged benchmark.

`HostScoreboard.forwarded` overlays every older fully buffered overlapping write in source order, byte by byte. Successful retirement waits for all contributing physical and integrity outcomes, including overwritten contributors; any failed dependency prevents success. The generic host trace currently expects individual physical write witnesses. Integrating physical merging requires an observer that validates identical region/rank/ECC/profile context, absence of intervening overlapping reads, final byte composition and every original response dependency. Until attached, merged controller traffic cannot be qualified with this trace path.

Physical completion, integrity outcome, transfer into reserved host storage, source BVALID exposure and source B handshake are different events. `LifetimeMonitor.transfer` can free a channel slot only after a reserved owner inherits the obligation. It does not expose a response. MR43 membership/history survives posted response handshake and slot reuse, independently for every rank. Failure before exposure changes the unoffered response to SLVERR; after exposure the response remains stable and the applicable late error is recorded. Failed operations may transfer an error before pin completion so fatal settlement can finish; destructive recovery still requires host settlement and explicit reset confirmation.

Posted OKAY may wait for integrity, including conservative asynchronous `POSTED_EARLY=0`; there is no required early-B latency. Only actual source BVALID marks exposure. A clean integrity result does not make an internally queued OKAY immune to a later global fatal/ownership fault. The environment tests that race before/after transfer and exposure, prolonged stalls, reset and stale identity feedback. A deliberately unoffered OKAY after fault is rejected. Actual same-edge arbitration and delayed feedback through producer clocks remain combined tests.

## Host trace format

[trace.py](../../dv/system/trace.py) accepts UTF-8 JSONL with one version-one header:

```json
{"kind":"header","version":1,"bus_bytes":16,"ports":2,"capacity":64,"source":"actual_source_edge_adapter"}
```

All subsequent records have integer `ps` in simulator source-edge picoseconds, a `kind`, and exactly the fields below. Times are nondecreasing. Addresses/data/strobes/enables accept integers or base-prefixed numeric strings. Keys correlate observations; they are not expected data. Reset clears keys and increments the scoreboard epoch. A line is limited to 65536 characters, and a sampled instant to 4096 events.

| Kind | Required fields besides `kind`, `ps` |
| --- | --- |
| `aw`, `ar` | `key`, `port`, `id`, `address`, `length`, `size`, `burst`, `lock` |
| `w` | `port`, `data`, `strobe`, `last` |
| `pin` | `key`, `address`, `hex`, `enables`, `epoch` |
| `physical` | `key` |
| `integrity` | `key`, `failed` |
| `b` | `key`, `response`, `ready`, `posted` |
| `r` | `key`, `data`, `response`, `last`, `ready` |
| `fault` | No additional fields |
| `reset` | `destructive` |
| `end` | No additional fields |

`pin.hex` is ascending-address byte order, two hexadecimal digits per byte. Its enable bit zero selects the first byte. Physical events must come from the external memory/pin observer, not a DUT `done` flag. `b`/`r` describe every sampled VALID cycle, including repeated stalled payloads; `ready` records the actual handshake. Missing records cannot be inferred from a trace. End-of-trace rejects unsettled host counts and unresolved posted integrity. Separate WCK/CA, ECC, grant and lifecycle monitors must also finish; a host trace pass alone does not prove their behavior.

Replay an independently captured trace with:

```sh
PYTHONPATH=ips/lpddr5_mc/dv python3 -m system.trace \
  /absolute/path/to/trace.jsonl --report /absolute/path/to/new/trace-report.json
```

## Performance accounting

The benchmark configuration and legality basis remain [performance.md](../spec/performance.md). Drive two AXI128/400 ports and two x16 CK800/WCK3200 channels, one rank/channel, balanced aligned BG BL32 64-byte full bursts, ECC/DBI off, ready responses, 64 credits/channel/direction and the declared feedback/CDC allowance. Finite successive rows and REF/MR4/ZQ remain mandatory. The existing periodic command/resource reference complements actual host payload measurement; reference counts cannot replace it.

`Benchmark` admits only 64-byte observations and counts a completed burst at its final R handshake or successful B handshake after scoreboard validation. Run at least 100 periods of 3000 CK. Discard periods zero through nine and measure the next 90 integral periods, the half-open CK interval `[30000,300000)`. Later tail completions release bookkeeping but add no measured bytes. Reads and writes have separate reports for CK, CK/2 and CK/4 selection. Reference payload is 375 bursts/channel/period for CK or CK/2 (12.8 GB/s combined), or 339 for CK/4 (11.5712 GB/s); the criterion is at least 99% of the applicable bytes. No synthetic result here is an RTL performance claim.

The report retains completed bytes by channel/period, missing bytes, threshold disposition, actual duration conversion at 1.25 ns/CK, named stall causes, maximum outstanding count and every measured boundary backlog. Credit overflow, unmatched/duplicate completion, data/integrity failure, truncated observation, missing boundaries, growing endpoint backlog or missing stall explanations fail independently of average rate. A subthreshold run returns `threshold_passed: false`; the combined qualification caller must treat that as failure. Actual clock/configuration validation, steady-source saturation, complete stall attribution and command legality remain obligations of the producer binding.

Caps charge actual 32/64-byte issues in CK windows. Zero disables a direction even with borrowing enabled; borrowing otherwise requires no eligible opposite traffic and available combined byte budget. Fairness checks every eligible CK including the final tail, reports suspended intervals, and rejects an unproved issue gap `G`. The conditional bound is `(A+1)*T + D*G`, 4360 eligible CK for D64/A32/T8/G64. Check every class/port separately. Mixed/random, partial/narrow, ECC, thermal, rank-switch, maintenance and backpressure traffic needs correctness plus separate performance reports; the benchmark rate is not promised for those workloads.

## Evidence and integration handoff

`report.json` records source commit/dirty state, tracked IP file hashes, tools, commands, exit statuses, durations and limits. `tests.json` retains every executed environment test and the validated coverage map. `rtl.log`, `rtl.jsonl` and `trace.json` retain the positive fixture; `sensitivity.log` retains the deliberate rejection. The separate foundation report/log records ancestry, preserved-document hashes, generated consistency, reference matrices/schedules, C11/SV representation and relative Markdown links. Earlier failed runs remain separate evidence.

Commit the deliverable, run the full flow with `--require-clean`, then reproduce the exact commit in a separate full-history clone with independent Git metadata and isolated build output. Compare commit, clean status, all source hashes and substantive test/trace/coverage results. Commands/durations and absolute build paths naturally differ. A local clone using the same installed tools establishes source reproducibility in that environment; fresh-host preparation and remote CI are separate, unexecuted checks.

The integration owner must connect the actual host/channel top-level clocks/resets and canonical bus/configuration/completion/feedback ports, supply memory/PHY observations and live management responses, repair combined failures, and run the coverage map's pending cases. The early producer interface notes describe evolving source outside this isolated checkout; no final binding or producer behavior is inferred from those notes. Broad supported-parameter testing, actual 100-period throughput, synthesis/PPA, formal CDC/reset proof, vendor PHY/electrical and physical qualification have not been established by these fixtures.
