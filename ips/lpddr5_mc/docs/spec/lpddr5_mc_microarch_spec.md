# LPDDR5 Memory Controller: Microarchitecture Specification

**Status:** Draft v0.1 (first cut)
**Scope:** Controller RTL microarchitecture. AXI4 ingress, DFI 5.0 egress to a separately integrated PHY.
**Target:** LPDDR5-6400, x16 channels, parameterizable for 1 or 2 AXI ports and 1 or 2 DRAM channels.

> Items marked **[VERIFY]** must be checked against the JEDEC LPDDR5 (JESD209-5) and DFI 5.0 specifications before freeze. Timing values are parameters in CSRs, not hard-coded.

---

## 1. Goals and Non-Goals

### 1.1 Goals
- AXI4 slave ports on ingress; DFI 5.0 master towards an external PHY.
- Open-page-state-aware command scheduling with per-bank/bank-group/rank state tracking.
- Software-configurable read/write bandwidth control in the scheduler.
- QoS/urgency handling with starvation protection.
- Refresh, power-down/self-refresh, DVFS (frequency set point) support.
- Inline ECC and link ECC support.
- Parameterizable: `N_AXI_PORTS` (1..2), `N_CH` (1..2), `N_RANK`, queue depths, data widths.

### 1.2 Non-Goals (v0.1)
- PHY design, PHY training algorithms (handled by PHY/firmware; the controller only provides hooks).
- Multi-die stacking beyond ranks selected by chip select.
- AXI5 features (atomics, poison, etc.).

---

## 2. Key Parameters

| Parameter | Default | Range | Description |
|---|---|---|---|
| `N_AXI_PORTS` | 2 | 1..2 | Number of AXI4 slave ports |
| `N_CH` | 2 | 1..2 | Number of independent DRAM channels (each x16, own DFI) |
| `N_RANK` | 1 | 1..2 | Ranks (chip selects) per channel |
| `N_BG` | 4 | 2..4 | Bank groups (BG mode) |
| `N_BANK_PER_BG` | 4 | 4 | Banks per bank group |
| `AXI_DATA_W` | 128 | 64..256 | AXI data width |
| `AXI_ID_W` | 8 | 4..16 | AXI ID width |
| `AXI_ADDR_W` | 36 | 32..40 | AXI address width |
| `RDQ_DEPTH` | 32 | 16..64 | Read queue entries per channel |
| `WRQ_DEPTH` | 32 | 16..64 | Write queue entries per channel |
| `DFI_RATIO` | 2 | 2 or 4 | DFI frequency ratio (1:2 default) **[VERIFY]** |
| `BURST_BYTES` | 32 | 32 or 64 | BL16 (32B) or BL32 (64B) per x16 access **[VERIFY]** |
| `ECC_MODE` | off | off/inline/link/both | ECC configuration (CSR-selectable at init) |

### 2.1 Clocking Assumptions (LPDDR5-6400)
- Data rate 6400 Mbps/pin, WCK = 3200 MHz, CK = 800 MHz (WCK:CK = 4:1).
- DFI ratio 1:2 gives a controller core clock of about 400 MHz. A 1:4 option gives about 200 MHz with four command phases per core cycle.
- Peak raw bandwidth per x16 channel: 12.8 GB/s.

---

## 3. Top-Level Block Diagram

