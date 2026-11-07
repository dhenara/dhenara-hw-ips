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

The evidence directory must not exist. Compiler output, bytecode and tool caches stay under the absolute tool-storage root. Use a different build name for a separate clean reproduction. `--skip-foundation` is an iteration aid and does not establish the full available-source check. The non-mutating flow runs Ruff lint/format checks, environment tests and coverage-reference validation, the timed SV fixtures, streamed read/write measurement replay, host JSONL replay, deliberate unstable-B rejection, whitespace and the complete existing foundation flow. Each command has a 300-second timeout; the original SV fixture has a two-microsecond simulation watchdog and the 100-period streaming fixture has a 450-microsecond watchdog. Failures propagate and remain in the new evidence directory.

The fixture manifest is [files.f](../../dv/system/rtl/files.f). Its two AXI clocks have different periods/phases; APB and CK have separate periods/phases. AXI agents stall W and B/R; the toy APB endpoint inserts wait states and checks strobes and errors. The buffered memory endpoint emits its actual byte effects only after full write validation. It is a small environment fixture, with four-beat INCR transfers and separate per-port memories. It contains neither an LPDDR5 controller nor asynchronous CDC transports. The separate streaming fixture varies clock periods, phase and reset release through plusargs. Overlapping asynchronous controller traffic and reset propagation remain combined tests.

The SV compile uses `--timing --assert -Wall`. It waives imported-package timescale differences and intentionally unused fixture metadata only. Width/latch/assertion and other warnings remain failures. `+bad_stability` deliberately changes B while stalled; success requires both a failing process and the expected stability assertion diagnostic. The reusable [observer](../../formal/system/lpddr5_mc_observer.sv) also rejects VALID withdrawal. It is an executed simulation assertion, with no formal proof claimed.

## Models and adapters

| Owner | Observations and checks | Current limits |
| --- | --- | --- |
| [host.py](../../dv/system/host.py) | Independent byte ranges, complete AW/W validation, physical byte witnesses, serial ordering, forwarding dependencies, physical/integrity prerequisites, stable response exposure and actual handshakes, fatal settlement | Global source-edge ordering is supplied by the adapter. The separate merge observer validates composition and retains each original response obligation. |
| [dram.py](../../dv/system/dram.py) | Literal 14-bit CA decode; adjacent ACT/MRW/CAS pairs; selected bank, column, activation, thermal and refresh constraints; split pin beats; masked/write-DBI memory effects | Restricted digital model, with admitted MR/refresh/power/turnaround checks described below. Training MPCs and unadmitted operands fail explicitly; a complete vendor model is not claimed. |
| [phy.py](../../dv/system/phy.py) | Explicit command/data path delays; pin-memory reads; independently skewed/gapped/rotated byte slices; DNV and deadlines; raw read parity or read DBI; isolated waveform capture | Ideal digital phase. Callers must bind real enables, WCK/CS observations and management pins. `write_word` collects raw DQ/DMI/parity, corrects complete link groups and applies decoded mask/DBI before memory updates. |
| [ecc.py](../../dv/system/ecc.py) | Complete raw link groups, independent JEDEC-matrix checking; physical inline data/check outcomes, RMW locks, poison and versioned correction scrub | The existing foundation covers reserved check capacity/cache pressure. Actual physical mapping and dirty-cache eviction still need controller bindings. |
| [ownership.py](../../dv/system/ownership.py) | Reserved channel-to-host transfer, generation/boot/config identity, distinct exposure/handshake, per-rank sixteen-write MR43 epochs, retained posted history and late errors, destructive settlement | Whole-granule lifetime seam. Production burst-to-granule identity correlation and feedback crossings must be attached. |
| [control.py](../../dv/system/control.py) | All-channel prepare/ACK/commit, abort/recovery epochs, retained first diagnostic; controllable update/PM/FSP/master response scripts | CRC/epoch/drain observations are explicit inputs. `ManagementPort` binds sampled request/response pins to clocked scripts and monitors observed release/ownership. It does not prove actual producer drain. |
| [admission.py](../../dv/system/admission.py) | Preacceptance range reservations, disjoint grants, credit pressure, acknowledged revocation races, source fences/reset | Conservative observable grant contract, independent of a particular CDC algorithm. It does not prove metastability or actual grant implementation. |
| [adapters.py](../../dv/system/adapters.py) | Canonical `lpddr5_mc_return4_t` field unpacking, ratio-two inactive-valid rejection and independent return-oracle binding; sampled APB setup/wait/completion checked against canonical `RegisterBank` | APB caller supplies decoded channel/register, owner status and publication events. Final producer module wiring is not present in this source. |
| [measurement.py](../../dv/system/measurement.py) | Completed-payload windows, occupancy/credits/backlog, stall causes, byte caps and conditional fairness through the trace tail | Measurement alone does not establish legal memory/protocol traffic or actual RTL throughput. |

