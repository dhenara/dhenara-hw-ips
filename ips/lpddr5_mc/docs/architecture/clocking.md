# Clock and scheduling architecture

Use one CK-rate command/timing engine per channel. DFI=CK=800 MHz in the qualified digital fixtures; WCK=3200 or1600 MHz. Selection uses enables at CK, CK/2 or CK/4. Accepted ACT/CAS pairs complete at the next CK and all timing/maintenance state advances every CK. This avoids a separate slower-selector crossing while preserving high/reduced service. Physical closure at 800 MHz remains an implementation obligation.

| Choice | Benefit | Cost |
| --- | --- | --- |
| Chosen CK engine with enabled selection | One owner for live legality, CA reservations and immediate pair completion; one-CK event feedback | Selection/age critical paths need pipelining, generation checks and implementation validation |
| Slower selector plus CK adapter | Lower selection clock | Requires another CDC/mesochronous queue, descriptor lead, atomic pair reservations, delayed credit/state feedback and separate qualification |

The chosen approach has no evidence-based need for the second clock boundary. Host/APB crossings remain and use the [atomic CDC contract](../spec/control.md). Queue selection and buffer completion are different lifetimes; [canonical interfaces](../spec/interfaces.md) identify their owners. No cache hit, urgency or reduced-service mode bypasses the live issue gate.

The [periodic performance reference](../spec/performance.md) accounts for one-CK feedback, three-CK crossings, split BL32, finite rows, refresh, MR4, ZQ and response storage. It reproduces bounded host backlog while meeting the 12.8 GB/s default combined same-direction target; it replaces the earlier finite pre-opened-row capacity experiment as the feasibility basis. The older `schedule.py` checks remain useful focused collision tests, with their narrower assumptions retained in their own docstrings.

Portable PPA choices are bounded queues, saturating ticked age counters, one state writer, enabled selection and small ECC check caching. No synthesis/library/timing/power result is claimed. Selection comparators, broadcast fanout,128-bit DFI data per channel, host response storage and inline RMW/cache bandwidth are the main implementation risks to measure. Wider AXI options need backpressure and do not create additional memory bandwidth.
