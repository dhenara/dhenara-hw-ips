# LPDDR5 controller contracts

These reviewed contracts define the implementation basis and its qualified digital reference models. The source also contains host/control, channel and ECC RTL with owning simulation evidence. Combined controller wiring and verification remain in progress: reference-model and isolated-unit results do not establish complete controller, clock/reset or vendor-PHY qualification. See the [host/control implementation](../architecture/host-control.md), [channel implementation](../architecture/channel.md) and [independent system checks](../verification/system.md) for the current owners and evidence limits.

Use this document and its linked pages for the corrected design. The preserved [v0.1](lpddr5_mc_microarch_spec.md), [v0.2](lpddr5_mc_microarch_addendum_v0.2.md) and [targeted review](review.md) are historical inputs. Their bytes and SHA-256 identities remain checked. Their former unresolved items are addressed below; preserving their text does not make their old status statements current.

## Contract owners and evidence

| Boundary | Canonical source and contract | Executable qualification | Limit |
| --- | --- | --- | --- |
| Supported integration | [Numeric profiles](profiles.md), `config/contracts/profiles.json` and `example.json` | Strict schema/MR/capability admission, independent half-WCK waveforms, invalid profiles and timing fault injection | Restricted digital fixtures; no electrical training margins |
| Commands and data | [Interfaces](interfaces.md), `rtl/contracts/`, `dv/contracts/commands.py` | Literal CA vectors, operand reconstruction, pair constraints, split beats, returned word rotation/skew/gaps/DNV; channel RTL owning checks | Complete combined pin/WCK binding remains to be qualified |
| Host and integrity | [AXI and ECC](host-ecc.md), `host.py`, `integrity.py` | Ordering/errors/fatal settlement, independent link matrices, SECDED faults, RMW/scrub/epochs; host and channel RTL owning checks | Full combined ECC and fault/recovery coverage remains pending |
| Configuration and lifecycle | [Control and recovery](control.md), `registers.json`, `configuration.py`, `lifecycle.py` | APB errors/locks/strobes, mailbox atomicity, boot/refresh/RFM/thermal/PM/FSP/ownership faults; real asynchronous transport simulations | Representative full-wrapper destructive recovery to fresh service passes on an iteration source; broad recovery and physical CDC qualification remain pending |
| Clock/service policy | [Clocking](../architecture/clocking.md), `scheduling.py` | Saturating ages, generation/replay, live issue gates, byte caps and conditional fairness; owning RTL scheduler checks | Final combined issue-gap and fairness evidence remains pending |
| Payload feasibility | [Performance criteria](performance.md), `performance.py` | Independent periodic bank/CA/pin/host ledger, maintenance and checker faults | Feasible schedule; no measured RTL performance |
| Reproduction | [Owning checks](../verification/contracts.md), `tools/`, explicit `files.f` | Generation consistency, Python tests, C header compilation, SV assertions, source hashes/links | CI execution and clean-host portability separately reported |

The source preserves initial commit `b46072841f1a8e89bb110ee2ed578af6fc80dd0a`, shaping ancestor `252648f79786d740c6518abfdb58a325e70af500`, predecessor `441e24c847bd909aaa297ffa3928440b45843c22` and reviewed contract commit `8fb3e4dce57bd0b6c2ef02d4ccb8efe20c2e4f47`. No moving upstream replaces the assigned source. The source-integrity check verifies this ancestry and all three accepted document digests.

## Supported configuration

The contract requires one/two AXI4 ports, one/two independent full-x16 channels, one/two identical 8 Gb ranks per channel, 64/128/256-bit AXI data, 4–16 ID bits, 32–40 address bits, 16/32/64 queue entries and 32/64-byte DRAM granules. Outstanding credits cannot exceed allocated queue capacity. A 64-byte granule is the default. Higher widths are interface options; the same-rate sustained-bandwidth requirement applies only to the declared default benchmark. Owning parameter samples do not close the complete combined matrix.

The digital operating points keep CK/DFI at 800 MHz: WCK:CK=4 at 6400 Mb/s in BG mode, or WCK:CK=2 at 3200 Mb/s in 16B mode. There are three staging FSP banks. Other densities, byte/8B modes, data copy, enhanced/non-target ODT, DVFSC and arbitrary CK rates require new traced profiles before admission. This bounds the delivered fixtures rather than waiving later controller requirements.

Inline ECC is permitted at either ratio. Read/write link ECC is permitted only at ratio four with both discovered DRAM support and the documented raw parity PHY extension. Read link ECC excludes read DBI. Write DBI and masking use the ordered transform in the integrity contract. Profile admission never trusts a user-supplied “qualified” flag. All timings are explicit, range checked and independently evaluated before traffic admission.

Both read/write data CS gap values must be zero in the qualified profiles; nonzero values reject activation. CS remains continuously asserted through split BL32 gaps with an explicit deassertion edge, and rank changes wait for old CS holds and full WCK disable. Posted writes separate physical completion, response eligibility, stable BVALID and actual acknowledgement. Destructive recovery clears volatile maintenance/ownership state while retaining first-fault diagnostics and rejecting stale training/profile epochs. The linked profile, integrity and lifecycle checks exercise these boundaries.

## Resolved design choices

Use one CK-rate command/timing engine per channel. Selection enables at CK, CK/2 or CK/4 reduce new-selection service only; live legality, maintenance and accepted pair completion run every CK. This preserves the original high/reduced-service intent without confusing internal enables with external DFI:WCK ratios. Host and APB clock crossings remain explicit obligations.

Use controller-owned link encoding/decoding, strict drained write epochs by default, full buffer retention until response completion, conservative refresh without postponed debt, mandatory RFM counters and destructive recovery after lost ownership/retention. Synchronization reuse is restricted to the same rank and direction. Rank/owner changes and direction turnarounds stop WCK completely and start fresh synchronization. Synchronized-static restart is rejected.

The selected early controller-update acknowledgement policy remains an integration restriction supported by DFI Table 17 and Figure 33. Table 39's question marks remain source uncertainty: numeric values qualify the supplied model PHY only. No publisher erratum or universal timing limit is asserted.

## Adopted contract basis

The protocol basis uses JESD209-5C and the supplied DFI 5.0 copy with the explicit early-update policy, bounded supported matrix, numeric model-PHY profiles, parity extension and independent digital checks linked above. A new PHY must supply timing/capability evidence and pass the same pin/data checks before enabling traffic.

The implementation contracts use the CK-rate engine, enabled selection, canonical payload/register schema, host ordering, complete word/slice identity, strict/posted ECC responses and the lifecycle/ownership recovery rules on these pages. RTL must implement and test these contracts; reference-model success does not establish multi-clock RTL behavior.

Performance evidence uses the exact full/reduced benchmark, periodic reference schedule, queue/feedback assumptions, completed-payload windows and conditional fairness criteria in [performance.md](performance.md). Preserve 12.8 GB/s combined default same-direction AXI payload. Final acceptance compares combined RTL with this reference and the separate integrity/management matrix.

The reviewed foundation establishes the reference basis. Final controller requirements remain open until the combined RTL and its independent evidence meet these checks.
