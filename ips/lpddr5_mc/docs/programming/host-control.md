# Host configuration and status programming

This page describes the APB-domain implementation in [lpddr5_mc_apb.sv](../../rtl/control/lpddr5_mc_apb.sv). The canonical [register table](registers.md) and [lifecycle contract](../spec/control.md) remain the authoritative register and channel behavior definitions. The APB block owns staging, command delivery, status capture and all-channel publication. The channel owner supplies actual training, PHY/profile validation, pin/ECC/maintenance drain and destructive-recovery evidence through coherent interfaces.

## Program and publish a configuration

Reset leaves traffic and all timing profiles invalid. Request initialization through CONTROL.INIT, read TRAIN_REQUEST and BOOT_EPOCH, perform the external training procedure, write the matching TRAIN_TOKEN, then write TRAIN_ACK.GO. Epoch zero is a valid initial token. TRAIN_ACK queues a command and never writes TRAIN_DONE; only the hardware owner can publish completion. A stale token produces PSLVERR.

Stage the complete integration image while disabled or quiesced, including MODE, regions and invertible address permutations, scheduler fields and every populated channel's active timing bank. The supplied [example image](../../config/generated/example-register-image.json) is an explicit digital fixture; it is not an automatic profile selector. Inactive FSP banks can be edited while running. FSP_VALID declares the banks to be validated, and a channel must still compare its prepared image with its actual timing/capability descriptor before acknowledging preparation.

With the host settled and traffic disabled or quiesced, write CONFIG_COMMIT.GO. This freezes every staging word, allocates one monotonic publication sequence, computes an image CRC, and sends PREPARE to every populated channel. The CRC is reflected CRC-32 with polynomial `0xEDB88320`, initial value `0xffffffff` and final complement; it consumes each 32-bit word least-significant bit first in schema order. No commit is sent until all channels accept that exact boot epoch, sequence and phase. COMMIT then goes to every channel. PROFILE_VALID and CONFIG_EPOCH change only after every exact commit acknowledgement. A channel must keep normal admission disabled until the coordinator's complete publication result is delivered.

During publication, writes return PSLVERR and leave staging unchanged. Partial acknowledgements do not publish. Negative, duplicate, stale, wrong-phase or wrong-boot acknowledgements invalidate configuration, latch a fault and send ABORT to all channels. A bounded timeout does the same. ABORT itself is bounded; a missing channel cannot silently return the controller to service. Clearing IRQ_STATUS does not clear the fault or restore profile validity. The owner must establish host settlement and confirmed destructive recovery, then complete a new boot/training/profile sequence.

[The C example](../../sw/examples/lpddr5_mc_control.c) demonstrates token acknowledgement and bounded configuration polling through ordered MMIO callbacks. Its wait expiration is only a software timeout, not evidence of DRAM preservation or recovery. The platform-specific driver remains responsible for device access ordering, APB error reporting and reset coordination.

## Access and command rules

The local address is 18 bits, with 32-bit aligned data and four byte strobes. Shared registers use their absolute offsets. Channel registers add `0x10000 + channel*0x10000`. Reads require PSTRB=0. Unmapped/misaligned accesses, writes to read-only fields, locked writes and invalid broadcast complete with PSLVERR and no effects. Reserved bits read zero and ignore writes. PPROT is retained as the most recent completed access diagnostic; it grants no security service.

CONTROL.BROADCAST selects channel writes through the channel-zero window. A broadcast write through channel one fails. Shared register writes update the shared staging image. Each W1S command reads as zero and allocates one mailbox entry per asserted command field; channel broadcasts allocate one per populated destination. The entire entry count, queue capacity and 32-bit sequence capacity are checked before any staging value or mailbox entry changes. W1S payloads retain the merged staging/command value, training token and boot epoch until the command handshake. Command acceptance does not mean that training, low-power, DVFS or recovery has completed.

One APB completion creates one effect. Holding PENABLE high after PREADY does not repeat the command. The master must provide the next SETUP phase before another transfer. Strobe bits affect their corresponding byte lanes. IRQ_STATUS uses W1C with event-set priority when an event and clear coincide; IRQ_MASK gates a level interrupt.

## Coherent diagnostics

The hardware owner transfers a complete status payload through an acknowledged mailbox or asynchronous FIFO into pclk. The APB implementation never samples independently synchronized bits of a multibit register. Each channel status image carries its boot identity in BOOT_EPOCH; stale-boot images are consumed without changing status, IRQs, counters or error detail. Software reads the resulting held image. A performance snapshot response replaces both halves of every reported 64-bit counter together; ordinary status updates do not overwrite the held performance image.

Each channel captures the first uncleared error address, full AXI ID, full tag and ERROR_INFO. Later errors preserve that detail and set detail-overflow while CE/UE event counters keep saturating at `0xffffffff`. ERROR_INFO bits31:18 read zero. Clearing the error event that captured the detail rearms capture; reading a diagnostic has no clear effect. Counter saturation and atomic source sampling are the channel producer's responsibility before crossing; the APB event counters additionally saturate in pclk.

## Source generation and limits

[registers.json](../../config/contracts/registers.json) is the sole register schema. The scoped [metadata generator](../../rtl/control/generate_apb_metadata.py) derives decoder masks, resets, lock classes and snapshot indices without modifying the canonical generated software or RTL products. Run it with `--check` to reject stale local metadata.

The APB-domain block performs structural mode, policy, credit, region/permutation and capacity checks before freezing a snapshot. Each channel must additionally validate all numeric PHY timing, MR and capability requirements and its real drain prerequisites; local structural validation alone never qualifies a PHY. CDC adapters, global admission gating, the channel command owners and destructive reset are integration dependencies. The focused [host verification page](../verification/host-control.md) distinguishes owning checks from combined controller qualification.