```text
                 AXI4 Port 0                          AXI4 Port 1
                     |                                    |
        +------------v------------+          +------------v------------+
        |    AXI Front End 0      |          |    AXI Front End 1      |
        | AR/AW/W/R/B, ID track,  |          | (same as port 0)        |
        | QoS capture, splitter   |          |                         |
        +------------+------------+          +------------+------------+
                     |  (decoded requests, tagged with port/QoS)
        +------------v--------------------------------------v------------+
        |           Address Decode + Port/Channel Crossbar                |
        |     (addr map -> ch/rank/bg/bank/row/col, channel interleave)   |
        +------------+------------------------------------+---------------+
                     | Channel 0                          | Channel 1
   +-----------------v-----------------+  +---------------v-----------------+
   |        CHANNEL CONTROLLER 0       |  |        CHANNEL CONTROLLER 1     |
   |                                   |  |        (identical instance)     |
   | +-------------+  +-------------+  |  |                                 |
   | | Read Queue  |  | Write Queue |  |  |                                 |
   | | (CAM, age,  |  | (CAM, merge,|  |  |                                 |
   | |  QoS)       |  |  RMW flags) |  |  |                                 |
   | +------+------+  +------+------+  |  |                                 |
   |        |    +-------------+        |  |                                 |
   |        |    | ECC / RMW   |        |  |                                 |
   |        |    | Engine      |        |  |                                 |
   |        |    +------+------+        |  |                                 |
   | +------v-----------v-----------+   |  |                                 |
   | |          SCHEDULER            |   |  |                                 |
   | | candidate select, R/W mode,   |<--+--+-- CSR (BW ctrl, QoS, policy)    |
   | | BW regulators, starvation,    |   |  |                                 |
   | | page policy                   |   |  |                                 |
   | +------+------------------------+   |  |                                 |
   |        ^   |                        |  |                                 |
   | +------+---v-------+  +----------+  |  |                                 |
   | | Page State       |  |Maintenance| |  |                                 |
   | | Tracker + Timing |  |Refresh/ZQ/| |  |                                 |
   | | Counters         |  |Temp/RFM/PM| |  |                                 |
   | +------+-----------+  +-----+----+  |  |                                 |
   |        |                    |       |  |                                 |
   | +------v--------------------v----+  |  |                                 |
   | |  Command Arbiter + Generator    |  |  |                                 |
   | |  (legality check, WCK sync,     |<-+--+-- DVFS/FSP FSM, MRW/MRR inject |
   | |   CA bus packing)               |  |  |                                 |
   | +------+--------------------------+  |  |                                 |
   |        |        +-----------------+  |  |                                 |
   |        |        | Write Data Path |  |  |                                 |
   |        |        | Read Data Path  |  |  |                                 |
   |        |        +--------+--------+  |  |                                 |
   | +------v-----------------v-------+   |  |                                 |
   | |          DFI Master 0          |   |  |                                 |
   | +------+-------------------------+   |  |                                 |
   +--------|-----------------------------+  +--------|------------------------+
            | DFI 5.0                                 | DFI 5.0
        [ PHY 0 ]                                 [ PHY 1 ]

   Shared: APB CSR block | Debug/perf counters | DVFS & low-power coordinator | Clock/reset
```

---

## 4. Interfaces

### 4.1 AXI4 Slave (per port)
- Full AXI4 (not Lite): AW/W/B/AR/R channels, INCR/WRAP/FIXED bursts, up to 256 beats (INCR).
- Supported: `AxLEN`, `AxSIZE`, `AxBURST`, `AxID`, `AxQOS`, `AxCACHE`/`AxPROT` (ignored, passed to debug), `WSTRB` (drives data mask / RMW).
- Not supported in v0.1: exclusive access (`AxLOCK` returns OKAY, treated as normal), user signals, atomics.
- Ordering: same-ID transactions return in order. Different IDs may return out of order. Per-ID response reorder buffer in the front end.
- Responses: OKAY, SLVERR (uncorrectable ECC error, address out of range), DECERR (unmapped region).
- Hazards: a read that hits an older pending write to the same address gets data forwarded from the write queue. A write behind an older read to the same address waits until the read has been issued. Both are handled in the channel queues (section 5.4).

