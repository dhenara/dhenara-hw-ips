# LPDDR5 Memory Controller: Microarchitecture Addendum v0.2

**Extends:** `lpddr5_mc_microarch_spec.md` v0.1, sections 5.6 (Scheduler), 5.13 (DFI Master) and 5.14 (CSR).
**Contents:** A. Cycle-level scheduler pipeline. B. DFI 1:2 command and data timing. C. CSR register map.

> **[VERIFY]** marks items to confirm against JESD209-5 (LPDDR5) and DFI 5.0. Numeric DRAM and PHY latencies are always CSR-programmed. Symbols (RL, WL, tRCD...) are used instead of numbers wherever I am not certain of the JEDEC value.

---

# A. Cycle-Level Scheduler Pipeline

## A.1 Timebase and Slot Model

| Item | Value at LPDDR5-6400, DFI 1:2 |
|---|---|
| CK | 800 MHz (1.25 ns) |
| `clk_core` | 400 MHz (2.5 ns), one core cycle = 2 CK |
| BL16 on x16 | 16 UI = 2 CK = **1 core cycle** of data bus time |
| BL32 on x16 | 4 CK = **2 core cycles** of data bus time |
| Command slot | 1 slot per core cycle per channel (assumes a command occupies 2 CK) **[VERIFY per-command CK occupancy]** |

**Design rule:** all timing counters count in **slot units** (1 slot = 2 CK at 1:2). CSR values are programmed in CK and hardware converts with `ceil(CK/2)`. At 1:4 a core cycle holds 2 slots, and the same counters tick at slot rate with 2 slot issues per core cycle.

**Command-bus budget check (important architectural constraint):**

| Access type (64B, row miss, BL32) | Command slots | Data slots | Result |
|---|---|---|---|
| Closed-page / row miss with auto-precharge | ACT-1, ACT-2, RD(A) = 3 (+1 if WCK sync CAS needed) | 2 | Command-bus limited at 3 slots per 2 data slots |
| Row hit, BL32 | 1 | 2 | 1 spare slot per CAS, available to ACT/PRE for other banks |
| Row hit, BL16 (32B) | 1 | 1 | **Zero spare slots**: any ACT/PRE steals data bandwidth |

Consequences: (1) default `BURST_BYTES = 64` (BL32) when the DRAM and AXI granule allow it; (2) the scheduler must treat command slots as a scarce resource, preferring auto-precharge CAS variants over separate PRE, and avoiding speculative PREs when `slot_pressure` is high; (3) the perf counter `CMD_SLOT_UTIL` is mandatory. Re-validate with real per-command CK occupancy.

## A.2 Pipeline Overview

```text
 enqueue     P0 TRACK        P1 FILTER       P2 CLASS        P3 PICK          P4 ISSUE
 --------> +-----------+   +-----------+   +-----------+   +-------------+   +--------------+
  RDQ/WRQ  | bank-ready|   | per-entry |   | R/W mode  |   | oldest in   |   | arbiter      |
  entries  | vectors,  |-->| next cmd  |-->| BW regs,  |-->| top priority|-->| legality gate|--> DFI
           | hit/conf  |   | + eligible|   | QoS, prep |   | mask (tree) |   | CA pack      |
           | flags     |   | masks     |   | masks     |   |             |   | state update |
           +-----------+   +-----------+   +-----------+   +-------------+   +------+-------+
                 ^                                                                  |
                 +-------------- issue event broadcast (bank, row, cmd) ------------+
                 +-------------- in-flight bit set at P3 / cleared on issue or replay
```

All stage boundaries are registered. Target: **one command pick per core cycle sustained**, pipeline depth 5 stages (P3 may split into P3a/P3b if timing at the target frequency requires; this adds one cycle of latency, no throughput loss).

Minimum latency, enqueue to first DRAM command: about 6 core cycles (about 15 ns at 400 MHz). This is a design target.

## A.3 Stage Definitions

### P0: Tracking (state class per entry)
Two implementation options were considered:

| Option | Description | Verdict |
|---|---|---|
| Lookup | Every entry reads the bank table each cycle (64 read ports for 32+32 entries) | Rejected: area and timing |
| **Event broadcast (chosen)** | Each entry caches `state_class` (HIT / CLOSED / CONFLICT). Every ACT/PRE/CAS issue broadcasts `{rank, bank, new_state, new_row}`; entries compare `{rank, bank}` (and row) and update their own flag in one cycle | Chosen |

- On enqueue, the entry's `state_class` is initialized from the bank table (one lookup port for the enqueue path only), with the broadcast event of the same cycle bypassed in.
- **Bank-ready vectors:** the tracker keeps registered vectors `rdy_ACT[b]`, `rdy_PRE[b]`, `rdy_RD[b]`, `rdy_WR[b]` for all `N_RANK x N_BG x N_BANK_PER_BG` banks, computed from the timing counters (including per-BG, per-rank and per-channel terms). Each entry muxes its own bank's bits (a 16:1 mux per vector per rank, cheap).
- **Predictive readiness:** vectors assert when the constraint will be satisfied at the time a command reaches P4, i.e. counter threshold = target minus `PIPE_DEPTH` slots. P4 re-checks exactly (A.4).

### P1: Filter and Next-Command Decode
For each entry:
```text
next_cmd = (state_class == HIT)      ? (dir == RD ? RD : WR)
         : (state_class == CLOSED)   ? ACT
         :                              PRE            // CONFLICT
eligible = rdy_<next_cmd>[bank] & ~hazard_dep_pending & ~inflight
         & ~maint_block[rank,bank] & ~entry_replay_hold
```
- `maint_block` comes from the maintenance block (refresh targeting this bank/rank, ZQ, DVFS quiesce).
- CAS-type next commands also require `wck_ready[rank]` or a WCK sync request path: if WCK is off, the entry's effective next command becomes **WCK-sync CAS** (shared per rank, one sync serves all following CAS commands).
- Outputs (registered): `elig_rd[RDQ_DEPTH]`, `elig_wr[WRQ_DEPTH]`, `ncmd[entry]`, `is_hit[entry]`, `urgent[entry]` (age comparator against `RD_URGENT_AGE`/`WR_AGE_MAX`, QoS class urgent).

