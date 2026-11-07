# Host and control verification

This page records owning RTL checks for the host/control contribution. Combined controller qualification remains governed by the [canonical contracts](../spec/contracts.md).

## Run the owning checks

After [preparing the pinned tools](contracts.md), run the [aggregate entry point](../../dv/host/check.py) from the repository root. Use a fresh report path for each run:

```sh
python3 ips/lpddr5_mc/dv/host/check.py \
  --build-subdir host-committed \
  --report ips/lpddr5_mc/dv/host/out/committed/summary.json
```

Add `--dry-run` to print the literal phase commands and deadlines without executing checks or writing reports. The aggregate runs its own failure-handling tests, the existing foundation `Makefile` check, CDC transports, APB/control-link checks, register/channel adapters, four representative host-core configurations, the full AXI bridge fixture matrix, assembled-host behavior and warning-fatal assembly elaboration. All phases run serially so compiler processes do not compete for the same sandbox memory.

The core behavior configurations are strict 64-bit/depth-16, 128-bit/depth-16 and 256-bit/depth-64, plus early-posted 128-bit/depth-16. Their testbench uses two ports and two channels to retain overlap and ordering checks. Root `lpddr5_mc_host` lint uses minimum `(ports=1, channels=1, ranks=1, data=64, ID=4, address=32, depth=16)`, the source defaults, and maximum `(2, 2, 2, 256, 16, 40, 64)`. These representative variants and boundary checks do not cover the Cartesian product of all supported parameters. Root elaboration does not establish combined host/channel behavior.

Compiler output, captured simulator sources and Python caches remain below `DHENARA_AGENT_TOOL_STORAGE_ROOT`. The aggregate report and a sibling `<report-stem>-phases/` directory retain phase reports and logs under `dv/host/out/`. Every phase has a finite deadline: runner and Python checks 30 seconds each, root elaboration 120 seconds each, AXI matrix 600 seconds, foundation 300 seconds, and transport/core/assembly phases 900 seconds each. A timeout kills that phase's compiler/simulator process group, records its actual signal exit and makes the aggregate exit 124. Other failures preserve their exit status; later phases are marked `not_run`. A child process exiting zero without its required valid report fails the aggregate.

The aggregate records the complete tracked and nonignored source-file inventory, hashes, Git revision and dirty state before and after execution. A source change or a different revision invalidates the aggregate result. Reports are immutable by convention: an existing report or phase directory is rejected, and reruns require a new report path. Dirty-tree runs remain development evidence. Delivery evidence requires a separate run on the exact clean committed contribution.

## Current development evidence

Retained APB and adapter development runs used working sources based on `86efe8167cfd51bf9dd71cf0db730434846ee985`; both explicitly record `source_dirty=true` and their per-file hashes. APB `evidence/host/apb-development-v9.json` and its log record `APB_PASS checks=2201 commands=11` and `APB_CDC_PASS checks=16 clocks=10ns/7ns phase=2ns words=732`. Adapter `dv/host/out/adapters/adapter-development-v5.json` records 6,383 register checks over 649 canonical mapping vectors and successful channel-link tests at depths 16/32/64. Those tests use 9-ns/14-ns independent clocks with a 1.75-ns phase offset, complete payload comparisons, stalls, capacity/wrap checks and both-endpoint reset/rearm cases.

Core reports `dv/host/out/core/iteration-13.json`, `iteration-14-data64.json`, `iteration-15-data256-q64.json` and `iteration-17-early128.json` each pass 24 behavior families and minimum/maximum core elaboration on their recorded development sources. They cover strict 128/64/256-bit configurations and early-posted 128-bit mode. CDC `dv/host/out/cdc/cdc-iteration-11.json` passes all three transport variants with actual reset-ready checks; AXI `dv/host/out/axi/iteration-10.json` passes its default-width transport case after that correction. The aggregate command above is prepared but its final committed-source matrix has not yet been run. Development reports do not prove that later source changes, combined channel wiring, physical/integrity completion, PHY timing or controller throughput pass.

## Common-domain core and simulator qualification

The [core runner](../../dv/host/core_run.py) captures its package, contracts, core RTL, testbench and runner before compilation and rejects a source change during the run. It retains the observed `CORE_RESULT` family count and requires every family expected by its current fixture. It also runs minimum/maximum core elaboration. Its independent byte-memory responder verifies host behavior within one common clock domain; asynchronous AXI admission and real channel pin/ECC behavior need their separate checks.