### 4.2 DFI 5.0 Master (per channel) **[VERIFY signal names and widths]**
- **Control:** `dfi_address`, `dfi_cs`, `dfi_cke`, `dfi_reset_n`, `dfi_dram_clk_disable`.
- **Write data:** `dfi_wrdata`, `dfi_wrdata_cs`, `dfi_wrdata_en`, `dfi_wrdata_mask` (DMI/mask).
- **Read data:** `dfi_rddata_en`, `dfi_rddata`, `dfi_rddata_valid`, `dfi_rddata_cs`.
- **WCK control:** `dfi_wck_cs`, `dfi_wck_en`, `dfi_wck_toggle` (LPDDR5 specific).
- **Update:** `dfi_ctrlupd_req/ack`, `dfi_phyupd_req/ack/type`.
- **Status:** `dfi_init_start`, `dfi_init_complete`, `dfi_freq_ratio`, `dfi_frequency`, `dfi_freq_fsp`.
- **Low power:** `dfi_lp_ctrl_req/wakeup/ack`, `dfi_lp_data_req/wakeup/ack`.
- **Error:** `dfi_error`, `dfi_error_info`.
- The DFI interface is phase-aware: with `DFI_RATIO = 2`, each core clock carries two command phases (p0, p1). The command generator packs commands into phases and the timing checker works at CK granularity.

### 4.3 CSR (APB)
- APB3/APB4 slave, 32-bit. Register map in section 5.14.

---

## 5. Block-Level Microarchitecture

### 5.1 AXI Front End
Per port:
- **Request splitter:** breaks an AXI burst into DRAM-granule requests (32B or 64B depending on `BURST_BYTES`), handles unaligned starts, WRAP bursts (critical word first where supported), and narrow transfers (sets partial-write flags).
- **Request descriptor** (to crossbar): `{port, axi_id, qos, rw, addr, size, byte_enable_info, last_of_burst, tag}`.
- **Write path:** AW and W are decoupled. A write request is only released to the channel once its data is available in the write data buffer (or buffer space is reserved for it). This avoids deadlock where the write queue is full of entries waiting for data.
- **Read return path:** a response reorder buffer (ROB) per port, indexed by a tag assigned at request time. Data from channel read data paths is written into the ROB. R channel drains in AXI ordering rules (same-ID in order).
- **Outstanding limits:** configurable per port (`MAX_OUTSTANDING_RD/WR`).
- **QoS:** `AxQOS[3:0]` mapped to an internal 2-bit priority class through a CSR lookup table (`QOS_MAP`). An optional port-level default priority is also available.
- **Timeout:** per-request age counter feeds the scheduler's starvation logic.

### 5.2 Address Decoder
- Programmable address map: bit-slices (or XOR hashes) select channel, rank, bank group, bank, row, column.
- Default mapping optimized for bank-group parallelism: low column bits, then channel bits (interleave granule configurable, 256B to 4KB), then bank group, bank, row.
- Optional bank/BG XOR hash with row bits to reduce row-buffer conflicts for strided access.
- Region table (N entries): base, size, attributes (ECC enable, channel interleave on/off, secure flag), used also for DECERR generation.
- Output: `{ch, rank, bg, bank, row, col}` attached to each request.

### 5.3 Port/Channel Crossbar
- `N_AXI_PORTS x N_CH` crossbar for request channels (small, since the request descriptor is narrow).
- Per-channel input arbitration: QoS-aware round robin across ports, with per-port credits to prevent one port from filling the channel queues.
- Write data and read data buffers sit on the channel side of the crossbar; data crossbar is port-to-buffer indexed by tag.
- When `N_CH = 1`, the channel select is a constant and the crossbar collapses. When `N_AXI_PORTS = 1`, arbitration collapses.

### 5.4 Transaction Queues (per channel)
**Read queue (`RDQ_DEPTH`)**: CAM-style entries:

| Field | Description |
|---|---|
| valid, state | idle / waiting / row-ready / issued |
| rank, bg, bank, row, col | decoded address |
| qos, port, tag | priority class and return path info |
| age | saturating counter, incremented each N cycles |
| hit_flag | updated by page tracker (row currently open for this bank matches) |
| hazard_dep | index of older write entry this read depends on (if any) |
| ecc_flags | ECC region addressing info |