### P2: Class Selection
Consumes only **registered** state, so it never sits on a long combinational path behind P1.

```text
inputs : rd_occ, wr_occ (registered counters), mode_reg, batch_cnt,
         bucket_empty[RD/WR/class/port] (registered), turnaround counters,
         oldest_rd_age, oldest_wr_age
outputs: sel_mask (which entries are candidates this cycle)
         mode_next
```

**R/W mode FSM** (states `RD_MODE`, `WR_MODE`, plus transient `TURN_RD2WR`, `TURN_WR2RD`):

| Transition | Condition |
|---|---|
| RD -> WR | `wr_occ >= WR_HI_WM`, or (`rd_occ == 0` and `wr_occ > 0`), or `oldest_wr_age >= WR_AGE_MAX`; and `batch_cnt >= RD_BATCH_MIN`; and (WR bucket not empty or not strict) |
| WR -> RD | `wr_occ <= WR_LO_WM`, or `batch_cnt >= WR_BATCH_MAX`, or `oldest_rd_age >= RD_URGENT_AGE` (urgent read overrides `WR_BATCH_MIN`); and `batch_cnt >= WR_BATCH_MIN` unless urgent |
| Any -> opposite when current direction's bucket empty | strict mode only; work-conserving mode switches only if the other direction has eligible work |

**Turnaround handling:**
- On a decision to switch, CAS of the old direction still in P3/P4 completes. Entries of the old direction in P1/P2 are squashed (their `inflight` bits were never set). Bubble: up to `PIPE_DEPTH - 1` slots unless covered by prep (below).
- **Prep commands across direction (`PREP_OTHER_DIR_EN`):** while in `RD_MODE`, if no read CAS is eligible, ACT/PRE of write entries are allowed (and vice versa). During `TURN_*` states, ACT/PRE for the new direction's entries are allowed so banks are open when the turnaround timers (tRTW, tWTR_L/S, WCK) expire. This hides much of the turnaround cost.
- Guard counters (tRTW, tWTR_*) are enforced both predictively in the ready vectors and exactly in P4.

**Priority masks produced by P2** (consumed by P3):
```text
pri0_mask = sel_mask & urgent        // starved or urgent QoS
pri1_mask = sel_mask & is_hit & ~hitcap_blocked[bank]
pri2_mask = sel_mask & prep_candidates   // ACT/PRE for look-ahead (gated by slot_pressure)
pri3_mask = sel_mask                   // everything else eligible
```
`hitcap_blocked[bank]` is set when `consec_hits[bank] >= MAX_CONSEC_HITS` and another entry to that bank (different row) is waiting.

### P3: Pick
- Select the **oldest** entry in the first non-empty priority mask. Age compare tree: `log2(RDQ_DEPTH)` = 5 levels for 32 entries (6 levels if RD and WR share one tree in `PREP` mode).
- Tie-break (equal age bucket): prefer a bank group different from the last issued CAS (to use tCCD_S), then the bank with the most queued entries.
- Output `pick_valid, pick_idx, pick_cmd`. The entry's `inflight` bit is set **in this stage**, immediately visible to P1 for the following cycle's evaluation, so the same entry is never picked twice.
- **Slot-pressure gating:** when `CMD_SLOT_UTIL` over a short window exceeds a CSR threshold, `pri2` (speculative ACT/PRE for look-ahead) is disabled so CAS commands are not delayed by preparatory commands.

### P4: Issue
1. **Arbiter** (fixed priority, section 5.7 of v0.1): inject/MRW > DVFS/PM > forced refresh > scheduler pick > opportunistic maintenance.
2. **Legality gate:** exact check from live counters and command bus occupancy for the arbitrated command. Pass -> issue. Fail -> replay (A.4).
3. **CA packing** into DFI phase(s) for the slot (section B).
4. **State update:** bank state, timing counters loaded, tFAW timestamp FIFO pushed, `consec_hits` updated, token buckets charged, R/W batch counter incremented.
5. **Broadcast** issue event to the queues (feeds P0), clear `inflight` and retire the entry on CAS issue (or advance `state_class` for ACT/PRE and clear `inflight` so the entry can be picked again for its next command).

## A.4 Pipeline Hazards and Handling

| Hazard | Cause | Mechanism |
|---|---|---|
| Double pick | Entry still eligible in P1 while its pick is in P3/P4 | `inflight` bit set at P3 |
| Stale bank state | Command issued at P4 changes bank state for entries already in P1-P3 | Predictive ready vectors, same-bank and same-BG **shadow masks** for commands currently in P2-P4, exact P4 gate |
| Predictive readiness wrong | Ready vectors optimistic by `PIPE_DEPTH` | P4 gate rejects, entry gets `entry_replay_hold` for 1 slot, `REPLAY_CNT` increments. Replay is a performance event, not a correctness issue |
| Stale row hit | An ACT/PRE for the same bank issued while a CAS pick for the old row sits in P3 | Shadow mask blocks CAS pick if a PRE/ACT to the same bank is in P2-P4; broadcast update squashes P1-P3 entries of that bank next cycle |
| Mode switch with in-flight picks | R/W FSM changes while picks in pipeline | Picks past P3 complete; P1/P2 contents squashed |
| Refresh preempt | Forced refresh needs bank idle | `maint_block` raised 2 cycles ahead (counter threshold minus `PIPE_DEPTH`); pending picks to that bank are replayed or squashed |
| Write data not ready | Write entry eligible before data in buffer | Entry `data_valid` is part of `eligible`; never relies on P4 |

## A.5 Example Pipeline Timeline

Entries: **A** = read row hit (bank 0), **B** = read to closed bank 1, **C** = read to bank 0, different row (conflict). Each row below shows the stage an entry occupies in each core cycle. `t` counts core cycles. DRAM timing waits are shown as "wait".