These models import independent algorithms from `dv/contracts`, never production controller algorithms. Shared JEDEC matrices and numeric profiles remain canonical read-only inputs. The command decoder is a separate literal decoder rather than the command packer's inverse implementation. `WaveCapture` reconstructs actual edge observations and passes them to the independent half-WCK waveform oracle; generated waveform fixtures establish its sensitivity only.

`DigitalPhy.command` applies the explicit CA path delay. Its decoded `Burst` retains physical rank/bank/row/column/beat times. `write_word` uses the explicit write serialization delay; `schedule_read` samples pin memory, emits raw read parity from the standard matrix when enabled, or performs read DBI, and adds configured slice skew/gaps. Read link excludes read DBI. Each `ReturnedSlice` carries two memory beats for one byte slice, with matching two-bit parity, DMI and DNV. `returned` controls quotas and rotation per slice; a delayed word cannot silently pass its deadline. `reset_pointer` refuses to discard live words unless explicitly invoked as a fatal abort.

A canonical return snapshot contains integer field values `data` (128 bits), `valid` (8 bits), `dnv` and `link_parity` (16 bits each), with word zero in the least significant bits. Each 32-bit data word contains two x16 beats; slice zero takes bits 7:0 and 23:16. Parity/DNV ordering is two beats by two byte lanes. `returned_snapshot` preserves that interleaving and `observe_return` feeds the independent enable/deadline/rotation oracle. Capture all active lanes on every CK, including valid gaps. Do not fabricate word identities from completion messages.

## Timing origins and continuous streams

The correction tests retain the review's four false-pass sequences and check each boundary one CK early and at the legal edge. `DramModel` uses pin CK after the explicit command path delay; it retains rank state and independent command history, so traffic to another bank cannot erase a recovery interval. ACT timing starts at ACT-1; pair completeness is checked at the required adjacent ACT-2. The previous BL16 same-bank regression now tests an actual illegal three-CK gap rather than rejecting the legal four-CK boundary.

| Check | Origin and basis in supplied JESD209-5C |
| --- | --- |
| ACT, PRE and column timing | ACT-1 origin for tRCD/tRAS/tRC/tRRD/tFAW; PRE command origin for tRP/tPPD. Sections 7.3 and 8.2, Tables 340–343. |
| REFpb/RFMpb | Actual maintenance command: tpbR2pbR before another pair; tpbR2act to another bank; target recovery tRFCpb/tRFMpb and ACT-to-maintenance tRRD. Sections 7.5.1/7.7.6, Tables 237/238 and refresh-management separation tables. |
| Read/write turnaround | Actual prior CA, RL/WL and BL/n effective span; tWTR starts after WL plus the complete effective burst, including split BL32. tRTW follows Tables 347–349 for admitted ODT-off profiles, including write-link input use. Pin collisions and rank overlap are checked independently. |
| MR timing | MRW-2 is the model's conservative completion origin; wait tMRW to another MRW and tMRD to other commands. MRR period and data/MR transitions follow Tables 252/372. This adjacent-pair policy can be stricter than the standard's MRW-1 period origin by one CK. |
| Self-refresh | Only idle/recovered banks enter; explicit state rejects ACT/data/maintenance during self-refresh. SRX uses tXSR for bank commands; allowed MR/CAS/MPC operations may occur during recovery under Table 243. Minimum residence is 15 ns rounded up at the selected CK. |
| Power-down | PDE blocks commands until `power_exit` receives observed CS-high, CS-low and CA-low intervals. It checks Table 245 pulse minima in physical time, then conservatively starts tXP at completed-pulse observation and the extra tMRRI before MRR. This fixture policy may delay admission beyond the earliest standard-legal edge. The caller supplies continuous CA-low observation, not a fabricated SRX command. |
| ZQ/FSP | The admitted MR28=0 background mode allows a latch without a new start; no DQ overlaps the latch, and tZQLAT blocks following commands. Command-based calibration has an explicit tZQCAL guard if selected in the model; canonical profiles still admit background mode only. MR16 writes conservatively impose tFC. Unsupported training MPCs fail instead of silently succeeding. |