**Write queue (`WRQ_DEPTH`)**: same fields plus:
- `data_ptr`: index into write data buffer; `be_mask`: byte enables; `partial`: needs RMW if inline ECC is on or if memory masks cannot express the pattern.
- **Write merging:** a new write to the same granule as an existing entry merges data/masks (CAM compare on address).
- **Write-to-read forwarding:** a read that matches a write entry address fully covered by byte enables is served directly from the write data buffer. A partial match makes the read wait (`hazard_dep`).
- **Write posting:** AXI B response is returned when the write is accepted into the write queue (configurable: at accept vs. at DRAM issue, for debug/strict ordering).

### 5.5 Page State Tracker and Timing Counters
Per `{rank, bank}` (16 banks per rank in BG mode, with `N_BG x N_BANK_PER_BG`):

| State | Meaning |
|---|---|
| IDLE | Precharged, ready for ACT |
| ACTIVATING | ACT-1/ACT-2 issued, waiting tRCD |
| ACTIVE | Row open, `open_row` valid |
| PRECHARGING | PRE issued, waiting tRP |
| REFRESHING | Per-bank or all-bank refresh in progress |

Stored per bank: `state`, `open_row`, `last_access_age` (for idle-close policy), `hit_count` (consecutive row hits, for fairness cap), and timers.

**Timing counters (per bank, per bank group, per rank, per channel):** all values come from CSRs. **[VERIFY exact LPDDR5 parameter list and per-mode values]**

| Scope | Constraints tracked |
|---|---|
| Per bank | tRCD, tRAS, tRP, tRC, tRTP, tWR (write recovery before PRE), tRFCpb |
| Per bank group | tCCD_L, tRRD_L, tWTR_L |
| Per rank | tCCD_S, tRRD_S, tFAW (four-activate window), tWTR_S, tRFCab |
| Per channel | Read-to-write and write-to-read bus turnaround (tRTW, WCK/DQS preamble and postamble), command bus occupancy, WCK sync window |

Implementation: down-counters loaded on the triggering command, with a ready bit per constraint. The tFAW window uses a 4-deep timestamp FIFO per rank. Counters run in CK units and are phase-aware for the 1:2 / 1:4 DFI ratios.

**Outputs to scheduler:** for every queue entry, the tracker provides in one cycle: `row_hit`, `row_conflict`, `row_closed`, and `can_issue_{ACT,RD,WR,PRE}_now` (all timing satisfied). This is implemented as a per-entry lookup into the bank table indexed by `{rank, bank}` (parallel lookup across all queue entries).

**Banking mode:** BG mode default; 8B and 16B modes selectable by CSR at init, changing the set of active timing counters **[VERIFY]**.

### 5.6 Scheduler (per channel)
The scheduler picks one DRAM command per command slot from read/write candidates. It is a pipelined, multi-stage design.

```text
 RDQ/WRQ entries ---> [S1 Candidate Filter] ---> [S2 Class Select] ---> [S3 Final Pick] ---> Cmd Arbiter
                          |                         |                       |
                  (can_issue, hazard,       (R/W mode, BW regulators,  (priority: urgent >
                   bank not in refresh)      QoS, starvation)           row-hit > oldest)
```

**Stage 1: Candidate filter**
- Removes entries blocked by hazards (`hazard_dep`), banks under refresh/maintenance, or entries whose next command is not yet timing-legal.
- For each remaining entry, determines its **next required command** from bank state:
  - Row hit -> RD/WR (the CAS command, preceded by a WCK sync command if WCK is off) 
  - Bank IDLE -> ACT
  - Row conflict -> PRE (then ACT)

**Stage 2: Read/write class selection**
Controls which direction the scheduler is serving, and enforces configurable read/write bandwidth.

*R/W mode state machine (READ_MODE / WRITE_MODE):*
- Switch to WRITE_MODE when write queue occupancy >= `WR_HI_WM`, or read queue is empty and writes are pending, or a write has aged beyond `WR_AGE_MAX`.
- Switch back to READ_MODE when write occupancy <= `WR_LO_WM`, or the write burst has issued `WR_BATCH_MAX` writes, or a read exceeds `RD_URGENT_AGE`.
- Minimum residency: `RD_BATCH_MIN` / `WR_BATCH_MIN` transfers before a turnaround is allowed, amortizing the read/write bus turnaround penalty.