```text
t       0    1    2    3    4    5    6    7    8   ...  
A       P0   P1   P2   P3   P4(RD)
B            P0   P1   P2   P3   P4(ACT-1)
B'                                    P0  P1  P2  P3  P4(ACT-2)        // ACT-2 as next command, fixed gap
C                 P0   P1   P2   P3   [blocked: A's RD in flight on bank 0,
                                        shadow mask holds PRE until RD->PRE legal (tRTP)]
C                                                            P4(PRE) ...wait tRP... ACT-1, ACT-2 ... RD
```
Reading the example: A issues RD at `t=4`. B's ACT-1 issues the next cycle (bank-level parallelism, different bank group, tRRD_S satisfied). C must wait for tRTP after A's CAS before PRE is legal; the predictive vector `rdy_PRE[bank0]` asserts early enough that C's PRE is picked in the first legal slot.

## A.6 Bandwidth Regulator Timing
- **Token bucket per direction:** `tokens += RD_BW_CAP` once per `BW_WINDOW` cycles (clamped to `BW_BURST`); one token consumed per granule at CAS issue (P4). `bucket_empty` is registered and consumed by P2, so overshoot is bounded by `PIPE_DEPTH` granules. Therefore **`BW_BURST >= PIPE_DEPTH` is required** (hardware clamps).
- **Read/write ratio (`RD_WEIGHT:WR_WEIGHT`):** deficit counters charged at issue, compared in P2 when both directions have eligible work.
- **Class and port buckets:** same structure, applied by masking entries in P2 (`sel_mask &= class_ok[class] & port_ok[port]`).
- **Work-conserving rule:** if all eligible entries belong to depleted buckets and `STRICT = 0`, the mask restriction is lifted for that cycle (no idle slot when work exists).
- **Throttle input:** thermal alert and DVFS can force cap values through the same buckets (shadow override register).

## A.7 Sizing Targets (v0.1 defaults)

| Item | Target |
|---|---|
| Pick rate | 1 per core cycle |
| Pipeline depth (`PIPE_DEPTH`) | 5 (6 with split P3) |
| RDQ / WRQ | 32 / 32 entries, parameterizable 16 to 64 |
| Age counters | 8 bits, tick = `AGE_TICK_DIV` cycles |
| Read tracking FIFO (outstanding reads) | at least `ceil((RL + tphy_rdlat) / slot_per_CAS)`, default 32 |

---

# B. DFI 1:2 Command and Data Timing

## B.1 Phase Mapping (DFI_RATIO = 2)

CK cycle `n` maps to DFI cycle `floor(n/2)`, phase `p(n mod 2)`.

```text
CK cycle :   0    1  |  2    3  |  4    5  |  6    7
DFI cycle:      d0   |     d1   |     d2   |     d3
phase    :  p0   p1  | p0   p1  | p0   p1  | p0   p1
```

**v0.1 rule: commands are phase-aligned** (start at p0 of a DFI cycle) and occupy one slot (p0+p1) **[VERIFY command CK occupancy and alignment rules]**. Consequences:
- `dfi_address_p0/p1`, `dfi_cs_p0/p1` carry the 2-CK command sequence of one command per DFI cycle.
- A timing constraint of an odd number of CK is rounded up to the next even CK. This loses at most 1 CK per constrained pair, and none when constraints are even. A CSR option (`ALLOW_P1_START`, future) may later allow odd-aligned commands.
- At **1:4**, each DFI cycle has p0..p3 and carries **two slots**: slot A on p0/p1 and slot B on p2/p3. Both can carry independent commands (the scheduler issues up to 2 commands per core cycle via a dual-pick or a P4 pair pack, **not** in scope for v0.1; with 1:4 v0.1 issues at most one per core cycle and runs the core at 1:4 clock for lower frequency, losing slot bandwidth. Needs a decision, see open items).

## B.2 DFI Timing Parameters (all CSR, in DFI cycles unless noted) **[VERIFY names and definitions against DFI 5.0]**

| Parameter | Meaning |
|---|---|
| `tctrl_delay` | Controller command path to PHY: delay between the DFI command and the PHY driving it |
| `tphy_wrlat` | From WR command on DFI to `dfi_wrdata_en` assertion |
| `tphy_wrdata` | From `dfi_wrdata_en` to first `dfi_wrdata` beat |
| `trddata_en` | From RD command on DFI to `dfi_rddata_en` assertion |
| `tphy_rdlat` | Maximum from `dfi_rddata_en` to `dfi_rddata_valid` |
| `tphy_wrcslat`, `tphy_rdcslat` | Delay for `dfi_wrdata_cs`, `dfi_rddata_cs` |
| `twck_*` (set) | WCK control latencies: `dfi_wck_en`, `dfi_wck_toggle`, `dfi_wck_cs` relative to the CAS (WCK sync) command |
| `tctrlupd_min/max` | Min and max duration of `dfi_ctrlupd_req` |
| `tphyupd_resp` | Max time controller may take to ack a PHY update request |
| `tinit_start`, `tinit_complete` | Init handshake timing |
| `tlp_resp`, `tlp_wakeup`, `tlp_data_wakeup` | Low-power handshake timing |
| `tdram_clk_disable/enable` | Clock disable/enable latency |

Controller never assumes defaults; the PHY integration guide supplies these. The controller derives its internal delay lines from them.

## B.3 Command and Data Alignment

**Write**
```text
WR cmd on DFI at DFI cycle W
  dfi_wrdata_en       asserted at W + tphy_wrlat        (for the burst length in DFI cycles)
  dfi_wrdata[first]   at W + tphy_wrlat + tphy_wrdata
  at DRAM: first data beat at WR cmd time + WL (in CK)  (PHY/DFI latencies are built to match)
```
Write data delay line length = `tphy_wrlat` DFI cycles. The write data buffer read is issued early enough that data is staged when needed (read latency of the buffer plus ECC encode stage counted in `WR_PIPE_EXTRA`).