[StreamMonitor](../../dv/system/stream.py) checks continuous WCK epochs with multiple same-rank/same-direction accesses, synchronization expiry or always-on reuse across all 32 admitted profiles and both directions, every half-WCK edge, full postamble/WS_OFF/disable and every data-clock CS sample. Overlapping same-rank holds merge; split gaps remain asserted. Missing edges, CS holes and wrong-rank transitions are rejected. Epoch setup takes observed preamble edges and accesses decoded by `DramModel`, rather than generating one isolated waveform for each transfer. A new rank/direction requires completed disable and fresh synchronization. Its tests qualify this seam, not actual controller WCK output.

[ManagementPort](../../dv/system/control.py) consumes request, observed response, traffic/drain and WCK state on every CK. It returns the level to drive after that edge. Each instance observes one operation and its idle tail; a new operation uses a new instance. Every active edge, including release and return, requires drain, disabled WCK and no normal traffic. Normal service can resume on the following CK. Request/response reassertion after return, missing samples, stale completion and incomplete trace tails fail.

| Kind | Checked sampled boundary | Remaining binding |
| --- | --- | --- |
| Controller update | Fresh ACK `1 <= a < minimum`; deassertion `a < b <= maximum-2`; registered request first sampled low at `max(minimum,b+1)+1`, or `minimum+1` for decline | Refresh-return budget and competing PHY-update arbitration use the lifecycle references. |
| FSP | Fresh completion low `1 <= a < minimum`; request release `r >= minimum` while completion remains low; later high at `r < c <= r+completion_limit`. Unchanged high with release exactly at `minimum` is a decline and keeps the old profile. | Bind `target=(frequency,ratio,fsp)` and `clock_change_done` on every active sample for target stability, no clock change before acceptance, and a completed clock change on the sample before registered release. Omitted witnesses qualify only the request/response pins; no target/clock behavior is inferred. Profile activation, MR guards and training remain separate lifecycle/publication checks. |
| Low power | ACK in `1..minimum` strictly before release; hold ACK until request release, then return low by `release+completion_limit`. Unacknowledged release exactly at `minimum` is a decline. | Minimum maps to the response bound. The lifecycle owner supplies refresh admission, actual power state and wakeup limits. |
| Training probe | Fresh high-low-high completion with request held through return and released on a later sample; bounded observation window | This is a primitive training-completion probe, not the FSP `init_start` handshake. Actual software token, boot/profile epochs and known-state handover use the recovery monitor. |
| PHY master probe | Fresh ownership pulse within `minimum`, completed before later request release; known returned state required at release | Pins are a normalized ownership pulse. Actual DFI request/ACK/done mapping, type bounds, closed pages, stable rank states and PHY refresh must also pass `contracts.lifecycle.check_master`. |

`minimum=4` and `maximum=32` are the default envelope. `maximum` bounds the fixture request window; `completion_limit` separately bounds FSP/PM return after release and defaults to `maximum`. The FSP/low-power matrix tests compare legal and deliberately invalid sampled traces to the unchanged accepted `contracts.dvfs.check_dvfs` and `contracts.lifecycle.check_low_power` oracles. Positive/negative tests exercise every advertised kind, WCK/drain/traffic throughout ownership, registered clock completion, target stability, timeout, reassertion and unsafe return. The streaming RTL fixture emits seven independently sampled lanes: all five kinds plus valid FSP/PM declines. Its FSP lane releases before completion and binds clock/target observations during replay. These fixtures establish adapter sensitivity; final producer wiring and actual drain/ownership races remain combined checks.