*Bandwidth regulators (configurable):*
- **Direction weights / ratio:** a deficit-weighted counter pair with `RD_WEIGHT` and `WR_WEIGHT`. Each granule transferred charges its direction's counter. When both queues are non-empty, the scheduler biases the mode switch to hold the programmed read:write share.
- **Bandwidth cap per direction:** token buckets `RD_BW_CAP` and `WR_BW_CAP` (tokens per window of `BW_WINDOW` cycles, bucket depth `BW_BURST`). When a bucket is empty, that direction is deprioritized (work-conserving mode: still served if the other direction is idle) or blocked (strict mode), selectable by CSR.
- **Per-QoS-class / per-port weights:** optional weighted share per priority class and per AXI port, using the same token-bucket structure (`N_CLASS` buckets).
- All regulators can be bypassed (`BW_CTRL_EN = 0`) for maximum-throughput mode.

**Stage 3: Final pick (within the chosen direction)**
Priority order:
1. **Urgent / starved:** any entry whose age exceeds the escalation threshold, or QoS class = urgent (age-based escalation promotes a class one level per `AGE_STEP` cycles).
2. **Maintenance deadlines:** refresh that has exhausted its postponement credit preempts normal traffic (handled via the command arbiter, see 5.7).
3. **Row hit:** oldest row-hit entry (FR-FCFS), subject to the **hit cap** `MAX_CONSEC_HITS` per bank, which stops a streaming row from starving others.
4. **Bank-parallelism preference:** among ready ACT candidates, prefer a different bank group than the last command (to use tCCD_S/tRRD_S), and a bank with the most pending requests.
5. **Oldest first.**

**Page management policy (per-bank, CSR-selectable globally or per region):**
- **Open page:** leave row open until a conflicting request arrives.
- **Closed page:** auto-precharge on the last pending access to the row (look-ahead into the queue: if no other queued entry targets the same `{bank, row}`, use the auto-precharge CAS variant).
- **Adaptive:** open page with an idle timer `PAGE_IDLE_TIMEOUT`; a lightweight per-bank 2-bit saturating predictor (hit vs. conflict history) decides whether to close early. PRE is issued speculatively only on idle command slots.

**Look-ahead ACT/PRE:** the scheduler issues ACT or PRE for a younger request to a different bank while the data bus is busy with an older request's burst (bank-level parallelism), as long as it does not delay the oldest ready command.

### 5.7 Command Arbiter and Generator
- **Arbitration among sources** (fixed priority, with timers to prevent starvation):
  1. MRW/MRR / training command injection (CSR driven, during init or DVFS)
  2. Power/DVFS FSM commands (SRE/SRX, PDE/PDX, FSP switch)
  3. Urgent refresh
  4. Scheduler command
  5. Opportunistic refresh/ZQ (only when no scheduler command)
- **Legality check (final gate):** re-verifies timing counters and command bus occupancy so that a scheduler bug cannot produce a timing violation. Violation flags assert a debug interrupt (and optionally block issue).
- **LPDDR5 specifics:**
  - Two-phase activate (ACT-1 / ACT-2) tracked as one logical ACT with a fixed gap. The tracker marks the bank ACTIVATING after ACT-1.
  - **WCK sync management:** before an RD/WR, a CAS command (WCK2CK sync) must have been issued and WCK must be running. A per-rank WCK state machine (OFF / SYNCING / ON) drives `dfi_wck_en`/`dfi_wck_toggle`, issues sync CAS commands when needed, and turns WCK off after an idle timeout (`WCK_IDLE_TIMEOUT`) to save power (or leaves it always-on by CSR option) **[VERIFY CAS sync sequences and tWCK timing]**.
  - Read/write burst length selection (BL16 / BL32) and per-access auto-precharge.
  - Write data mask / DMI usage per mode.
- **CA bus packing:** converts a command into a multi-cycle CA bus sequence on `dfi_address` / `dfi_cs` across DFI phases **[VERIFY encoding]**.