The qualified Verilator 5.006 core simulation requires `--public-flat-rw`, which `core_run.py` supplies. A retained minimal reproducer showed a task-written packed slot changing from 0 to 1 while its derived combinational slot remained 0 without that option; the same source with the option produced matching 0/1/2 values and `SENSITIVITY PASS`. This option keeps task-driven packed writes visible to the simulator's combinational scheduling. Diagnostic logs are retained as `dv/host/out/core/sensitivity-2-run.log` and `sensitivity-public-run.log`. Keep the remedy when reproducing this qualified tool version; qualify another simulator version independently.

## APB and channel adapters

The [APB runner](../../dv/host/apb_run.py) checks generated metadata consistency, schema-derived register access, strobes, locks, command sequencing, IRQ/W1C behavior, immutable prepare/commit snapshots, stale/partial/timeout acknowledgement handling and status/first-error snapshots. It exercises the actual configuration/command/acknowledgement/status mailboxes on independent clocks, checks minimum/maximum APB parameters and compiles the C11 programming example. Channel-side numeric-profile validation and live drain witnesses remain integration responsibilities.

The [adapter runner](../../dv/host/adapter_run.py) compares committed register-image decoding and mapping with canonical Python oracle vectors. Cases cover all 16 QoS classes, port limits, modes/epochs, 40-bit boundaries, region holes and unsorted permutations, ECC capacity and rank boundaries, one/two channels and eight regions. Real channel-link queues compare complete request, completion and response-feedback payloads at actual handshakes. Reset tests account for aborted transport records and require explicit two-sided rearm with fresh epochs; acceptance-ledger settlement remains the host owner's responsibility.

## CDC transports

The [explicit source list](../../rtl/cdc/files.f) contains the synchronizer, reset fence, asynchronous FIFO and bundled mailbox. Run their independent-clock RTL tests from the repository root after the [pinned tool preparation](contracts.md):

```sh
python3 ips/lpddr5_mc/dv/host/cdc_check.py \
  --report /absolute/path/to/evidence/cdc.json
```

Disposable compilation output is placed below `DHENARA_AGENT_TOOL_STORAGE_ROOT/host-cdc`. The report records source hashes, Git revision/dirty state, simulator version, literal commands, durations, seed 2095 and logs. A dirty-tree run is development evidence. The final contribution must rerun on its committed source and preserve that report separately.

The testbench compares every accepted transport payload against independent queues and checks VALID/data stability under backpressure. It exercises depth 2/16/64, widths 1/80/1024, independently phased 6/10-ns and 10/4-ns clocks, full capacity, pointer wrap, enable gating, random receiver stalls, mailbox acknowledgement delay, destination reset while its clock is stopped, one-sided rearm rejection and source reset. It explicitly accounts aborted entries separately from consumed entries and checks that old payload/toggle state cannot reappear after rearm.

Reset at either endpoint asynchronously aborts both transport ends and latches faults. Startup is also faulted. The two sides stay blocked until each owner supplies a domain-local rearm pulse after every actual local transport reset-ready witness, host settlement or coordinated host reset, and fresh boot/configuration identities. FIFO and mailbox readiness outputs are the owning fence reset releases; they remain low when a reset endpoint clock is stopped. The regression rejects a pulse before those releases, checks that one-sided rearm cannot reopen transport, and requires a new valid pulse after readiness. The AXI endpoint retains an initialization request until all nine source transport ends can accept rearm; the bridge waits for all core transport ends, and initialization completes only after the matching acknowledgement and cleared fault observations. A continuously high rearm input would bypass this recovery permission and is unsupported. FIFO storage alone cannot reconstruct accepted AXI beat counts after transport reset; the host must retain that ledger outside the aborted transport or coordinate the host reset explicitly. Configuration mailboxes must be discarded and rebuilt from the new epoch.

The FIFOs synchronize only Gray publication pointers. The mailbox holds one immutable source payload until its destination consumes it and the acknowledgement returns. Read/write enable inputs are local clock-domain controls. The [physical integration requirements](../../constraints/host/README.md) describe pointer skew, bundled-data timing and reset constraints. Digital simulation does not prove metastability mean time between failures or replace combined-netlist CDC/reset analysis.

## Asynchronous AXI endpoints

Run the bridge's real independent-clock simulations from the repository root:

```sh
python3 ips/lpddr5_mc/dv/host/axi_run.py \
  --report /absolute/path/to/evidence/axi.json
```

