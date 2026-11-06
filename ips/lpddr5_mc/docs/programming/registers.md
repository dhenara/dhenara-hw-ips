# Register contract

Generated from [registers.json](../../config/contracts/registers.json). See [control and recovery](../spec/control.md) for atomic publication, APB4 errors, ownership and clock crossings.

Shared addresses are absolute. Channel offsets add `0x10000 + channel * 0x10000`. RW configuration fields write staging state; mailbox RW fields hold the next command payload. Active state is identified by CONFIG_EPOCH. Unlisted bits read zero and ignore writes. All counters saturate. Snapshot captures both halves of every performance counter together.

| Name | Scope / offset | Reset | Lock | Owner / CDC | Fields (lsb:width access unit) |
| --- | --- | --- | --- | --- | --- |
| ID | shared / 0x0000 | 0x4c500200 | none | constant / none | VALUE (0:32 ro integer): value |
| CONFIG | shared / 0x0004 | 0x00000000 | none | constant / none | PORTS (0:4 ro integer): ports; CHANNELS (4:4 ro integer): channels; RANKS (8:4 ro integer): ranks; AXI_BYTES (12:6 ro integer): axi bytes |
| CONTROL | shared / 0x0008 | 0x00000000 | none | software / mailbox | ENABLE (0:1 w1s integer): enable; QUIESCE (1:1 w1s integer): quiesce; INIT (2:1 w1s integer): init; RECOVER_DESTRUCTIVE (3:1 w1s integer): recover destructive; BROADCAST (4:1 rw integer): broadcast |
| STATUS | shared / 0x000c | 0x00000000 | none | hardware / snapshot | PROFILE_VALID (0:1 ro integer): profile valid; TRAIN_DONE (1:1 ro integer): train done; QUIESCED (2:1 ro integer): quiesced; TRUSTED (3:1 ro integer): trusted; FAULT (4:1 ro integer): fault; STATE (8:4 ro integer): state |
| IRQ_STATUS | shared / 0x0018 | 0x00000000 | none | hardware_set_software_clear / sticky_set_dominates | EVENTS (0:16 w1c integer): events |
| IRQ_MASK | shared / 0x001c | 0x00000000 | none | software / mailbox | EVENTS (0:16 rw integer): events |
| CONFIG_COMMIT | shared / 0x0020 | 0x00000000 | quiesced | software / mailbox | GO (0:1 w1s integer): go |
| CONFIG_EPOCH | shared / 0x0024 | 0x00000000 | none | hardware / snapshot | EPOCH (0:32 ro integer): epoch |
| MODE | shared / 0x0028 | 0x00000101 | disabled | software / snapshot | BURST64 (0:1 rw integer): burst64; INLINE_ECC (1:1 rw integer): inline ecc; LINK_READ (2:1 rw integer): link read; LINK_WRITE (3:1 rw integer): link write; POSTED_B (4:1 rw integer): posted b; SERVICE_DIV (8:3 rw integer): service div |
| DVFS | shared / 0x0030 | 0x00000000 | quiesced | software / mailbox | FSP (0:2 rw integer): fsp; GO (8:1 w1s integer): go |
| TRAIN_ACK | shared / 0x0034 | 0x00000000 | quiesced | software / mailbox | GO (0:1 w1s command): Acknowledge current TRAIN_REQUEST with TRAIN_TOKEN matching BOOT_EPOCH. |
| QOS_MAP | shared / 0x0040 | 0xffaa5500 | disabled | software / snapshot | CLASSES (0:32 rw integer): classes |
| PORT_LIMIT_0 | shared / 0x0050 | 0x00002020 | disabled | software / snapshot | READ (0:8 rw integer): read; WRITE (8:8 rw integer): write |
| PORT_LIMIT_1 | shared / 0x0054 | 0x00002020 | disabled | software / snapshot | READ (0:8 rw integer): read; WRITE (8:8 rw integer): write |
| REGION_0_BASE_LO | shared / 0x0100 | 0x00000000 | disabled | software / snapshot | VALUE (0:32 rw integer): value |
| REGION_0_BASE_HI | shared / 0x0104 | 0x00000000 | disabled | software / snapshot | VALUE (0:8 rw integer): value |
| REGION_0_SIZE_4K | shared / 0x0108 | 0x00000000 | disabled | software / snapshot | VALUE (0:28 rw 4096_bytes): value |
| REGION_0_ATTR | shared / 0x010c | 0x00000000 | disabled | software / snapshot | ENABLE (0:1 rw integer): Region valid; CHANNEL_MODE (1:2 rw integer): 0 channel0, 1 channel1, 2 64-byte interleave; RANKS (3:2 rw integer): Populated rank count; INLINE (5:1 rw integer): Protected reserved-capacity region |
| REGION_0_MAP | shared / 0x0110 | 0x00000000 | disabled | software / snapshot | VALUE (0:1 rw integer): 0 linear, 1 explicit 40-entry bit permutation; preserve byte/granule bits |
| REGION_1_BASE_LO | shared / 0x0120 | 0x00000000 | disabled | software / snapshot | VALUE (0:32 rw integer): value |
| REGION_1_BASE_HI | shared / 0x0124 | 0x00000000 | disabled | software / snapshot | VALUE (0:8 rw integer): value |
| REGION_1_SIZE_4K | shared / 0x0128 | 0x00000000 | disabled | software / snapshot | VALUE (0:28 rw 4096_bytes): value |
| REGION_1_ATTR | shared / 0x012c | 0x00000000 | disabled | software / snapshot | ENABLE (0:1 rw integer): Region valid; CHANNEL_MODE (1:2 rw integer): 0 channel0, 1 channel1, 2 64-byte interleave; RANKS (3:2 rw integer): Populated rank count; INLINE (5:1 rw integer): Protected reserved-capacity region |
| REGION_1_MAP | shared / 0x0130 | 0x00000000 | disabled | software / snapshot | VALUE (0:1 rw integer): 0 linear, 1 explicit 40-entry bit permutation; preserve byte/granule bits |
| REGION_2_BASE_LO | shared / 0x0140 | 0x00000000 | disabled | software / snapshot | VALUE (0:32 rw integer): value |
| REGION_2_BASE_HI | shared / 0x0144 | 0x00000000 | disabled | software / snapshot | VALUE (0:8 rw integer): value |
| REGION_2_SIZE_4K | shared / 0x0148 | 0x00000000 | disabled | software / snapshot | VALUE (0:28 rw 4096_bytes): value |
| REGION_2_ATTR | shared / 0x014c | 0x00000000 | disabled | software / snapshot | ENABLE (0:1 rw integer): Region valid; CHANNEL_MODE (1:2 rw integer): 0 channel0, 1 channel1, 2 64-byte interleave; RANKS (3:2 rw integer): Populated rank count; INLINE (5:1 rw integer): Protected reserved-capacity region |
| REGION_2_MAP | shared / 0x0150 | 0x00000000 | disabled | software / snapshot | VALUE (0:1 rw integer): 0 linear, 1 explicit 40-entry bit permutation; preserve byte/granule bits |
| REGION_3_BASE_LO | shared / 0x0160 | 0x00000000 | disabled | software / snapshot | VALUE (0:32 rw integer): value |
| REGION_3_BASE_HI | shared / 0x0164 | 0x00000000 | disabled | software / snapshot | VALUE (0:8 rw integer): value |
| REGION_3_SIZE_4K | shared / 0x0168 | 0x00000000 | disabled | software / snapshot | VALUE (0:28 rw 4096_bytes): value |
| REGION_3_ATTR | shared / 0x016c | 0x00000000 | disabled | software / snapshot | ENABLE (0:1 rw integer): Region valid; CHANNEL_MODE (1:2 rw integer): 0 channel0, 1 channel1, 2 64-byte interleave; RANKS (3:2 rw integer): Populated rank count; INLINE (5:1 rw integer): Protected reserved-capacity region |
| REGION_3_MAP | shared / 0x0170 | 0x00000000 | disabled | software / snapshot | VALUE (0:1 rw integer): 0 linear, 1 explicit 40-entry bit permutation; preserve byte/granule bits |
| REGION_4_BASE_LO | shared / 0x0180 | 0x00000000 | disabled | software / snapshot | VALUE (0:32 rw integer): value |
| REGION_4_BASE_HI | shared / 0x0184 | 0x00000000 | disabled | software / snapshot | VALUE (0:8 rw integer): value |
| REGION_4_SIZE_4K | shared / 0x0188 | 0x00000000 | disabled | software / snapshot | VALUE (0:28 rw 4096_bytes): value |
| REGION_4_ATTR | shared / 0x018c | 0x00000000 | disabled | software / snapshot | ENABLE (0:1 rw integer): Region valid; CHANNEL_MODE (1:2 rw integer): 0 channel0, 1 channel1, 2 64-byte interleave; RANKS (3:2 rw integer): Populated rank count; INLINE (5:1 rw integer): Protected reserved-capacity region |
| REGION_4_MAP | shared / 0x0190 | 0x00000000 | disabled | software / snapshot | VALUE (0:1 rw integer): 0 linear, 1 explicit 40-entry bit permutation; preserve byte/granule bits |
| REGION_5_BASE_LO | shared / 0x01a0 | 0x00000000 | disabled | software / snapshot | VALUE (0:32 rw integer): value |
| REGION_5_BASE_HI | shared / 0x01a4 | 0x00000000 | disabled | software / snapshot | VALUE (0:8 rw integer): value |
| REGION_5_SIZE_4K | shared / 0x01a8 | 0x00000000 | disabled | software / snapshot | VALUE (0:28 rw 4096_bytes): value |
| REGION_5_ATTR | shared / 0x01ac | 0x00000000 | disabled | software / snapshot | ENABLE (0:1 rw integer): Region valid; CHANNEL_MODE (1:2 rw integer): 0 channel0, 1 channel1, 2 64-byte interleave; RANKS (3:2 rw integer): Populated rank count; INLINE (5:1 rw integer): Protected reserved-capacity region |
| REGION_5_MAP | shared / 0x01b0 | 0x00000000 | disabled | software / snapshot | VALUE (0:1 rw integer): 0 linear, 1 explicit 40-entry bit permutation; preserve byte/granule bits |
| REGION_6_BASE_LO | shared / 0x01c0 | 0x00000000 | disabled | software / snapshot | VALUE (0:32 rw integer): value |
| REGION_6_BASE_HI | shared / 0x01c4 | 0x00000000 | disabled | software / snapshot | VALUE (0:8 rw integer): value |
| REGION_6_SIZE_4K | shared / 0x01c8 | 0x00000000 | disabled | software / snapshot | VALUE (0:28 rw 4096_bytes): value |
| REGION_6_ATTR | shared / 0x01cc | 0x00000000 | disabled | software / snapshot | ENABLE (0:1 rw integer): Region valid; CHANNEL_MODE (1:2 rw integer): 0 channel0, 1 channel1, 2 64-byte interleave; RANKS (3:2 rw integer): Populated rank count; INLINE (5:1 rw integer): Protected reserved-capacity region |
| REGION_6_MAP | shared / 0x01d0 | 0x00000000 | disabled | software / snapshot | VALUE (0:1 rw integer): 0 linear, 1 explicit 40-entry bit permutation; preserve byte/granule bits |
| REGION_7_BASE_LO | shared / 0x01e0 | 0x00000000 | disabled | software / snapshot | VALUE (0:32 rw integer): value |
| REGION_7_BASE_HI | shared / 0x01e4 | 0x00000000 | disabled | software / snapshot | VALUE (0:8 rw integer): value |
| REGION_7_SIZE_4K | shared / 0x01e8 | 0x00000000 | disabled | software / snapshot | VALUE (0:28 rw 4096_bytes): value |
| REGION_7_ATTR | shared / 0x01ec | 0x00000000 | disabled | software / snapshot | ENABLE (0:1 rw integer): Region valid; CHANNEL_MODE (1:2 rw integer): 0 channel0, 1 channel1, 2 64-byte interleave; RANKS (3:2 rw integer): Populated rank count; INLINE (5:1 rw integer): Protected reserved-capacity region |
| REGION_7_MAP | shared / 0x01f0 | 0x00000000 | disabled | software / snapshot | VALUE (0:1 rw integer): 0 linear, 1 explicit 40-entry bit permutation; preserve byte/granule bits |
| SCHED_RD_AGE | channel / 0x0000 | 0x00000400 | quiesced | software / snapshot | VALUE (0:16 rw age_ticks): value |
| SCHED_WR_AGE | channel / 0x0004 | 0x00001000 | quiesced | software / snapshot | VALUE (0:16 rw age_ticks): value |
| AGE_STEP | channel / 0x0008 | 0x00000100 | quiesced | software / snapshot | VALUE (0:16 rw age_ticks): value |
| AGE_TICK_DIV | channel / 0x000c | 0x00000010 | quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| WR_HIGH | channel / 0x0010 | 0x00000030 | quiesced | software / snapshot | VALUE (0:7 rw entries): value |
| WR_LOW | channel / 0x0014 | 0x00000010 | quiesced | software / snapshot | VALUE (0:7 rw entries): value |
| RD_BATCH_MIN | channel / 0x0018 | 0x00000008 | quiesced | software / snapshot | VALUE (0:8 rw granules): value |
| WR_BATCH_MIN | channel / 0x001c | 0x00000008 | quiesced | software / snapshot | VALUE (0:8 rw granules): value |
| WR_BATCH_MAX | channel / 0x0020 | 0x00000020 | quiesced | software / snapshot | VALUE (0:8 rw granules): value |
| HIT_CAP | channel / 0x0024 | 0x00000010 | quiesced | software / snapshot | VALUE (0:16 rw granules): value |
| PAGE_IDLE | channel / 0x0028 | 0x00000100 | quiesced | software / snapshot | VALUE (0:32 rw CK): value |
| BW_WINDOW | channel / 0x0040 | 0x00000100 | quiesced | software / snapshot | VALUE (0:32 rw CK): value |
| BW_BURST | channel / 0x0044 | 0x00000400 | quiesced | software / snapshot | VALUE (0:32 rw bytes): value |
| RD_CAP | channel / 0x0048 | 0x00000000 | quiesced | software / snapshot | VALUE (0:32 rw bytes_per_window): value |
| WR_CAP | channel / 0x004c | 0x00000000 | quiesced | software / snapshot | VALUE (0:32 rw bytes_per_window): value |
| RD_WEIGHT | channel / 0x0050 | 0x00000001 | quiesced | software / snapshot | VALUE (0:16 rw weight): value |
| WR_WEIGHT | channel / 0x0054 | 0x00000001 | quiesced | software / snapshot | VALUE (0:16 rw weight): value |
| BW_MODE | channel / 0x0058 | 0x00000000 | quiesced | software / snapshot | VALUE (0:2 rw enum): value |
| PD_IDLE | channel / 0x0080 | 0x00000100 | quiesced | software / snapshot | VALUE (0:32 rw CK): value |
| SR_IDLE | channel / 0x0084 | 0x00010000 | quiesced | software / snapshot | VALUE (0:32 rw CK): value |
| WCK_IDLE | channel / 0x0088 | 0x00000040 | quiesced | software / snapshot | VALUE (0:32 rw CK): value |
| PM_REQUEST | channel / 0x008c | 0x00000000 | quiesced | software / snapshot | VALUE (0:4 rw enum): value |
| TEMP_POLL | channel / 0x0090 | 0x000f4240 | quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| ZQ_INTERVAL | channel / 0x0094 | 0x000f4240 | quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| DEVICE_MR8 | channel / 0x0200 | 0x00000000 | none | hardware / snapshot | VALUE (0:8 ro integer): value |
| DEVICE_MR27 | channel / 0x0204 | 0x00000000 | none | hardware / snapshot | VALUE (0:8 ro integer): value |
| DEVICE_MR57 | channel / 0x0208 | 0x00000000 | none | hardware / snapshot | VALUE (0:8 ro integer): value |
| TEMP_MR4 | channel / 0x020c | 0x00000000 | none | hardware / snapshot | VALUE (0:8 ro integer): value |
| ERROR_ADDR_LO | channel / 0x0240 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| ERROR_ADDR_HI | channel / 0x0244 | 0x00000000 | none | hardware / snapshot | VALUE (0:8 ro integer): value |
| ERROR_AXI_ID | channel / 0x0248 | 0x00000000 | none | hardware / snapshot | VALUE (0:16 ro integer): value |
| ERROR_TAG_LO | channel / 0x024c | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| ERROR_TAG_HI | channel / 0x0250 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| ERROR_INFO | channel / 0x0254 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| CE_COUNT | channel / 0x0258 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| UE_COUNT | channel / 0x025c | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_SNAPSHOT | channel / 0x0280 | 0x00000000 | none | software / mailbox | GO (0:1 w1s integer): go |
| PERF_PAYLOAD_RD_0 | channel / 0x0290 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_PAYLOAD_RD_1 | channel / 0x0294 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_PAYLOAD_WR_0 | channel / 0x0298 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_PAYLOAD_WR_1 | channel / 0x029c | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_COMMANDS_0 | channel / 0x02a0 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_COMMANDS_1 | channel / 0x02a4 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_REPLAYS_0 | channel / 0x02a8 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_REPLAYS_1 | channel / 0x02ac | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_MAINT_CK_0 | channel / 0x02b0 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_MAINT_CK_1 | channel / 0x02b4 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_MAX_LATENCY_0 | channel / 0x02b8 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_MAX_LATENCY_1 | channel / 0x02bc | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_FAIR_WAIT_0 | channel / 0x02c0 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_FAIR_WAIT_1 | channel / 0x02c4 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_CYCLES_0 | channel / 0x02c8 | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| PERF_CYCLES_1 | channel / 0x02cc | 0x00000000 | none | hardware / snapshot | VALUE (0:32 ro integer): value |
| FSP0_TRCD | channel / 0x1000 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TRP_PB | channel / 0x1004 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TRP_AB | channel / 0x1008 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TRAS_MIN | channel / 0x100c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TRAS_MAX | channel / 0x1010 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| FSP0_TRC | channel / 0x1014 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TRRD | channel / 0x1018 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TFAW | channel / 0x101c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TWR | channel / 0x1020 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TRBTP | channel / 0x1024 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TWTR_S | channel / 0x1028 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TWTR_L | channel / 0x102c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TRTW | channel / 0x1030 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TPPD | channel / 0x1034 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TRFC_AB | channel / 0x1038 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TRFC_PB | channel / 0x103c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TRFM_AB | channel / 0x1040 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TRFM_PB | channel / 0x1044 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TPBR2PBR | channel / 0x1048 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TPBR2ACT | channel / 0x104c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TREFI | channel / 0x1050 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| FSP0_TXP | channel / 0x1054 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TCKE | channel / 0x1058 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TXSR | channel / 0x105c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TZQCAL | channel / 0x1060 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| FSP0_TZQLAT | channel / 0x1064 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TMRD | channel / 0x1068 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TMRR | channel / 0x106c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TMRW | channel / 0x1070 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_RL | channel / 0x1074 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_WL | channel / 0x1078 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TCMD_LAT | channel / 0x107c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TCTRL_DELAY | channel / 0x1080 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TPHY_WRLAT | channel / 0x1084 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TPHY_WRDATA | channel / 0x1088 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TWRDATA_DELAY | channel / 0x108c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TRDDATA_EN | channel / 0x1090 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TPHY_RDLAT | channel / 0x1094 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TPHY_WRCSLAT | channel / 0x1098 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TPHY_RDCSLAT | channel / 0x109c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TPHY_WRCSGAP | channel / 0x10a0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): Additional rank-change CS hold in DFI data clocks. The qualified reference profile requires zero; nonzero values reject activation. CS stays continuous through split BL32. |
| FSP0_TPHY_RDCSGAP | channel / 0x10a4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): Additional rank-change CS hold in DFI data clocks. The qualified reference profile requires zero; nonzero values reject activation. CS stays continuous through split BL32. |
| FSP0_TCTRLUPD_MIN | channel / 0x10a8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TCTRLUPD_MAX | channel / 0x10ac | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TPHYUPD_RESP | channel / 0x10b0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TPHYUPD_TYPE0 | channel / 0x10b4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TPHYUPD_TYPE1 | channel / 0x10b8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TPHYUPD_TYPE2 | channel / 0x10bc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TPHYUPD_TYPE3 | channel / 0x10c0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TINIT_START | channel / 0x10c4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TINIT_COMPLETE | channel / 0x10c8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| FSP0_TLP_RESP | channel / 0x10cc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TLP_CTRL_WAKEUP | channel / 0x10d0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TLP_DATA_WAKEUP | channel / 0x10d4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TDRAM_CLK_DISABLE | channel / 0x10d8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_TDRAM_CLK_ENABLE | channel / 0x10dc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP0_RETENTION_BUDGET | channel / 0x10e0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| FSP0_TWCK_DIS | channel / 0x10e4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TWCK_EN_FS | channel / 0x10e8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TWCK_EN_RD | channel / 0x10ec | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TWCK_EN_WR | channel / 0x10f0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TWCK_FAST_TOGGLE | channel / 0x10f4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TWCK_TOGGLE | channel / 0x10f8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TWCK_TOGGLE_CS | channel / 0x10fc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TWCK_TOGGLE_POST | channel / 0x1100 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TWCK_TOGGLE_RD | channel / 0x1104 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_TWCK_TOGGLE_WR | channel / 0x1108 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP0_MR1 | channel / 0x1180 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR2 | channel / 0x1184 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR3 | channel / 0x1188 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR10 | channel / 0x118c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR11 | channel / 0x1190 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR13 | channel / 0x1194 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR16 | channel / 0x1198 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR18 | channel / 0x119c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR19 | channel / 0x11a0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR20 | channel / 0x11a4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR21 | channel / 0x11a8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR22 | channel / 0x11ac | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR23 | channel / 0x11b0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR24 | channel / 0x11b4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR25 | channel / 0x11b8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR26 | channel / 0x11bc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR28 | channel / 0x11c0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR41 | channel / 0x11c4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR46 | channel / 0x11c8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP0_MR57 | channel / 0x11cc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_TRCD | channel / 0x1400 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TRP_PB | channel / 0x1404 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TRP_AB | channel / 0x1408 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TRAS_MIN | channel / 0x140c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TRAS_MAX | channel / 0x1410 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| FSP1_TRC | channel / 0x1414 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TRRD | channel / 0x1418 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TFAW | channel / 0x141c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TWR | channel / 0x1420 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TRBTP | channel / 0x1424 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TWTR_S | channel / 0x1428 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TWTR_L | channel / 0x142c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TRTW | channel / 0x1430 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TPPD | channel / 0x1434 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TRFC_AB | channel / 0x1438 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TRFC_PB | channel / 0x143c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TRFM_AB | channel / 0x1440 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TRFM_PB | channel / 0x1444 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TPBR2PBR | channel / 0x1448 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TPBR2ACT | channel / 0x144c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TREFI | channel / 0x1450 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| FSP1_TXP | channel / 0x1454 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TCKE | channel / 0x1458 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TXSR | channel / 0x145c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TZQCAL | channel / 0x1460 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| FSP1_TZQLAT | channel / 0x1464 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TMRD | channel / 0x1468 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TMRR | channel / 0x146c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TMRW | channel / 0x1470 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_RL | channel / 0x1474 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_WL | channel / 0x1478 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TCMD_LAT | channel / 0x147c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TCTRL_DELAY | channel / 0x1480 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TPHY_WRLAT | channel / 0x1484 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TPHY_WRDATA | channel / 0x1488 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TWRDATA_DELAY | channel / 0x148c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TRDDATA_EN | channel / 0x1490 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TPHY_RDLAT | channel / 0x1494 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TPHY_WRCSLAT | channel / 0x1498 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TPHY_RDCSLAT | channel / 0x149c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TPHY_WRCSGAP | channel / 0x14a0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): Additional rank-change CS hold in DFI data clocks. The qualified reference profile requires zero; nonzero values reject activation. CS stays continuous through split BL32. |
| FSP1_TPHY_RDCSGAP | channel / 0x14a4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): Additional rank-change CS hold in DFI data clocks. The qualified reference profile requires zero; nonzero values reject activation. CS stays continuous through split BL32. |
| FSP1_TCTRLUPD_MIN | channel / 0x14a8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TCTRLUPD_MAX | channel / 0x14ac | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TPHYUPD_RESP | channel / 0x14b0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TPHYUPD_TYPE0 | channel / 0x14b4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TPHYUPD_TYPE1 | channel / 0x14b8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TPHYUPD_TYPE2 | channel / 0x14bc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TPHYUPD_TYPE3 | channel / 0x14c0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TINIT_START | channel / 0x14c4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TINIT_COMPLETE | channel / 0x14c8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| FSP1_TLP_RESP | channel / 0x14cc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TLP_CTRL_WAKEUP | channel / 0x14d0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TLP_DATA_WAKEUP | channel / 0x14d4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TDRAM_CLK_DISABLE | channel / 0x14d8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_TDRAM_CLK_ENABLE | channel / 0x14dc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP1_RETENTION_BUDGET | channel / 0x14e0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| FSP1_TWCK_DIS | channel / 0x14e4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TWCK_EN_FS | channel / 0x14e8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TWCK_EN_RD | channel / 0x14ec | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TWCK_EN_WR | channel / 0x14f0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TWCK_FAST_TOGGLE | channel / 0x14f4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TWCK_TOGGLE | channel / 0x14f8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TWCK_TOGGLE_CS | channel / 0x14fc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TWCK_TOGGLE_POST | channel / 0x1500 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TWCK_TOGGLE_RD | channel / 0x1504 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_TWCK_TOGGLE_WR | channel / 0x1508 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP1_MR1 | channel / 0x1580 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR2 | channel / 0x1584 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR3 | channel / 0x1588 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR10 | channel / 0x158c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR11 | channel / 0x1590 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR13 | channel / 0x1594 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR16 | channel / 0x1598 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR18 | channel / 0x159c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR19 | channel / 0x15a0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR20 | channel / 0x15a4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR21 | channel / 0x15a8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR22 | channel / 0x15ac | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR23 | channel / 0x15b0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR24 | channel / 0x15b4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR25 | channel / 0x15b8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR26 | channel / 0x15bc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR28 | channel / 0x15c0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR41 | channel / 0x15c4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR46 | channel / 0x15c8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP1_MR57 | channel / 0x15cc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_TRCD | channel / 0x1800 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TRP_PB | channel / 0x1804 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TRP_AB | channel / 0x1808 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TRAS_MIN | channel / 0x180c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TRAS_MAX | channel / 0x1810 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| FSP2_TRC | channel / 0x1814 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TRRD | channel / 0x1818 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TFAW | channel / 0x181c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TWR | channel / 0x1820 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TRBTP | channel / 0x1824 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TWTR_S | channel / 0x1828 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TWTR_L | channel / 0x182c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TRTW | channel / 0x1830 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TPPD | channel / 0x1834 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TRFC_AB | channel / 0x1838 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TRFC_PB | channel / 0x183c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TRFM_AB | channel / 0x1840 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TRFM_PB | channel / 0x1844 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TPBR2PBR | channel / 0x1848 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TPBR2ACT | channel / 0x184c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TREFI | channel / 0x1850 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| FSP2_TXP | channel / 0x1854 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TCKE | channel / 0x1858 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TXSR | channel / 0x185c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TZQCAL | channel / 0x1860 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| FSP2_TZQLAT | channel / 0x1864 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TMRD | channel / 0x1868 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TMRR | channel / 0x186c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TMRW | channel / 0x1870 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_RL | channel / 0x1874 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_WL | channel / 0x1878 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TCMD_LAT | channel / 0x187c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TCTRL_DELAY | channel / 0x1880 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TPHY_WRLAT | channel / 0x1884 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TPHY_WRDATA | channel / 0x1888 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TWRDATA_DELAY | channel / 0x188c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TRDDATA_EN | channel / 0x1890 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TPHY_RDLAT | channel / 0x1894 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TPHY_WRCSLAT | channel / 0x1898 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TPHY_RDCSLAT | channel / 0x189c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TPHY_WRCSGAP | channel / 0x18a0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): Additional rank-change CS hold in DFI data clocks. The qualified reference profile requires zero; nonzero values reject activation. CS stays continuous through split BL32. |
| FSP2_TPHY_RDCSGAP | channel / 0x18a4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): Additional rank-change CS hold in DFI data clocks. The qualified reference profile requires zero; nonzero values reject activation. CS stays continuous through split BL32. |
| FSP2_TCTRLUPD_MIN | channel / 0x18a8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TCTRLUPD_MAX | channel / 0x18ac | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TPHYUPD_RESP | channel / 0x18b0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TPHYUPD_TYPE0 | channel / 0x18b4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TPHYUPD_TYPE1 | channel / 0x18b8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TPHYUPD_TYPE2 | channel / 0x18bc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TPHYUPD_TYPE3 | channel / 0x18c0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TINIT_START | channel / 0x18c4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TINIT_COMPLETE | channel / 0x18c8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| FSP2_TLP_RESP | channel / 0x18cc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TLP_CTRL_WAKEUP | channel / 0x18d0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TLP_DATA_WAKEUP | channel / 0x18d4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TDRAM_CLK_DISABLE | channel / 0x18d8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_TDRAM_CLK_ENABLE | channel / 0x18dc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw CK): value |
| FSP2_RETENTION_BUDGET | channel / 0x18e0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:32 rw ns): value |
| FSP2_TWCK_DIS | channel / 0x18e4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TWCK_EN_FS | channel / 0x18e8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TWCK_EN_RD | channel / 0x18ec | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TWCK_EN_WR | channel / 0x18f0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TWCK_FAST_TOGGLE | channel / 0x18f4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TWCK_TOGGLE | channel / 0x18f8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TWCK_TOGGLE_CS | channel / 0x18fc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TWCK_TOGGLE_POST | channel / 0x1900 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TWCK_TOGGLE_RD | channel / 0x1904 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_TWCK_TOGGLE_WR | channel / 0x1908 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:16 rw WCK): value |
| FSP2_MR1 | channel / 0x1980 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR2 | channel / 0x1984 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR3 | channel / 0x1988 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR10 | channel / 0x198c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR11 | channel / 0x1990 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR13 | channel / 0x1994 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR16 | channel / 0x1998 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR18 | channel / 0x199c | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR19 | channel / 0x19a0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR20 | channel / 0x19a4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR21 | channel / 0x19a8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR22 | channel / 0x19ac | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR23 | channel / 0x19b0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR24 | channel / 0x19b4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR25 | channel / 0x19b8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR26 | channel / 0x19bc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR28 | channel / 0x19c0 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR41 | channel / 0x19c4 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR46 | channel / 0x19c8 | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| FSP2_MR57 | channel / 0x19cc | 0x00000000 | inactive_or_quiesced | software / snapshot | VALUE (0:8 rw MR_image): value |
| REGION_0_BIT_0 | shared / 0x2000 | 0x00000000 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_1 | shared / 0x2004 | 0x00000001 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_2 | shared / 0x2008 | 0x00000002 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_3 | shared / 0x200c | 0x00000003 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_4 | shared / 0x2010 | 0x00000004 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_5 | shared / 0x2014 | 0x00000005 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_6 | shared / 0x2018 | 0x00000006 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_7 | shared / 0x201c | 0x00000007 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_8 | shared / 0x2020 | 0x00000008 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_9 | shared / 0x2024 | 0x00000009 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_10 | shared / 0x2028 | 0x0000000a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_11 | shared / 0x202c | 0x0000000b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_12 | shared / 0x2030 | 0x0000000c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_13 | shared / 0x2034 | 0x0000000d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_14 | shared / 0x2038 | 0x0000000e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_15 | shared / 0x203c | 0x0000000f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_16 | shared / 0x2040 | 0x00000010 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_17 | shared / 0x2044 | 0x00000011 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_18 | shared / 0x2048 | 0x00000012 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_19 | shared / 0x204c | 0x00000013 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_20 | shared / 0x2050 | 0x00000014 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_21 | shared / 0x2054 | 0x00000015 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_22 | shared / 0x2058 | 0x00000016 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_23 | shared / 0x205c | 0x00000017 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_24 | shared / 0x2060 | 0x00000018 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_25 | shared / 0x2064 | 0x00000019 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_26 | shared / 0x2068 | 0x0000001a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_27 | shared / 0x206c | 0x0000001b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_28 | shared / 0x2070 | 0x0000001c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_29 | shared / 0x2074 | 0x0000001d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_30 | shared / 0x2078 | 0x0000001e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_31 | shared / 0x207c | 0x0000001f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_32 | shared / 0x2080 | 0x00000020 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_33 | shared / 0x2084 | 0x00000021 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_34 | shared / 0x2088 | 0x00000022 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_35 | shared / 0x208c | 0x00000023 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_36 | shared / 0x2090 | 0x00000024 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_37 | shared / 0x2094 | 0x00000025 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_38 | shared / 0x2098 | 0x00000026 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_0_BIT_39 | shared / 0x209c | 0x00000027 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_0 | shared / 0x2100 | 0x00000000 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_1 | shared / 0x2104 | 0x00000001 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_2 | shared / 0x2108 | 0x00000002 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_3 | shared / 0x210c | 0x00000003 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_4 | shared / 0x2110 | 0x00000004 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_5 | shared / 0x2114 | 0x00000005 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_6 | shared / 0x2118 | 0x00000006 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_7 | shared / 0x211c | 0x00000007 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_8 | shared / 0x2120 | 0x00000008 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_9 | shared / 0x2124 | 0x00000009 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_10 | shared / 0x2128 | 0x0000000a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_11 | shared / 0x212c | 0x0000000b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_12 | shared / 0x2130 | 0x0000000c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_13 | shared / 0x2134 | 0x0000000d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_14 | shared / 0x2138 | 0x0000000e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_15 | shared / 0x213c | 0x0000000f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_16 | shared / 0x2140 | 0x00000010 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_17 | shared / 0x2144 | 0x00000011 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_18 | shared / 0x2148 | 0x00000012 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_19 | shared / 0x214c | 0x00000013 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_20 | shared / 0x2150 | 0x00000014 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_21 | shared / 0x2154 | 0x00000015 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_22 | shared / 0x2158 | 0x00000016 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_23 | shared / 0x215c | 0x00000017 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_24 | shared / 0x2160 | 0x00000018 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_25 | shared / 0x2164 | 0x00000019 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_26 | shared / 0x2168 | 0x0000001a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_27 | shared / 0x216c | 0x0000001b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_28 | shared / 0x2170 | 0x0000001c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_29 | shared / 0x2174 | 0x0000001d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_30 | shared / 0x2178 | 0x0000001e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_31 | shared / 0x217c | 0x0000001f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_32 | shared / 0x2180 | 0x00000020 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_33 | shared / 0x2184 | 0x00000021 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_34 | shared / 0x2188 | 0x00000022 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_35 | shared / 0x218c | 0x00000023 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_36 | shared / 0x2190 | 0x00000024 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_37 | shared / 0x2194 | 0x00000025 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_38 | shared / 0x2198 | 0x00000026 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_1_BIT_39 | shared / 0x219c | 0x00000027 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_0 | shared / 0x2200 | 0x00000000 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_1 | shared / 0x2204 | 0x00000001 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_2 | shared / 0x2208 | 0x00000002 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_3 | shared / 0x220c | 0x00000003 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_4 | shared / 0x2210 | 0x00000004 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_5 | shared / 0x2214 | 0x00000005 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_6 | shared / 0x2218 | 0x00000006 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_7 | shared / 0x221c | 0x00000007 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_8 | shared / 0x2220 | 0x00000008 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_9 | shared / 0x2224 | 0x00000009 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_10 | shared / 0x2228 | 0x0000000a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_11 | shared / 0x222c | 0x0000000b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_12 | shared / 0x2230 | 0x0000000c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_13 | shared / 0x2234 | 0x0000000d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_14 | shared / 0x2238 | 0x0000000e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_15 | shared / 0x223c | 0x0000000f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_16 | shared / 0x2240 | 0x00000010 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_17 | shared / 0x2244 | 0x00000011 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_18 | shared / 0x2248 | 0x00000012 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_19 | shared / 0x224c | 0x00000013 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_20 | shared / 0x2250 | 0x00000014 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_21 | shared / 0x2254 | 0x00000015 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_22 | shared / 0x2258 | 0x00000016 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_23 | shared / 0x225c | 0x00000017 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_24 | shared / 0x2260 | 0x00000018 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_25 | shared / 0x2264 | 0x00000019 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_26 | shared / 0x2268 | 0x0000001a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_27 | shared / 0x226c | 0x0000001b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_28 | shared / 0x2270 | 0x0000001c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_29 | shared / 0x2274 | 0x0000001d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_30 | shared / 0x2278 | 0x0000001e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_31 | shared / 0x227c | 0x0000001f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_32 | shared / 0x2280 | 0x00000020 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_33 | shared / 0x2284 | 0x00000021 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_34 | shared / 0x2288 | 0x00000022 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_35 | shared / 0x228c | 0x00000023 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_36 | shared / 0x2290 | 0x00000024 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_37 | shared / 0x2294 | 0x00000025 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_38 | shared / 0x2298 | 0x00000026 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_2_BIT_39 | shared / 0x229c | 0x00000027 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_0 | shared / 0x2300 | 0x00000000 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_1 | shared / 0x2304 | 0x00000001 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_2 | shared / 0x2308 | 0x00000002 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_3 | shared / 0x230c | 0x00000003 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_4 | shared / 0x2310 | 0x00000004 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_5 | shared / 0x2314 | 0x00000005 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_6 | shared / 0x2318 | 0x00000006 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_7 | shared / 0x231c | 0x00000007 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_8 | shared / 0x2320 | 0x00000008 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_9 | shared / 0x2324 | 0x00000009 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_10 | shared / 0x2328 | 0x0000000a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_11 | shared / 0x232c | 0x0000000b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_12 | shared / 0x2330 | 0x0000000c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_13 | shared / 0x2334 | 0x0000000d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_14 | shared / 0x2338 | 0x0000000e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_15 | shared / 0x233c | 0x0000000f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_16 | shared / 0x2340 | 0x00000010 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_17 | shared / 0x2344 | 0x00000011 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_18 | shared / 0x2348 | 0x00000012 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_19 | shared / 0x234c | 0x00000013 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_20 | shared / 0x2350 | 0x00000014 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_21 | shared / 0x2354 | 0x00000015 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_22 | shared / 0x2358 | 0x00000016 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_23 | shared / 0x235c | 0x00000017 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_24 | shared / 0x2360 | 0x00000018 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_25 | shared / 0x2364 | 0x00000019 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_26 | shared / 0x2368 | 0x0000001a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_27 | shared / 0x236c | 0x0000001b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_28 | shared / 0x2370 | 0x0000001c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_29 | shared / 0x2374 | 0x0000001d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_30 | shared / 0x2378 | 0x0000001e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_31 | shared / 0x237c | 0x0000001f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_32 | shared / 0x2380 | 0x00000020 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_33 | shared / 0x2384 | 0x00000021 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_34 | shared / 0x2388 | 0x00000022 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_35 | shared / 0x238c | 0x00000023 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_36 | shared / 0x2390 | 0x00000024 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_37 | shared / 0x2394 | 0x00000025 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_38 | shared / 0x2398 | 0x00000026 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_3_BIT_39 | shared / 0x239c | 0x00000027 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_0 | shared / 0x2400 | 0x00000000 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_1 | shared / 0x2404 | 0x00000001 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_2 | shared / 0x2408 | 0x00000002 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_3 | shared / 0x240c | 0x00000003 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_4 | shared / 0x2410 | 0x00000004 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_5 | shared / 0x2414 | 0x00000005 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_6 | shared / 0x2418 | 0x00000006 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_7 | shared / 0x241c | 0x00000007 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_8 | shared / 0x2420 | 0x00000008 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_9 | shared / 0x2424 | 0x00000009 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_10 | shared / 0x2428 | 0x0000000a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_11 | shared / 0x242c | 0x0000000b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_12 | shared / 0x2430 | 0x0000000c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_13 | shared / 0x2434 | 0x0000000d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_14 | shared / 0x2438 | 0x0000000e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_15 | shared / 0x243c | 0x0000000f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_16 | shared / 0x2440 | 0x00000010 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_17 | shared / 0x2444 | 0x00000011 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_18 | shared / 0x2448 | 0x00000012 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_19 | shared / 0x244c | 0x00000013 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_20 | shared / 0x2450 | 0x00000014 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_21 | shared / 0x2454 | 0x00000015 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_22 | shared / 0x2458 | 0x00000016 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_23 | shared / 0x245c | 0x00000017 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_24 | shared / 0x2460 | 0x00000018 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_25 | shared / 0x2464 | 0x00000019 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_26 | shared / 0x2468 | 0x0000001a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_27 | shared / 0x246c | 0x0000001b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_28 | shared / 0x2470 | 0x0000001c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_29 | shared / 0x2474 | 0x0000001d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_30 | shared / 0x2478 | 0x0000001e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_31 | shared / 0x247c | 0x0000001f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_32 | shared / 0x2480 | 0x00000020 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_33 | shared / 0x2484 | 0x00000021 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_34 | shared / 0x2488 | 0x00000022 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_35 | shared / 0x248c | 0x00000023 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_36 | shared / 0x2490 | 0x00000024 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_37 | shared / 0x2494 | 0x00000025 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_38 | shared / 0x2498 | 0x00000026 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_4_BIT_39 | shared / 0x249c | 0x00000027 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_0 | shared / 0x2500 | 0x00000000 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_1 | shared / 0x2504 | 0x00000001 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_2 | shared / 0x2508 | 0x00000002 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_3 | shared / 0x250c | 0x00000003 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_4 | shared / 0x2510 | 0x00000004 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_5 | shared / 0x2514 | 0x00000005 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_6 | shared / 0x2518 | 0x00000006 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_7 | shared / 0x251c | 0x00000007 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_8 | shared / 0x2520 | 0x00000008 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_9 | shared / 0x2524 | 0x00000009 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_10 | shared / 0x2528 | 0x0000000a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_11 | shared / 0x252c | 0x0000000b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_12 | shared / 0x2530 | 0x0000000c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_13 | shared / 0x2534 | 0x0000000d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_14 | shared / 0x2538 | 0x0000000e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_15 | shared / 0x253c | 0x0000000f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_16 | shared / 0x2540 | 0x00000010 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_17 | shared / 0x2544 | 0x00000011 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_18 | shared / 0x2548 | 0x00000012 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_19 | shared / 0x254c | 0x00000013 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_20 | shared / 0x2550 | 0x00000014 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_21 | shared / 0x2554 | 0x00000015 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_22 | shared / 0x2558 | 0x00000016 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_23 | shared / 0x255c | 0x00000017 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_24 | shared / 0x2560 | 0x00000018 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_25 | shared / 0x2564 | 0x00000019 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_26 | shared / 0x2568 | 0x0000001a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_27 | shared / 0x256c | 0x0000001b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_28 | shared / 0x2570 | 0x0000001c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_29 | shared / 0x2574 | 0x0000001d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_30 | shared / 0x2578 | 0x0000001e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_31 | shared / 0x257c | 0x0000001f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_32 | shared / 0x2580 | 0x00000020 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_33 | shared / 0x2584 | 0x00000021 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_34 | shared / 0x2588 | 0x00000022 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_35 | shared / 0x258c | 0x00000023 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_36 | shared / 0x2590 | 0x00000024 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_37 | shared / 0x2594 | 0x00000025 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_38 | shared / 0x2598 | 0x00000026 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_5_BIT_39 | shared / 0x259c | 0x00000027 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_0 | shared / 0x2600 | 0x00000000 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_1 | shared / 0x2604 | 0x00000001 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_2 | shared / 0x2608 | 0x00000002 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_3 | shared / 0x260c | 0x00000003 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_4 | shared / 0x2610 | 0x00000004 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_5 | shared / 0x2614 | 0x00000005 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_6 | shared / 0x2618 | 0x00000006 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_7 | shared / 0x261c | 0x00000007 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_8 | shared / 0x2620 | 0x00000008 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_9 | shared / 0x2624 | 0x00000009 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_10 | shared / 0x2628 | 0x0000000a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_11 | shared / 0x262c | 0x0000000b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_12 | shared / 0x2630 | 0x0000000c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_13 | shared / 0x2634 | 0x0000000d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_14 | shared / 0x2638 | 0x0000000e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_15 | shared / 0x263c | 0x0000000f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_16 | shared / 0x2640 | 0x00000010 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_17 | shared / 0x2644 | 0x00000011 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_18 | shared / 0x2648 | 0x00000012 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_19 | shared / 0x264c | 0x00000013 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_20 | shared / 0x2650 | 0x00000014 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_21 | shared / 0x2654 | 0x00000015 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_22 | shared / 0x2658 | 0x00000016 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_23 | shared / 0x265c | 0x00000017 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_24 | shared / 0x2660 | 0x00000018 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_25 | shared / 0x2664 | 0x00000019 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_26 | shared / 0x2668 | 0x0000001a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_27 | shared / 0x266c | 0x0000001b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_28 | shared / 0x2670 | 0x0000001c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_29 | shared / 0x2674 | 0x0000001d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_30 | shared / 0x2678 | 0x0000001e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_31 | shared / 0x267c | 0x0000001f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_32 | shared / 0x2680 | 0x00000020 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_33 | shared / 0x2684 | 0x00000021 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_34 | shared / 0x2688 | 0x00000022 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_35 | shared / 0x268c | 0x00000023 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_36 | shared / 0x2690 | 0x00000024 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_37 | shared / 0x2694 | 0x00000025 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_38 | shared / 0x2698 | 0x00000026 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_6_BIT_39 | shared / 0x269c | 0x00000027 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_0 | shared / 0x2700 | 0x00000000 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_1 | shared / 0x2704 | 0x00000001 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_2 | shared / 0x2708 | 0x00000002 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_3 | shared / 0x270c | 0x00000003 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_4 | shared / 0x2710 | 0x00000004 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_5 | shared / 0x2714 | 0x00000005 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_6 | shared / 0x2718 | 0x00000006 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_7 | shared / 0x271c | 0x00000007 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_8 | shared / 0x2720 | 0x00000008 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_9 | shared / 0x2724 | 0x00000009 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_10 | shared / 0x2728 | 0x0000000a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_11 | shared / 0x272c | 0x0000000b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_12 | shared / 0x2730 | 0x0000000c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_13 | shared / 0x2734 | 0x0000000d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_14 | shared / 0x2738 | 0x0000000e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_15 | shared / 0x273c | 0x0000000f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_16 | shared / 0x2740 | 0x00000010 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_17 | shared / 0x2744 | 0x00000011 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_18 | shared / 0x2748 | 0x00000012 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_19 | shared / 0x274c | 0x00000013 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_20 | shared / 0x2750 | 0x00000014 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_21 | shared / 0x2754 | 0x00000015 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_22 | shared / 0x2758 | 0x00000016 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_23 | shared / 0x275c | 0x00000017 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_24 | shared / 0x2760 | 0x00000018 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_25 | shared / 0x2764 | 0x00000019 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_26 | shared / 0x2768 | 0x0000001a | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_27 | shared / 0x276c | 0x0000001b | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_28 | shared / 0x2770 | 0x0000001c | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_29 | shared / 0x2774 | 0x0000001d | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_30 | shared / 0x2778 | 0x0000001e | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_31 | shared / 0x277c | 0x0000001f | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_32 | shared / 0x2780 | 0x00000020 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_33 | shared / 0x2784 | 0x00000021 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_34 | shared / 0x2788 | 0x00000022 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_35 | shared / 0x278c | 0x00000023 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_36 | shared / 0x2790 | 0x00000024 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_37 | shared / 0x2794 | 0x00000025 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_38 | shared / 0x2798 | 0x00000026 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| REGION_7_BIT_39 | shared / 0x279c | 0x00000027 | disabled | software / snapshot | SOURCE (0:6 rw byte_address_bit): Output bit selects region-relative input bit |
| FSP0_TPHYMSTR_RESP | channel / 0x1120 | 0x00000000 | inactive_or_quiesced | MC / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP0_TPHYMSTR_RFSH | channel / 0x1124 | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP0_TPHYMSTR_TYPE0 | channel / 0x1128 | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP0_TPHYMSTR_TYPE1 | channel / 0x112c | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP0_TPHYMSTR_TYPE2 | channel / 0x1130 | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP0_TPHYMSTR_TYPE3 | channel / 0x1134 | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP0_TINIT_RESP | channel / 0x1138 | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP0_BOOT_TIMEOUT | channel / 0x113c | 0x00000000 | inactive_or_quiesced | system / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP0_MR43_POLL_BOUND | channel / 0x1140 | 0x00000000 | inactive_or_quiesced | system / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP0_MR43_EPOCH_LIMIT | channel / 0x1144 | 0x00000000 | inactive_or_quiesced | system / snapshot | VALUE (0:16 rw count): Explicit integration bound; zero invalid |
| FSP1_TPHYMSTR_RESP | channel / 0x1520 | 0x00000000 | inactive_or_quiesced | MC / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP1_TPHYMSTR_RFSH | channel / 0x1524 | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP1_TPHYMSTR_TYPE0 | channel / 0x1528 | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP1_TPHYMSTR_TYPE1 | channel / 0x152c | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP1_TPHYMSTR_TYPE2 | channel / 0x1530 | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP1_TPHYMSTR_TYPE3 | channel / 0x1534 | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP1_TINIT_RESP | channel / 0x1538 | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP1_BOOT_TIMEOUT | channel / 0x153c | 0x00000000 | inactive_or_quiesced | system / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP1_MR43_POLL_BOUND | channel / 0x1540 | 0x00000000 | inactive_or_quiesced | system / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP1_MR43_EPOCH_LIMIT | channel / 0x1544 | 0x00000000 | inactive_or_quiesced | system / snapshot | VALUE (0:16 rw count): Explicit integration bound; zero invalid |
| FSP2_TPHYMSTR_RESP | channel / 0x1920 | 0x00000000 | inactive_or_quiesced | MC / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP2_TPHYMSTR_RFSH | channel / 0x1924 | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP2_TPHYMSTR_TYPE0 | channel / 0x1928 | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP2_TPHYMSTR_TYPE1 | channel / 0x192c | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP2_TPHYMSTR_TYPE2 | channel / 0x1930 | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP2_TPHYMSTR_TYPE3 | channel / 0x1934 | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP2_TINIT_RESP | channel / 0x1938 | 0x00000000 | inactive_or_quiesced | PHY / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP2_BOOT_TIMEOUT | channel / 0x193c | 0x00000000 | inactive_or_quiesced | system / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP2_MR43_POLL_BOUND | channel / 0x1940 | 0x00000000 | inactive_or_quiesced | system / snapshot | VALUE (0:16 rw CK): Explicit integration bound; zero invalid |
| FSP2_MR43_EPOCH_LIMIT | channel / 0x1944 | 0x00000000 | inactive_or_quiesced | system / snapshot | VALUE (0:16 rw count): Explicit integration bound; zero invalid |
| BOOT_EPOCH | shared / 0x0038 | 0x00000000 | none | controller / status_snapshot | VALUE (0:32 ro epoch): boot epoch |
| TRAIN_REQUEST | shared / 0x003c | 0x00000000 | none | controller / status_snapshot | VALUE (0:1 ro epoch): Pending training request |
| TRAIN_DONE | shared / 0x0044 | 0x00000000 | none | controller / status_snapshot | VALUE (0:1 ro epoch): Current boot epoch training acknowledged |
| FSP0_TFC | channel / 0x1150 | 0x00000000 | inactive_or_quiesced | JEDEC / snapshot | VALUE (0:16 rw CK): FSP change AC minimum |
| FSP0_TCKFSPE | channel / 0x1154 | 0x00000000 | inactive_or_quiesced | JEDEC / snapshot | VALUE (0:16 rw CK): FSP change AC minimum |
| FSP0_TCKFSPX | channel / 0x1158 | 0x00000000 | inactive_or_quiesced | JEDEC / snapshot | VALUE (0:16 rw CK): FSP change AC minimum |
| FSP1_TFC | channel / 0x1550 | 0x00000000 | inactive_or_quiesced | JEDEC / snapshot | VALUE (0:16 rw CK): FSP change AC minimum |
| FSP1_TCKFSPE | channel / 0x1554 | 0x00000000 | inactive_or_quiesced | JEDEC / snapshot | VALUE (0:16 rw CK): FSP change AC minimum |
| FSP1_TCKFSPX | channel / 0x1558 | 0x00000000 | inactive_or_quiesced | JEDEC / snapshot | VALUE (0:16 rw CK): FSP change AC minimum |
| FSP2_TFC | channel / 0x1950 | 0x00000000 | inactive_or_quiesced | JEDEC / snapshot | VALUE (0:16 rw CK): FSP change AC minimum |
| FSP2_TCKFSPE | channel / 0x1954 | 0x00000000 | inactive_or_quiesced | JEDEC / snapshot | VALUE (0:16 rw CK): FSP change AC minimum |
| FSP2_TCKFSPX | channel / 0x1958 | 0x00000000 | inactive_or_quiesced | JEDEC / snapshot | VALUE (0:16 rw CK): FSP change AC minimum |
| FSP_VALID | channel / 0x0180 | 0x00000000 | quiesced | software / snapshot | BANKS (0:3 rw bitmask): Explicit banks to validate before publication |
| TRAIN_TOKEN | shared / 0x0048 | 0x00000000 | quiesced | software / mailbox | EPOCH (0:32 rw epoch): Token latched by TRAIN_ACK; zero is valid for the first boot. |