### 5.8 Data Paths
**Write data path**
- Write data buffer (per channel): `WRQ_DEPTH x BURST_BYTES` plus byte-enable mask storage, dual-ported, written by the front end, read by the DFI write path.
- Timing: `dfi_wrdata_en` and `dfi_wrdata` are launched a fixed number of cycles after the WR command (`tphy_wrlat` + `tphy_wrdata`, from CSR, **[VERIFY]**). A write-data pipeline (delay line) aligned to the command pipeline handles this.

**Read data path**
- `dfi_rddata_en` is asserted at the programmed latency after RD (`trddata_en`). Returned data with `dfi_rddata_valid` is matched to the outstanding read via an in-order per-channel read-tracking FIFO (DRAM returns data in command order), then written to the ROB entry by tag.
- If ECC is enabled, the data passes through the ECC decoder before the ROB write.

### 5.9 ECC
**Inline ECC (controller-side)**
- ECC bits stored in a reserved DRAM region (e.g., top 1/8 of capacity at default SECDED 64+8 ratio; ratio parameterizable). The address decoder translates data address to `{data location, ECC location}` for each access.
- **Read:** issue data read and an ECC read (ECC fetch may be shared across neighboring data granules through a small **ECC cache** holding recent ECC blocks, with hit/miss counters).
- **Write:** full-granule writes compute ECC and update the ECC block (write to ECC cache, written back on eviction). Partial writes trigger **read-modify-write**: read old data+ECC, merge, recompute, write back.
- The ECC/RMW engine sits between queues and scheduler: it injects the extra read/write requests into the queues with dependency links so that ordering is preserved.
- **Error handling:** single-bit errors are corrected and optionally scrubbed (write-back of corrected data); double-bit errors return SLVERR on AXI and log in status registers with address capture. An optional background **patrol scrub** engine walks memory at a programmable rate using idle slots.

**Link ECC (DRAM-side)**
- Uses the LPDDR5 link ECC feature (enabled via MRW) so the DRAM and controller protect the DQ link **[VERIFY mode-register settings and how the PHY exposes the check bits]**. The controller enables the mode at init and records link error status from the interface (DFI error signals / MR read of error counters).

### 5.10 Maintenance
- **Refresh:**
  - Modes: all-bank (REFab) and per-bank (REFpb) selectable by CSR.
  - A refresh timer generates a request every `tREFI / (scale)`; temperature-based refresh rate scaling comes from MR4 polling (below).
  - **Postpone/pull-in credits:** counter of owed refreshes (bounded per JEDEC **[VERIFY max postpone limit]**). Refresh is issued opportunistically when the owed count is low and the channel is idle, and forced (highest priority) when the limit is reached.
  - **Per-bank refresh scheduling:** chooses banks with no pending requests/open rows first, issuing PRE to the target bank if needed. This hides refresh behind traffic to other banks.
- **ZQ calibration:** periodic ZQ start/latch commands (interval `ZQ_INTERVAL`, CSR) issued when the bus is quiet, as required by the DRAM **[VERIFY LPDDR5 ZQ sequence]**.
- **Temperature monitor:** periodic MR4 read (interval `TEMP_POLL_INTERVAL`) to adjust refresh rate; thermal alert raises an interrupt and can throttle bandwidth (reuses the BW regulator).
- **RFM (refresh management / row-hammer mitigation):** optional per-bank activation counters (rolling accumulated ACT count, RAAIMT threshold) that trigger an RFM command when the threshold is reached **[VERIFY applicability and parameters for target DRAM]**.

### 5.11 Power Management
- **Idle power-down (PDE/PDX):** entered after `PD_IDLE_TIMEOUT` cycles with empty queues; exit on any new request (adds tXP latency to the first access).
- **Self-refresh (SRE/SRX):** entered by an explicit CSR request or after `SR_IDLE_TIMEOUT`; requires closing all pages first. Hardware handshakes with the DFI low-power interface (`dfi_lp_ctrl_req/ack`) so the PHY can gate clocks.
- **Deep sleep / DPD** (optional, CSR triggered) **[VERIFY mode support]**.
- **Clock gating:** per-channel queue/scheduler clock gating when idle.
- AXI side: a low-power handshake (Q-channel or CSR based) lets the SoC power controller request quiesce.

