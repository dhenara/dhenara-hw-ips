# Clock and scheduling investigation

Use one CK-rate command/timing engine per channel as the starting recommendation. DFI remains equal to CK; internal selection may use clock enables for lower service, while pair completion and timing continue every CK. This removes the proposed slower-scheduler clock crossing and its delayed state/credit feedback. It is not a claim that an 800 MHz implementation meets physical timing. The choice remains subject to the incomplete [contract investigation](../spec/contracts.md).

## Distinct clock meanings

At the 6400 Mb/s fixture CK/DFI is 800 MHz and WCK is 3200 MHz. Each DFI cycle has one 14-bit CA word carrying both CK edges. Four 32-bit data words per DFI cycle carry 128 payload bits per x16 channel. Two 128-bit AXI ports at 400 MHz offer 12.8 GB/s combined in one direction. Two x16 channels offer 25.6 GB/s raw. Neither raw number includes refresh, row changes, ECC or other protocol costs.

At the 3200 Mb/s 2:1 fixture CK/DFI is still 800 MHz while WCK is 1600 MHz. There are two 32-bit data words per DFI cycle per channel. This external ratio is independent of any internal service divider. The original “1:2” and “1:4” internal-core labels must not be reused for external DFI:WCK configuration.

| Choice | Advantages | Costs and missing evidence |
| --- | --- | --- |
| Single CK-rate engine | One authority for live legality and timing; no scheduler/DFI CDC; adjacent ACT and synchronization pair completion | Higher engine clock, possible age/select critical path; pipeline/generation feedback still needs implementation and tests |
| Slower selector with CK-rate adapter | Potentially slower candidate-selection logic | Atomic pair reservations, bounded descriptor lead, asynchronous or mesochronous queues, credits, stale-pick handling and actual feedback latency must all be qualified |

The existing drafts do not prove either implementation. Prefer the first unless implementation evidence shows it cannot meet the intended portable design goals. A later physical frequency result is separate from simulation correctness.

## Finite schedule evidence

The reference schedule assumes pre-opened rows and established synchronization, one direction, BG BL32 and zero maintenance inside the measured window. It issues one 64-byte request every eight CK per channel. Each request occupies two CK of data, an eight-WCK gap (two CK), and two more CK of data. A second reference interleaves two bank groups at offsets zero and two of each eight-CK group. The occupancy checker checks every half-WCK data interval, same-group eight-CK spacing and unique burst identities.

For two channels, the first reference transfers 16 payload bytes per CK across the pair, giving 12.8 GB/s at 800 MHz. This equals the two default AXI ports at 400 MHz. The interleaved reference transfers 32 bytes per CK, showing extra capacity in this specific row-hit pattern. It does not prove that the extra capacity covers a complete real maintenance/row-change workload.

The test uses 64 repetitions, a 512-CK steady-state accounting window. Payload is measured in the output window shifted by the fixed RL, excluding startup. The scripts count the scheduled payload and check pin occupancy; they do not drive AXI ports, measure an RTL return path or account for a complete periodic refresh schedule. The test window must be embedded in a validated refresh/retention envelope before being called a legal complete DRAM workload.

## Pipeline contract still to implement

A queue pick carries entry index, allocation generation and transaction tag. The issue gate must recheck the current generation, row, hazards, data availability, ownership, maintenance deadlines, all timing and pair reservations. Only actual issue advances bank state, charges bandwidth and broadcasts the state event. A stale pick is replayed; it cannot retire an entry or spend credits twice. A selected ACT pair reserves adjacent CK cycles, with ACT-2 as the documented timing origin. A synchronization CAS/read-or-write pair also reserves adjacent CK cycles, independent of a reduced service enable.

Use 16-bit saturating ages and consistent age ticks. Empty current-direction queues must escape minimum batching. Strict token caps need issue-time credit reservation rather than an accepted pipeline overshoot or clamping. Urgency cannot bypass live legality, mandatory maintenance or a strict cap. Fairness guarantees must state enabled caps, feasible traffic and maximum maintenance/ownership pauses. These rules have not been modeled here.

Before performance acceptance, specify outstanding depth, FIFO/response/ECC storage, decision and event latency, warm-up, periodic maintenance, row boundaries, full and reduced windows, tolerances and a finite service bound. Wider AXI ports remain planned options; two 256-bit ports at 400 MHz already equal the two-channel raw peak, leaving no overhead margin. Do not promise the same sustained target for that configuration without a different qualified operating point or workload definition.