[write_link.py](../../dv/system/write_link.py) keeps corrected DMI separate from payload so a masked zero cannot become an enabled zero. The PHY buffers complete raw write groups, decodes independent DQ and DMI codewords, then interprets masks/DBI and updates pin memory. Single DQ/DMI faults are corrected; an uncorrectable block produces no partial memory update. Tests cover admitted write-link profiles, BL16 masks, split BL32 and DBI.

## Source order and response ownership

Observe AW/AR only at actual source AXI `VALID && READY` edges. Assign a testbench correlation key there, and preserve chronological order across independent clocks. At an exact common sample instant, port zero precedes one and AW precedes AR per port. CDC FIFO insertion/import is not that order. The trace adapter buffers one bounded sampled instant and sorts its admissions by these ties; it never uses crossing arrival to reorder source times. A full AW range exists before its first W. Observe each port's W handshakes in AW order.

The source-order sensitivity test rejects an overlapping read overtaking an earlier write. The grant monitor tests revocation racing source acceptance, credit exhaustion, disjoint concurrent reservations and stale reset epochs. These are synthetic monitor tests; actual phase/skew/reset variation and a deliberately faulty CDC implementation remain pending. A centralized or partitioned admission implementation is free to optimize disjoint traffic while preserving the observable order and the unchanged benchmark.

`HostScoreboard.forwarded` overlays every older fully buffered overlapping write in source order, byte by byte. Successful retirement waits for all contributing physical and integrity outcomes, including overwritten contributors; any failed dependency prevents success. The generic host trace currently expects individual physical write witnesses. [MergeMonitor](../../dv/system/merge.py) validates identical decoded region/channel/rank/ECC/profile/boot/config context, one physical granule, complete original W validation, absence of intervening overlapping reads, final byte composition and every original response dependency. Register each original context before `begin`, observe merged bytes with `pin`, and call `physical` only after all enabled bytes. `begin` exclusively reserves every original until physical completion, rejecting duplicate or overlapping groups even when all resulting bytes would match. It rejects originals with earlier partial/complete physical effects and rechecks their ownership before pin effects and completion. Integrity and B exposure/handshakes remain individual. Disjoint pending merges and new writes after retired originals are supported.

Physical correlation IDs are unique within a host epoch; adapters must include slot generation in that integer ID rather than reusing a live correlation. Duplicate completion, stale epochs and all physical effects during a fault fail. Only after the host has settled and `HostScoreboard.reset(destructive=True)` succeeds may `MergeMonitor.reset()` clear its reservations/history and permit correlation ID reuse in the new epoch. Reusing a hardware slot within an epoch requires a new correlation ID. These APIs are qualified on fixtures; the existing JSONL host adapter retains its individual-write format. Reconciliation must bind actual merge observations to this API.

Physical completion, integrity outcome, transfer into reserved host storage, source BVALID exposure and source B handshake are different events. `LifetimeMonitor.transfer` can free a channel slot only after a reserved owner inherits the obligation. It does not expose a response. MR43 membership/history survives posted response handshake and slot reuse, independently for every rank. Live adapters call `poll(rank, ck)` at the actual MR43 command, then `result(rank, ck, failed, read_clear_ck=...)` at the returned result with a qualified read-clear completion. The result cannot precede tMRR; reopening cannot precede tMRR+tMRW from the sampled poll. Defaults are the admitted eight/twelve CK, and adapters must pass the active timing profile. New rank-local writes are blocked from poll through that guard; unrelated ranks remain eligible. Every timestamped API (`allocate`, `poll`, `result`, `advance`, and the `mr43` convenience helper) validates a nonnegative integer CK against one shared observed time across ranks. Earlier timestamps, including a poll before its first write or a result before already observed time, fail before settlement. Equal-edge events and rank-independent epochs remain legal. Every time-bearing event checks the outstanding 256-CK poll deadline, so omitting `advance` cannot hide a missed poll. `read_clear_ck` is a future reopening guard, not an observation that advances the shared clock. Destructive reset clears volatile rank guards but preserves absolute simulation time; it never permits clock rewind. Untimed APIs operate at the last observed CK, so live adapters must advance to their actual sample before invoking them. The old `mr43` helper advances through both poll and result and must not be used as a live pin observation. Interleaved rank events must be supplied chronologically using the separate APIs. Failure before exposure changes the unoffered response to SLVERR; after exposure the response remains stable and the applicable late error is recorded. Failed operations may transfer an error before pin completion so fatal settlement can finish; destructive recovery still requires host settlement and explicit reset confirmation.

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