### 5.12 DVFS / Frequency Set-Point Control
- Supports multiple frequency set points (FSP) with per-FSP timing parameter sets in CSR shadow banks (`TIMING_SET[0..N]`).
- **Sequence** (FSM per channel, coordinated across channels by the shared DVFS coordinator):
  1. Block new AXI requests (backpressure at front end, or let in-flight drain).
  2. Drain queues, precharge all banks, ensure no pending refresh.
  3. Enter self-refresh (if required by the switch type) or issue MRW for FSP-OP switch **[VERIFY LPDDR5 FSP/DVFSC/DVFSQ flows]**.
  4. Drive DFI frequency change: set `dfi_frequency`/`dfi_freq_fsp`, assert `dfi_init_start`, wait for `dfi_init_complete`.
  5. Swap active timing set, resume refresh/ZQ timers, release the front end.
- Retraining after frequency change is performed by the PHY / firmware; the controller exposes a hold-off (`TRAIN_REQ` / `TRAIN_DONE` CSR bits) before releasing traffic.

### 5.13 DFI Master
- Instantiated per channel. Converts the controller's phase-agnostic command/data streams into DFI phase signals according to `DFI_RATIO`.
- **Update handling:** `dfi_ctrlupd_req` is issued periodically (or on temperature change). The controller must drain/hold commands per DFI rules until `dfi_ctrlupd_ack`. `dfi_phyupd_req` from the PHY is honored with a controller pause (programmable max wait) before `dfi_phyupd_ack`.
- **Initialization:** `dfi_init_start` / `dfi_init_complete` handshake, then the DRAM init sequence (reset, CKE, MRW sequence for MR1-MR22 equivalents) driven by an init sequencer FSM using the command injection path **[VERIFY LPDDR5 init sequence]**.
- **Training hooks:** CSR-driven command injection and DFI PHY-master/controller-master handover mode to allow PHY training sequences (CBT, WCK2CK leveling, read/write training) while the scheduler is held off.
- Error: `dfi_error` captured into status registers with interrupt.

### 5.14 CSR Block (APB)
Register groups (all sizes illustrative):

| Group | Contents |
|---|---|
| `GLOBAL` | Enable, soft reset, banking mode, ECC mode, DFI ratio, interrupt enable/status |
| `ADDRMAP` | Bit-slice selects, XOR hash controls, channel interleave granule, region table |
| `TIMING[n]` | All DRAM timing parameters per frequency set point (tRCD, tRP, tRAS, tRC, tCCD_L/S, tRRD_L/S, tFAW, tWTR_L/S, tRTW, tWR, tRFCab/pb, tREFI, tXP, tCKE, tZQ*, WCK parameters, DFI latencies) |
| `SCHED` | Watermarks (`WR_HI_WM`, `WR_LO_WM`), batch sizes, age thresholds, `MAX_CONSEC_HITS`, page policy and timeouts, QoS map |
| `BWCTRL` | Read/write weights, token-bucket caps, window, burst, strict/work-conserving mode, per-class and per-port weights |
| `MAINT` | Refresh mode, postpone limit, ZQ interval, temp poll interval, RFM thresholds, scrub rate |
| `PM` | Power-down/self-refresh timeouts, WCK idle timeout, manual entry/exit requests |
| `DVFS` | Target FSP, trigger, status, train handshake |
| `INJECT` | Command injection (MRW/MRR/raw command), read-back data |
| `DEBUG` | Performance counters, queue occupancy snapshots, timing violation flags, trace control |

### 5.15 Debug and Performance
- Counters per channel: reads/writes accepted, row hits/misses/conflicts, activate count, refresh count, bus utilization, R/W turnarounds, average and max queue latency per QoS class, ECC corrected/uncorrected counts, starvation escalations.
- Optional command trace buffer (last N commands with timestamps) readable via CSR.
- Timing-violation checker flags (from the legality gate) are sticky and generate interrupts.

