# Canonical consumer interfaces

The explicit source list is [rtl/contracts/files.f](../../rtl/contracts/files.f). It includes the generated register package followed by the handwritten contract package. These are payload/type definitions with representation assertions; they contain no memory controller state machine. Consumers must implement the behavior in the [contracts index](contracts.md).

## Widths and ownership

| Payload | Producer → consumer | Width / lifetime |
| --- | --- | --- |
| AXI address/write/read/B | AXI wrapper ↔ host frontend | Maximum 40-bit address, 16-bit ID, 256-bit data, 32 strobes, LEN8/SIZE3/BURST2; unused upper parameter bits zero; valid/ready outside payload |
| APB request/response | APB wrapper ↔ configuration owner | 18-bit local address, 32-bit data, four strobes, PROT3 and error; PSEL/PENABLE/PREADY outside payload |
| Granule | Host splitter → channel queue | Tag64, generation16, full ID/address, port/channel/rank, BG/bank/row/column, class, 512-bit data and 64 byte enables; 32-byte requests use low half |
| Pick / broadcast event | Selector ↔ live issue owner / all queues | Queue index6, generation16, tag64; revocable until actual issue; event updates all cached state |
| CA | CK engine → PHY | One 14-bit word per CK, low seven rising CA, upper seven falling CA; two rank-select bits; no duplicated command phases |
| Write data | ECC/data owner → PHY | Four 32-bit words, each two x16 pin beats, four byte mask/DBI bits, two slice enables and rank selection; raw parity extension four bits per word |
| Read request / return | Engine ↔ PHY | Per-word/slice enables and chip selects; four 32-bit returned words, per-slice valid, four DNV bits and raw parity per word |
| WCK control | CK engine → PHY | Four WCK phases × two byte slices, one WCK instance per slice; per-phase enable, two rank bits, two-bit toggle encoding |
| Word identity / error | Completion/ECC owner → response/status | Word index4, slice, split half, profile epoch32 and tag/generation; errors preserve full ID/address/tag and correlated/trust flags |
| Management / publication | Lifecycle/configuration owner ↔ PHY/channels | Sampled handshake signals, stable rank states, epochs, request CRC, destination mask and prepare/commit/abort request |

At ratio two only words/phases zero and one are active; upper lanes, valid/enables and unused CS bits are zero. WCK toggle encodings are 00 static-low, 01 static-high, 10 half-rate toggle, 11 fast toggle (DFI Table 38). Internal literal WCK ratio is 2 or 4. DFI `dfi_freq_ratio` encoding is 01 for 1:2 and 10 for 1:4; `lpddr5_mc_dfi_ratio` converts it and returns reserved 11 for invalid input. Frequency codes are system/PHY defined; the integration maps code zero to the 3200 fixture and one to 6400 and declares its supported range. FSP is 0/1/2; three is reserved.

The canonical CA select is logical rank selection. Wrappers map its polarity and constant unused DFI command pins according to the selected memory interface. CA opcodes are independently anchored against JEDEC Table 201, including MRW-1=0x58 on the rising CA edge. Adjacent ACT/MRW pairs retain operands and reserved CA slots; ACT-2 is the bank-timing origin. READ32 may rotate its burst start according to the JEDEC operands; the declared performance fixture uses aligned B3=0. Masked writes use MWR16 only.

The selected raw transport keeps pin beat order and split gaps. A PHY that reshapes BL32 or performs link ECC internally requires a separately specified adapter and independent checks before admission. DFI DBI ownership is MC (`phydbi_mode=0`). Generic `dfi_error` becomes a sticky lifecycle fault and is never substituted for command-correlated link parity.

## Clocks, buffers and reset

AXI clocks are independent and default to 400 MHz; APB is independent. Each channel's engine and DFI use CK=800 MHz in the fixtures. WCK is 1600/3200 MHz and is represented through phase/data words. No slower-selector crossing exists in this architecture. Configuration snapshots and AXI requests/responses cross through the [specified CDC protocol](control.md).

The host reserves one response dependency before accepting each AXI transaction and reserves every resulting granule's data/tracking credits before dispatch. Per-channel queues have 64 entries per direction in the default benchmark. Completion/response buffers and tracking hold up to 64 live granules, including in-service AXI data. Queue retirement at CAS issue releases only queue selection state; write data persists through pin drain/integrity outcome, read tracking persists through every slice/word, and response storage persists through the final handshake. Inline check-cache credits and locks are separate from command credits.

A one-CK registered issue event updates every cached row/credit view. A pick made using old state is only a proposal. At actual issue the sole CK owner rechecks generation, tag, row, overlap hazards, payload availability, owner, refresh/RFM deadlines, all bank/data timings, response/check-cache credits and pair reservations. Failed predictive readiness replays; duplicate/stale identities never change bank or credit state. Only actual issue charges caps. Counter timing runs at every CK even when selection is disabled.

A 16-bit allocation generation cannot wrap in a live epoch. Tags and configuration epochs cannot wrap silently either. Drain and coordinate a fresh epoch before reuse. Reset invalidates profile/training validity and aborts old CDC/completion identities through the host settlement rules. A bit-field width or successful compile does not implement these fences.