The [streaming SV fixture](../../dv/system/rtl/stream.f) adds `stream_address`, `stream_w` and `stream_responses` APIs to the same canonical agent. VALID stays asserted between accepted payloads and response READY stays high. An eight-entry endpoint exercises multiple outstanding requests, complete write validation and memory checking, back-to-back W/R, source IDs and finite credits. Both ports run 37,500 four-beat transactions, check write data against its AW identity and final memory against the latest write to each address, and retain actual admission/completion edges for the 100-period path. The run also varies port-one period/phase and reset release. Its endpoint has no DRAM timing, maintenance or CDC engine.

`system.benchmark_trace` replays these observed edges through the exact benchmark window for both directions and all three reference service divisors. It requires the 400-MHz clock and multiple outstanding requests. The divisor chooses the comparison reference; the fixture does not implement CK/CK2/CK4 scheduling. Fixture payload rates therefore qualify stimulus/accounting only, and every report retains `controller_qualified: false`. The actual controller must additionally supply independent data, command/pin legality, maintenance, stall and CDC evidence. Replay a saved streaming log with:

```sh
PYTHONPATH=ips/lpddr5_mc/dv python3 -m system.benchmark_trace \
  /absolute/path/to/stream-read.log --direction read --service 2 \
  --report /absolute/path/to/new/measurement.json
```

Caps charge actual 32/64-byte issues in CK windows. Zero disables a direction even with borrowing enabled; borrowing otherwise requires no eligible opposite traffic and available combined byte budget. Fairness checks every eligible CK including the final tail, reports suspended intervals, and rejects an unproved issue gap `G`. The conditional bound is `(A+1)*T + D*G`, 4360 eligible CK for D64/A32/T8/G64. Check every class/port separately. Mixed/random, partial/narrow, ECC, thermal, rank-switch, maintenance and backpressure traffic needs correctness plus separate performance reports; the benchmark rate is not promised for those workloads.

## Evidence and integration handoff

`report.json` records source commit/dirty state, tracked IP file hashes, tools, commands, exit statuses, durations and limits. `tests.json` retains every executed environment test and the validated coverage map. `rtl.log`, `rtl.jsonl` and `trace.json` retain the original positive fixture; `stream-read.log`, `stream-write.log`, `stream-varied.log` and six `benchmark-*.json` reports retain the streaming checks; `sensitivity.log` retains the deliberate unstable-B rejection; `benchmark-sensitivity.log` retains rejection of a streaming trace with its end timestamp removed. The separate foundation report/log records ancestry, preserved-document hashes, generated consistency, reference matrices/schedules, C11/SV representation and relative Markdown links. Earlier failed runs remain separate evidence.

Commit the deliverable, run the full flow with `--require-clean`, then reproduce the exact commit in a separate full-history clone with independent Git metadata and isolated build output. Compare commit, clean status, all source hashes and substantive test/trace/coverage results. Commands/durations and absolute build paths naturally differ. A local clone using the same installed tools establishes source reproducibility in that environment; fresh-host preparation and remote CI are separate, unexecuted checks.

The integration owner must connect the actual host/channel top-level clocks/resets and canonical bus/configuration/completion/feedback ports, bind memory/PHY observations and the sampled management port, repair combined failures, and run the coverage map's pending cases. The early producer interface notes describe evolving source outside this isolated checkout; no final binding or producer behavior is inferred from those notes. Broad supported-parameter testing, actual 100-period throughput, synthesis/PPA, formal CDC/reset proof, vendor PHY/electrical and physical qualification have not been established by these fixtures.