---

## 6. Representative Flows

### 6.1 Read, Row Hit
1. AXI AR accepted -> split -> decoded -> routed to channel queue.
2. Page tracker reports `row_hit`, `can_issue_RD`.
3. Scheduler picks entry (READ_MODE, row-hit priority) -> command generator checks WCK state (issues sync CAS first if WCK off) -> RD on DFI.
4. After `trddata_en` latency, `dfi_rddata_en` pulses; data returns via `dfi_rddata_valid` -> ECC check -> ROB -> AXI R channel.

### 6.2 Read, Row Conflict
1. Tracker reports `row_conflict`. Scheduler issues PRE when tRAS/tRTP/tWR are satisfied.
2. After tRP, ACT-1/ACT-2 issued. After tRCD, the entry becomes row-hit and follows 6.1.
3. During steps 1-2 the scheduler may issue commands for other banks (bank-level parallelism).

### 6.3 Write with Partial Strobe and Inline ECC
1. AW + W accepted; W data stored in the write data buffer; partial flag set.
2. ECC/RMW engine inserts a read for old data+ECC with a dependency link.
3. After the read returns, data is merged, ECC recomputed, and the write entry becomes eligible.
4. Write issued in WRITE_MODE batch; ECC block updated via the ECC cache.

---

## 7. Clocks and Resets
- Core clock (`clk_core`): `CK / DFI_RATIO`; all controller logic in one domain. DFI is synchronous to `clk_core`.
- AXI ports: each may have its own clock (`aclk[i]`), with async FIFOs in the front end when different from `clk_core` (parameter `AXI_ASYNC`).
- APB clock: separate `pclk` with CDC into the CSR shadow.
- DVFS changes `clk_core` frequency: a glitch-free clock switch is external to this block; the controller exposes handshake signals.
- Reset: asynchronous assert, synchronous deassert per domain. Software soft reset via `GLOBAL.SOFT_RST`.

---

## 8. Verification Hooks and Considerations
- **Assertions (SVA):** timing legality checkers for every tracked constraint independent of the RTL counters (separate implementation inside a bind module).
- **Protocol checkers:** AXI4 VIP, DFI protocol checker (update, low-power, frequency change handshakes).
- **Reference model:** transaction-level scoreboard with memory model including ECC behavior; DRAM behavioral model (LPDDR5 BFM) with timing violation reporting.
- **Coverage targets:** all bank state transitions, R/W mode switching corners, token-bucket empty/full corners, refresh postpone limit, DVFS during traffic, ECC single/double error injection, power-down/self-refresh entry and exit under pending traffic, queue-full and starvation escalation, 1-port vs. 2-port and 1-channel vs. 2-channel configurations.
- **Error injection:** DFI read data corruption, `dfi_error`, PHY update request storms.
- **Performance verification:** bandwidth/latency regression under synthetic traffic (sequential, random, strided, mixed R/W ratios) to validate the scheduler and bandwidth regulators.

---

## 9. Open Items for Next Revision
1. Confirm the `N_AXI_PORTS` / `N_CH` interpretation (independent parameters as drafted here) and the port-to-channel interleave policy.
2. Freeze DFI ratio (1:2 vs. 1:4) with the PHY team; confirm PHY training ownership and PHY-master/controller-master mode.
3. Validate all **[VERIFY]** items against JESD209-5 and DFI 5.0 (WCK sync, ACT-1/ACT-2, refresh limits, FSP/DVFS flows, link ECC, RFM).
4. Decide inline ECC granule and ECC cache sizing; decide scrubbing policy.
5. Define the exact register map (addresses, reset values, access types).
6. Cycle-level pipeline diagrams for the scheduler and the command timing for DFI 1:2 and 1:4.
7. Area/timing estimates for the CAM queues and the parallel page-tracker lookup at the target frequency; decide RDQ/WRQ depths accordingly.
8. QoS policy details: mapping of AXI QoS to classes, urgent-class definition, and interaction with bandwidth regulators.