The runner uses the [bridge source list](../../rtl/host/axi-files.f), assertion-enabled warning-fatal Verilator 5.006 and its `--public-flat-rw` scheduling workaround. The simulated AXI clocks both run at 400 MHz with a 0.375 ns phase offset; the host transport clock runs at 800 MHz. Four configurations exercise data widths 64/128/256, depths 16/32/64, ID widths 4/8/16 and address widths 32/36/40. Repeated narrow IDs exercise source response order. `--default-only` runs the 128-bit/depth-16 development case. Reports retain exact source hashes, dirty state, literal commands, timings and every simulator log. A successful compile alone does not produce a passing report: every selected simulation must emit its completion witness.

The fixture independently records actual source AW/AR handshakes and checks their common-domain import order and reserved slot/generation. It samples actual source R/B handshakes, verifies IDs, read payloads, RLAST and complete response counts, and checks held response stability. The core-side consumer models finite per-slot credits and explicit retirement after real source feedback. Tests cover delayed W, concurrent ports and directions, stale-boot rejection, core reset, peer reset, pending W consumption, remaining read beats, preserved exposed responses, and a fresh successful transaction after recovery.

Quiesce is a handshake across admission ownership. `admission_busy_o` stays set until the coordinator's granted address has imported and each source has acknowledged this exact disable request token with both synchronized enable stages zero and no remaining candidate. A falling enable fences admission immediately, before the next core edge publishes the token. The stopped-source test applies a one-core-cycle enable pulse and requires a fresh acknowledgement after the source clocks restart; an old idle level cannot satisfy it. Connect the common engine's admission enable to `run_enable || admission_busy_o`. The source ledger's ordinary settlement and abort settlement are separate registered single-bit crossings. Abort settlement registers `fault && source_settled` before synchronization, preventing a stale healthy-empty observation or a combinational decode glitch from authorizing recovery. A fatal transport reset must not reset this observation path: the source consumes every outstanding W and produces the complete required R/B sequence under its surviving AXI reset, while the common engine can discard its aborted copies. A consumed OKAY B remains covered by its exposed ledger until retirement, including a fault while its real handshake feedback is still crossing.

The saturated transport measurement uses independent continuously offered AW and W streams on both ports, consecutive W beats, multiple outstanding transactions and always-ready B/R. Reads use independent continuously offered AR streams. Each steady-state window counts completed payload bytes after warm-up and before drain; setup and trailing `wait_drained` cycles are excluded. Logs report source VALID/READY duty, coordinator idle/grant/accept cycles, waiting-for-source versus import backpressure, source prevalidation/waiting cycles, FIFO-full stalls and peak local ledger occupancy. Earlier serialized-driver measurements remain historical development evidence. This fixture measures the host transport against an independent response consumer; it does not establish memory-controller bandwidth, DFI behavior or the final 12.8 GB/s requirement. The assembled host and combined controller need their own behavior and throughput evidence.

## Assembled host behavior

The [assembly runner](../../dv/host/assembly_run.py) connects the real AXI bridge and common host core to an independent byte-memory responder. It uses host/AXI periods of 1.25/2.5/3.5 ns with a 0.375-ns phase offset and exercises a 128-beat burst whose 32 granules exceed queue depth 16. Other checks cover an accepted AW before delayed W, overlapping reads on the other port, a clean queued OKAY aborted before its stopped source can expose it, stable exposed B and late error, peer reset and core reset with remaining W obligations, and successful traffic after fresh-boot recovery. An additional consumed-OKAY fault case stops the host clock so exposure/handshake feedback cannot reach the core before the fault; the surviving source ledger must report the late error.

Development `dv/host/out/assembly/iteration-10.json` passes all seven families on its recorded source with no source change during the run. Its log records 71 channel requests, seven write responses and 140 read beats. The fresh reset-ready correction is included; this remains dirty-tree development evidence until clean committed reproduction. These completion witnesses establish host byte behavior, not channel/DFI/ECC correctness or memory throughput. Reports retain source hashes, exact commands, clock periods, logs and source-change detection. Split C++ generation and explicit make optimization settings bound simulator compilation cost without changing RTL assertions.

The expanded assembly also checks prompt recovery after a fatal flush without unnecessarily resetting the surviving source ledgers. A retained failure exposed a lost rearm pulse: the bridge had released its own reset while the payload FIFO was still releasing its additional local reset. Recovery now needs actual per-domain reset-ready signals from the transport owner. The updated assembly run passes; the separate clean committed aggregate remains required. A later diagnostic failure came from a testbench task reading the old combinational settled value in the same delta as it changed enable. The fixture now samples a later clock edge and retains its eight-CK stable-settlement assertion; that observation did not justify weakening the RTL settlement gate.