**Read**
```text
RD cmd on DFI at DFI cycle R
  dfi_rddata_en       asserted at R + trddata_en        (for the burst length)
  dfi_rddata_valid    returns within tphy_rdlat of dfi_rddata_en
  data returns in command order -> matched against read tracking FIFO
```
Read tracking FIFO entry: `{entry_tag, port, rob_idx, ecc_info, last}`, pushed at RD issue, popped at `dfi_rddata_valid`.

**WCK control**
```text
 WCK OFF
   |  need CAS (RD/WR) for a rank
   v
 issue WCK sync CAS command   -> dfi_wck_en / dfi_wck_toggle driven per twck_* delays [VERIFY]
   |  WCK running and synchronized
   v
 RD/WR allowed (tracker: wck_ready[rank] = 1)
   |  idle for WCK_IDLE_TIMEOUT, no CAS pending
   v
 turn WCK off (dfi_wck_en = 0), unless WCK_ALWAYS_ON
```
The per-rank WCK FSM delays the **first** CAS after WCK start by the sync/enable time, which the scheduler sees as `wck_ready = 0` (the entry's next command shows as WCK-sync). Fast path: when the queue shows a CAS will be eligible within `WCK_PREDICT_WINDOW` slots, the sync command is issued ahead of time (hidden behind tRCD of the ACT).

## B.4 Illustrative Read-Miss Sequence (DFI cycle level, symbolic)

Symbols: `Np = ceil(tRP/2)`, `Nrcd = ceil(tRCD/2)`, `Nsync` = sync-to-CAS gap in DFI cycles. Each row is one DFI cycle where something happens; `...` means idle cycles for waiting.

```text
DFI cyc     dfi_address/cs (p0,p1)         wck             data / enables
d0          PRE bank B                      off
...         (wait Np-1)
d0+Np       ACT-1 bank B row X              off
d0+Np+1     ACT-2 bank B row X              off
...         (wait Nrcd-2, WCK sync CAS issued inside this window so sync is hidden)
d_s         WCK sync CAS                    en/toggle start (twck_* later)
...         (wait Nsync)
d_r         RD (BL32), auto-precharge opt.  running
d_r+trddata_en        .                     dfi_rddata_en pulse (2 DFI cycles for BL32)
...                                          dfi_rddata_valid within tphy_rdlat
```

## B.5 Update, Low-Power and Frequency Handshakes

**Controller update (`dfi_ctrlupd_req/ack`)**
1. Update timer expires (or temperature change event) -> `maint` requests a quiet window.
2. Scheduler stops picking CAS; in-flight CAS and their data transfers complete (drain counter from the read tracking FIFO and write delay line); ACT/PRE may continue if allowed by `CTRLUPD_ALLOW_ACTPRE` **[VERIFY what DFI permits]**.
3. Assert `dfi_ctrlupd_req` for at least `tctrlupd_min`, wait `dfi_ctrlupd_ack`, deassert, resume. If req stays asserted beyond `tctrlupd_max`, raise IRQ.

**PHY update (`dfi_phyupd_req/ack`)**
1. PHY asserts `dfi_phyupd_req` (with `dfi_phyupd_type`).
2. Controller stops issuing new commands, drains in-flight transfers, asserts `dfi_phyupd_ack` within `tphyupd_resp` (programmable guard `PHYUPD_MAX_WAIT`, IRQ `PHYUPD_TIMEOUT` if exceeded).
3. Resume once `dfi_phyupd_req` deasserts. Refresh timers continue to count and the postpone credit absorbs the pause; refresh is forced after the update if credit ran out.

**Low power (`dfi_lp_ctrl_req/ack`, `dfi_lp_data_req/ack`)**
- Self-refresh and power-down entry: close all pages, issue SRE/PDE, then request `dfi_lp_ctrl_req` with the wakeup time hint; wait for `dfi_lp_ctrl_ack`; PHY may gate clocks.
- Exit: deassert request, wait `tlp_wakeup`, issue SRX/PDX, wait tXSR/tXP, resume scheduling.

**Frequency change**
1. DVFS FSM quiesced traffic (section 5.12 of v0.1).
2. Set `dfi_frequency`, `dfi_freq_fsp`, `dfi_freq_ratio` (if changing), assert `dfi_init_start`.
3. External clock switch; wait for `dfi_init_complete` (up to `tinit_complete`).
4. Swap active `TIMING[fsp]` set; PHY retrain (`TRAIN_DONE`) before release.

---

# C. CSR Register Map

## C.1 Conventions
- APB, 32-bit registers, word-aligned addresses, `PSLVERR` on access to unmapped or locked registers.
- Access types: **RW**, **RO**, **W1C** (write 1 to clear), **W1S** (write 1 to set, self-clearing where noted), reserved bits read as 0 and ignore writes.
- **Locking:** `MODE`, `ADDRMAP`, `REGION`, `TIMING[fsp]` of the **active** FSP are writable only while `CTRL.CTRL_EN = 0` (or while quiesced in DVFS). `TIMING[fsp]` of an inactive FSP is always writable (staging for DVFS). Writes to locked registers return `PSLVERR`.
- **Address space:**

| Range | Block |
|---|---|
| `0x0000_0000 - 0x0000_0FFF` | Shared (global) registers |
| `0x0001_0000 + c * 0x1_0000` | Channel `c` registers (c = 0, 1) |

- `GLOBAL.CTRL.CH_BCAST = 1` mirrors writes to channel 0's block into all channel blocks (convenience for symmetric configuration). Reads always return channel 0.

## C.2 Shared Registers (base `0x0000_0000`)

### GLOBAL

| Offset | Name | Bits | Field | Acc | Reset | Description |
|---|---|---|---|---|---|---|
| 0x000 | `ID` | 31:16 | DESIGN_ID | RO | impl. | Design identifier |
| | | 15:8 | VER_MAJOR | RO | 0x00 | |
| | | 7:0 | VER_MINOR | RO | 0x01 | |
| 0x004 | `CONFIG` | 3:0 | N_AXI_PORTS | RO | param | |
| | | 7:4 | N_CH | RO | param | |
| | | 11:8 | N_RANK | RO | param | |
| | | 15:12 | N_BG | RO | param | |
| | | 19:16 | DFI_RATIO | RO | param | 2 or 4 |
| 0x008 | `CTRL` | 0 | CTRL_EN | RW | 0 | Enable controller (traffic accepted) |
| | | 1 | SOFT_RST | W1S | 0 | Self-clearing soft reset (blocks reset first) |
| | | 2 | INIT_START | W1S | 0 | Start DRAM init sequence (needs INIT table programmed) |
| | | 3 | CH_BCAST | RW | 0 | Mirror channel 0 writes to all channels |
| 0x00C | `STATUS` | 0 | INIT_DONE | RO | 0 | |
| | | 1 | DFI_INIT_COMPLETE | RO | 0 | All channels |
| | | 2 | IN_SELF_REFRESH | RO | 0 | |
| | | 3 | IN_POWERDOWN | RO | 0 | |
| | | 4 | DVFS_BUSY | RO | 0 | |
| | | 5 | QUIESCED | RO | 0 | Queues empty, banks closed |
| 0x010 | `MODE` | 1:0 | BANK_MODE | RW | 0 | 0 = BG, 1 = 8B, 2 = 16B **[VERIFY]** |
| | | 2 | BURST_SEL | RW | 1 | 0 = BL16, 1 = BL32 |
| | | 3 | AXI_B_AT_ISSUE | RW | 0 | 0 = B response at queue accept, 1 = at DRAM issue |
| 0x018 | `IRQ_STATUS` | 11:0 | see below | W1C | 0 | |
| 0x01C | `IRQ_MASK` | 11:0 | per-bit enable | RW | 0 | |

IRQ bits: 0 TIMING_VIOL, 1 ECC_CE, 2 ECC_UE, 3 LINK_ECC_ERR, 4 DFI_ERR, 5 THERMAL, 6 REF_OVERDUE, 7 DVFS_DONE, 8 PM_DONE, 9 INIT_DONE, 10 PHYUPD_TIMEOUT, 11 QUEUE_WDT (queue head stuck beyond watchdog).

### ADDRMAP (bit-slice model; exact field widths tunable)

| Offset | Name | Bits | Field | Acc | Reset | Description |
|---|---|---|---|---|---|---|
| 0x100 | `AM_CH` | 5:0 | CH_BIT | RW | 0x08 | Address bit selecting channel |
| | | 8 | CH_INTLV_EN | RW | 1 | |
| | | 14:12 | INTLV_GRAN | RW | 0 | Granule = 256B << value (256B to 4KB) |
| 0x104 | `AM_RANK` | 5:0 | RANK_BIT | RW | 0 | |
| | | 8 | RANK_EN | RW | 0 | |
| 0x108 | `AM_BG` | 5:0 | BG0_BIT | RW | impl. | |
| | | 13:8 | BG1_BIT | RW | impl. | |
| 0x10C | `AM_BANK` | 5:0 | BA0_BIT | RW | impl. | |
| | | 13:8 | BA1_BIT | RW | impl. | |
| 0x110 | `AM_COL` | 5:0 | COL_BASE | RW | 0x05 | Lowest address bit of column field |
| | | 11:8 | COL_WIDTH | RW | 0x0A | |
| 0x114 | `AM_ROW` | 5:0 | ROW_BASE | RW | impl. | |
| | | 11:8 | ROW_WIDTH | RW | 0x0E | |
| 0x118 | `AM_HASH` | 0 | BG_XOR_EN | RW | 0 | BG bits XOR row bits |
| | | 1 | BANK_XOR_EN | RW | 0 | |
| | | 2 | CH_XOR_EN | RW | 0 | |
| | | 13:8 | XOR_ROW_BASE | RW | 0 | First row bit used for hashing |

### REGION[n], n = 0..7 (stride 0x10, base 0x200)

| Offset | Name | Bits | Field | Acc | Reset | Description |
|---|---|---|---|---|---|---|
| +0x0 | `RGN_BASE_L` | 31:0 | BASE[31:0] | RW | 0 | 4KB aligned |
| +0x4 | `RGN_BASE_H` | 7:0 | BASE[39:32] | RW | 0 | |
| +0x8 | `RGN_SIZE` | 31:0 | SIZE (4KB units) | RW | 0 | |
| +0xC | `RGN_ATTR` | 0 | EN | RW | 0 | |
| | | 1 | ECC_EN | RW | 0 | Inline ECC applies to region |
| | | 2 | CH_INTLV_EN | RW | 1 | |
| | | 3 | SECURE | RW | 0 | |
| | | 5:4 | PAGE_POLICY_OVR | RW | 0 | 0 = global, 1 = open, 2 = closed, 3 = adaptive |
| | | 7:6 | QOS_BOOST | RW | 0 | Add to QoS class (saturating) |

Addresses outside all enabled regions return DECERR.

### Ports and QoS

| Offset | Name | Bits | Field | Acc | Reset | Description |
|---|---|---|---|---|---|---|
| 0x300 | `QOS_MAP` | 2i+1:2i | CLASS[i] | RW | linear | Internal class for `AxQOS = i`, i = 0..15 (0 = low, 3 = urgent) |
| 0x304 + 4p | `PORT_CFG[p]` | 7:0 | MAX_OUT_RD | RW | 32 | Outstanding read limit |
| | | 15:8 | MAX_OUT_WR | RW | 32 | Outstanding write limit |
| | | 17:16 | DEFAULT_CLASS | RW | 1 | Used when QOS_OVERRIDE_EN |
| | | 18 | QOS_OVERRIDE_EN | RW | 0 | Ignore AxQOS on this port |

### DVFS

| Offset | Name | Bits | Field | Acc | Reset | Description |
|---|---|---|---|---|---|---|
| 0x400 | `DVFS_CTRL` | 1:0 | TARGET_FSP | RW | 0 | |
| | | 2 | GO | W1S | 0 | Start the switch sequence |
| | | 3 | DRAIN_MODE | RW | 1 | 0 = backpressure immediately, 1 = drain queues first |
| | | 4 | USE_SR | RW | 0 | Switch through self-refresh |
| 0x404 | `DVFS_STATUS` | 1:0 | CUR_FSP | RO | 0 | |
| | | 2 | BUSY | RO | 0 | |
| | | 3 | DONE | W1C | 0 | |
| | | 7:4 | STATE | RO | 0 | FSM state for debug |
| 0x408 | `DVFS_TIMEOUT` | 31:0 | CYCLES | RW | 0x00FF_FFFF | Abort/IRQ threshold |

### INIT table

| Offset | Name | Bits | Field | Acc | Reset | Description |
|---|---|---|---|---|---|---|
| 0x500 + 4k, k = 0..31 | `INIT_MR[k]` | 7:0 | MR_ADDR | RW | 0 | Mode register address |
| | | 15:8 | MR_DATA | RW | 0 | |
| | | 17:16 | RANK_SEL | RW | 3 | Bit per rank |
| | | 23:18 | POST_DELAY | RW | 0 | Wait after write (units of `INIT_DLY_UNIT`) |
| 0x580 | `INIT_CFG` | 5:0 | NUM_MR | RW | 0 | Number of valid table entries |
| | | 31:8 | INIT_DLY_UNIT | RW | 0x100 | Delay unit in core cycles |
| 0x584 | `INIT_TIMES` | 15:0 | T_INIT_RESET | RW | impl. | Reset hold, CKE sequencing counts (units of `INIT_DLY_UNIT`) **[VERIFY LPDDR5 init timing]** |

## C.3 Per-Channel Registers (base `0x0001_0000 + c * 0x1_0000`)

### SCHED (0x000)

| Offset | Name | Bits | Field | Acc | Reset | Description |
|---|---|---|---|---|---|---|
| 0x000 | `SCHED_CTRL` | 1:0 | PAGE_POLICY | RW | 2 | 0 open, 1 closed, 2 adaptive |
| | | 2 | FR_EN | RW | 1 | Row-hit-first |
| | | 3 | PREP_ACT_EN | RW | 1 | Look-ahead ACT/PRE for younger requests |
| | | 4 | PREP_OTHER_DIR_EN | RW | 1 | ACT/PRE for the non-active direction |
| | | 5 | WCK_ALWAYS_ON | RW | 0 | Disable WCK idle shutoff |
| 0x004 | `SCHED_WM` | 7:0 | WR_HI_WM | RW | 24 | |
| | | 15:8 | WR_LO_WM | RW | 8 | |
| 0x008 | `SCHED_BATCH` | 7:0 | RD_BATCH_MIN | RW | 8 | |
| | | 15:8 | WR_BATCH_MIN | RW | 8 | |
| | | 23:16 | WR_BATCH_MAX | RW | 32 | |
| 0x00C | `SCHED_AGE0` | 15:0 | RD_URGENT_AGE | RW | 0x0400 | In age ticks |
| | | 31:16 | WR_AGE_MAX | RW | 0x1000 | |
| 0x010 | `SCHED_AGE1` | 15:0 | AGE_STEP | RW | 0x0100 | Cycles per QoS class escalation |
| | | 23:16 | AGE_TICK_DIV | RW | 16 | Core cycles per age tick |
| 0x014 | `SCHED_HIT` | 7:0 | MAX_CONSEC_HITS | RW | 16 | 0 = unlimited |
| | | 23:16 | SLOT_PRESSURE_THR | RW | 0xC0 | Disable speculative prep above this utilization (of 256) |
| 0x018 | `SCHED_PAGE` | 15:0 | PAGE_IDLE_TIMEOUT | RW | 0x0100 | Cycles |
| | | 17:16 | PRED_INIT | RW | 2 | Initial 2-bit predictor state |

### BWCTRL (0x100)

| Offset | Name | Bits | Field | Acc | Reset | Description |
|---|---|---|---|---|---|---|
| 0x100 | `BW_CTRL` | 0 | BW_CTRL_EN | RW | 0 | 0 = regulators bypassed |
| | | 1 | STRICT | RW | 0 | 0 = work-conserving |
| | | 2 | CLASS_BW_EN | RW | 0 | |
| | | 3 | PORT_BW_EN | RW | 0 | |
| 0x104 | `BW_WINDOW` | 15:0 | BW_WINDOW | RW | 0x0100 | Cycles per refill |
| | | 31:16 | BW_BURST | RW | 0x0010 | Bucket depth, hardware clamps to at least PIPE_DEPTH |
| 0x108 | `RD_BW_CAP` | 15:0 | TOKENS | RW | 0 | Granules per window, 0 = unlimited |
| 0x10C | `WR_BW_CAP` | 15:0 | TOKENS | RW | 0 | |
| 0x110 | `RW_WEIGHT` | 7:0 | RD_WEIGHT | RW | 1 | |
| | | 15:8 | WR_WEIGHT | RW | 1 | |
| 0x114 + 4k | `CLASS_BW[k]` | 15:0 | TOKENS | RW | 0 | k = 0..3 |
| 0x124 + 4p | `PORT_BW[p]` | 15:0 | TOKENS | RW | 0 | p = 0..1 |
| 0x12C | `BW_STATUS` | 0 | RD_EMPTY | RO | 0 | Bucket-empty flags |
| | | 1 | WR_EMPTY | RO | 0 | |
| | | 5:2 | CLASS_EMPTY | RO | 0 | |
| | | 7:6 | PORT_EMPTY | RO | 0 | |

### MAINT (0x200)

| Offset | Name | Bits | Field | Acc | Reset | Description |
|---|---|---|---|---|---|---|
| 0x200 | `REF_CTRL` | 0 | REF_EN | RW | 0 | |
| | | 1 | REFPB_EN | RW | 1 | 0 = all-bank, 1 = per-bank |
| | | 2 | AUTO_RATE_EN | RW | 1 | Follow temperature (MR4) |
| | | 7:4 | POSTPONE_MAX | RW | 4 | Limit per JEDEC **[VERIFY]** |
| | | 11:8 | PULLIN_MAX | RW | 4 | |
| 0x204 | `REF_INT` | 15:0 | tREFI_CNT | RW | impl. | In units of 8 core cycles |
| 0x208 | `ZQ_CTRL` | 0 | ZQ_EN | RW | 0 | |
| | | 31:8 | ZQ_INTERVAL | RW | impl. | Core cycles |
| 0x20C | `TEMP_CTRL` | 0 | TEMP_POLL_EN | RW | 0 | |
| | | 31:8 | TEMP_POLL_INTERVAL | RW | impl. | |
| 0x210 | `TEMP_STATUS` | 7:0 | MR4_VALUE | RO | 0 | |
| | | 8 | THERMAL_ALERT | RO | 0 | |
| 0x214 | `RFM_CTRL` | 0 | RFM_EN | RW | 0 | **[VERIFY]** |
| | | 31:16 | RAAIMT | RW | impl. | Activation count threshold |
| 0x218 | `SCRUB_CTRL` | 0 | SCRUB_EN | RW | 0 | |
| | | 31:8 | SCRUB_INTERVAL | RW | impl. | |
| 0x21C | `SCRUB_ADDR` | 31:0 | ADDR | RO | 0 | Current scrub pointer (granule index) |

### PM (0x300)

| Offset | Name | Bits | Field | Acc | Reset | Description |
|---|---|---|---|---|---|---|
| 0x300 | `PM_CTRL` | 0 | PD_EN | RW | 0 | |
| | | 1 | SR_AUTO_EN | RW | 0 | |
| | | 2 | DPD_EN | RW | 0 | **[VERIFY]** |
| 0x304 | `PD_TMO` | 31:0 | CYCLES | RW | 0x100 | Idle cycles before power-down |
| 0x308 | `SR_TMO` | 31:0 | CYCLES | RW | 0x10000 | |
| 0x30C | `WCK_TMO` | 31:0 | CYCLES | RW | 0x40 | WCK idle shutoff |
| 0x310 | `PM_REQ` | 0 | SR_ENTER | W1S | 0 | |
| | | 1 | SR_EXIT | W1S | 0 | |
| | | 2 | PD_FORCE | W1S | 0 | |
| 0x314 | `PM_STATUS` | 1:0 | STATE | RO | 0 | 0 active, 1 PD, 2 SR, 3 DPD |

### ECC (0x400)

| Offset | Name | Bits | Field | Acc | Reset | Description |
|---|---|---|---|---|---|---|
| 0x400 | `ECC_CTRL` | 1:0 | ECC_MODE | RW | 0 | 0 off, 1 inline, 2 link, 3 both (locked while CTRL_EN) |
| | | 2 | SCRUB_ON_CE | RW | 1 | Write back corrected data |
| | | 3 | ECC_CACHE_EN | RW | 1 | |
| 0x404 | `ECC_CE_CNT` | 31:0 | COUNT | W1C | 0 | Corrected errors |
| 0x408 | `ECC_UE_CNT` | 31:0 | COUNT | W1C | 0 | Uncorrectable errors |
| 0x40C | `ECC_ERR_ADDR_L` | 31:0 | ADDR[31:0] | RO | 0 | Last error address |
| 0x410 | `ECC_ERR_ADDR_H` | 7:0 | ADDR[39:32] | RO | 0 | |
| 0x414 | `ECC_ERR_INFO` | 7:0 | SYNDROME | RO | 0 | |
| | | 15:8 | AXI_ID | RO | 0 | |
| | | 17:16 | PORT | RO | 0 | |
| 0x418 | `ECC_INJECT` | 0 | EN | RW | 0 | Debug error injection |
| | | 1 | DOUBLE | RW | 0 | 0 single bit, 1 double bit |
| | | 2 | ONE_SHOT | RW | 1 | |
| 0x41C | `ECC_CE_THR` | 31:0 | THRESHOLD | RW | 0 | CE count that raises IRQ, 0 = every CE |

### INJECT and TRAIN (0x500)

| Offset | Name | Bits | Field | Acc | Reset | Description |
|---|---|---|---|---|---|---|
| 0x500 | `INJ_CMD` | 0 | START | W1S | 0 | |
| | | 1 | IS_MRR | RW | 0 | 0 = MRW, 1 = MRR |
| | | 9:2 | MR_ADDR | RW | 0 | |
| | | 17:10 | MR_DATA | RW | 0 | MRW data |
| | | 19:18 | RANK | RW | 0 | |
| 0x504 | `INJ_STATUS` | 0 | BUSY | RO | 0 | |
| | | 1 | DONE | W1C | 0 | |
| 0x508 | `INJ_RDATA` | 7:0 | DATA | RO | 0 | MRR result |
| 0x510 | `TRAIN_CTRL` | 0 | TRAIN_HOLD | RW | 0 | Hold scheduler off the DFI |
| | | 1 | TRAIN_REQ | W1S | 0 | |
| | | 2 | PHY_MASTER_EN | RW | 0 | Hand DFI to PHY-controlled training |
| 0x514 | `TRAIN_STATUS` | 0 | TRAIN_DONE | RW1C | 0 | Set by software (or PHY signal) when training finishes |

### TIMING[fsp] (base 0x1000 + fsp * 0x100, fsp = 0..2)

All fields in **CK** unless noted; hardware converts to slots with `ceil(x/2)`.

| Offset | Name | Bits | Field |
|---|---|---|---|
| 0x00 | `T_ROW0` | 7:0 / 15:8 / 23:16 / 31:24 | tRCD / tRP / tRAS / tRC |
| 0x04 | `T_ROW1` | 7:0 / 15:8 / 23:16 | tRRD_L / tRRD_S / tFAW |
| 0x08 | `T_COL` | 7:0 / 15:8 / 23:16 / 31:24 | tCCD_L / tCCD_S / tWTR_L / tWTR_S |
| 0x0C | `T_RW` | 7:0 / 15:8 / 23:16 | tRTP / tWR / tRTW |
| 0x10 | `T_RFC` | 15:0 / 31:16 | tRFCab / tRFCpb |
| 0x14 | `T_PM` | 7:0 / 15:8 / 31:16 | tXP / tCKE / tXSR |
| 0x18 | `T_ZQ` | 15:0 / 31:16 | tZQCAL / tZQLAT |
| 0x1C | `T_LAT` | 7:0 / 15:8 | RL / WL |
| 0x20 | `T_WCK` | 7:0 / 15:8 / 23:16 / 31:24 | WCK sync-to-CAS gap / WCK enable lead RD / WCK enable lead WR / WCK post-amble guard **[VERIFY]** |
| 0x24 | `T_DFI0` | 7:0 / 15:8 / 23:16 / 31:24 | trddata_en / tphy_rdlat / tphy_wrlat / tphy_wrdata (DFI cycles) |
| 0x28 | `T_DFI1` | 7:0 / 15:8 / 23:16 / 31:24 | tctrl_delay / tphy_wrcslat / tphy_rdcslat / tphyupd_resp |
| 0x2C | `T_DFI2` | 15:0 / 31:16 | tctrlupd_min / tctrlupd_max |
| 0x30 | `T_DFI3` | 7:0 / 15:8 / 23:16 / 31:24 | tlp_resp / tlp_wakeup / tinit_start / tdram_clk_disable |
| 0x34 | `T_FSP_MR` | 7:0 / 15:8 | MR13-like FSP control value / MR for CK-WCK ratio setting **[VERIFY]** |

Reset values: all zero. Software must program the active FSP set before `CTRL.INIT_START`.

### PERF and DEBUG (0x2000)

| Offset | Name | Bits | Field | Acc | Reset | Description |
|---|---|---|---|---|---|---|
| 0x2000 | `PERF_CTRL` | 0 | CNT_EN | RW | 0 | |
| | | 1 | CLR | W1S | 0 | Self-clearing |
| | | 2 | SNAPSHOT | W1S | 0 | Latch all counters atomically |
| 0x2010 | `CNT_RD` | 31:0 | | RO | 0 | Read granules accepted |
| 0x2014 | `CNT_WR` | | | RO | 0 | Write granules accepted |
| 0x2018 | `CNT_ROW_HIT` | | | RO | 0 | |
| 0x201C | `CNT_ROW_CLOSED` | | | RO | 0 | |
| 0x2020 | `CNT_ROW_CONF` | | | RO | 0 | |
| 0x2024 | `CNT_ACT` | | | RO | 0 | |
| 0x2028 | `CNT_REF` | | | RO | 0 | |
| 0x202C | `CNT_BUS_BUSY` | | | RO | 0 | Data bus busy cycles |
| 0x2030 | `CNT_CYCLES` | | | RO | 0 | Total cycles counted |
| 0x2034 | `CNT_RW_TURN` | | | RO | 0 | R/W turnarounds |
| 0x2038 | `CNT_STARVE` | | | RO | 0 | Starvation escalations |
| 0x203C | `CNT_REPLAY` | | | RO | 0 | P4 legality-gate replays |
| 0x2040 | `CNT_CMD_SLOTS` | | | RO | 0 | Command slots used (for `CMD_SLOT_UTIL`) |
| 0x2050 + 8k | `LAT_SUM[k]` / `LAT_MAX[k]` | | | RO | 0 | Read latency sum and max per QoS class k = 0..3 (two words per class) |
| 0x2100 | `TRACE_CTRL` | 0 | TRACE_EN | RW | 0 | Command trace capture |
| | | 1 | TRACE_CLR | W1S | 0 | |
| 0x2104 | `TRACE_POP` | 31:0 | ENTRY | RO | 0 | Pop next trace word (cmd, bank, row, timestamp over multiple words) |
| 0x2200 | `VIOL_STATUS` | 31:0 | per constraint | W1C | 0 | One bit per tracked timing constraint |
| 0x2204 | `VIOL_CTRL` | 0 | BLOCK_ON_VIOL | RW | 1 | Block the offending command at P4 |
| 0x2208 | `QUEUE_OCC` | 7:0 | RDQ_OCC | RO | 0 | |
| | | 15:8 | WRQ_OCC | RO | 0 | |
| 0x220C | `BANK_SEL` | 7:0 | RANK/BANK index | RW | 0 | Bank to inspect |
| 0x2210 | `BANK_STATE` | 2:0 | STATE | RO | 0 | IDLE/ACTIVATING/ACTIVE/PRECHARGING/REFRESHING |
| | | 31:8 | OPEN_ROW | RO | 0 | |

---

# D. Open Items Added in v0.2
1. **Per-command CK occupancy and alignment** (affects slot model, A.1 command-bus budget, B.1 phase packing). First thing to verify.
2. **1:4 mode issue width:** one command per core cycle at 1:4 loses command bandwidth. Decide between dual-slot issue (second P3/P4 lane) or restricting 1:4 to lower speed grades.
3. **Odd-aligned command start** (`ALLOW_P1_START`) to recover rounding loss at 1:2.
4. Exact DFI 5.0 rules for what is permitted during `dfi_ctrlupd` and `dfi_phyupd` windows (ACT/PRE, refresh).
5. WCK sync sequence details (CAS command variants, enable lead times, post-amble).
6. Register map review with SW/firmware team: address bit-slice model vs. a table-driven address map, number of regions, TIMING bank count (FSP 0..2).
7. RTL-ready pipeline diagrams with signal names for P0 to P4 and the issue-feedback paths.
