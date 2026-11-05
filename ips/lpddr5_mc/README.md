# LPDDR5 memory controller

Technology-independent memory-controller IP in the specification stage. The supplied drafts target LPDDR5-6400, AXI4 request ports, a DFI 5.0 interface to an external PHY, and APB configuration registers. They describe independent one- or two-port and one- or two-channel configurations, scheduling and QoS, refresh, power management, frequency changes, and ECC.

There is no RTL, simulation environment, formal proof, synthesis result, or measured performance yet. The directories alongside this README are a scaffold for those deliverables.

## Start here

- [Specification index and import checks](docs/spec/README.md)
- [Draft v0.1 microarchitecture specification](docs/spec/lpddr5_mc_microarch_spec.md)
- [Draft v0.2 scheduler, DFI, and CSR addendum](docs/spec/lpddr5_mc_microarch_addendum_v0.2.md)
- [Review findings and scope decisions](docs/spec/review.md)
- [Repository conventions](../../docs/development/repository-layout.md)

## Current direction

The owner confirmed a generic PHY-facing design, technology-independent implementation with power, performance, and area as optimization goals, and simulation-verified RTL as the first delivery. The higher-throughput profile must match the combined payload bandwidth of the two AXI ports in a declared feasible configuration; a reduced-throughput profile is acceptable. The original drafts call these 1:2 and 1:4 relative to CK. The supplied DFI 5.0 copy instead keeps DFI clock equal to CK and defines the LPDDR5 ratio relative to WCK, so the clock-domain contract must be corrected without changing the performance intent. The review records the measurement proposal and remaining protocol prerequisites. The recommendation is to evaluate a CK-rate DFI boundary with a slower internal scheduler; its buffering, feedback and throughput remain unqualified. The DFI reference also contains unresolved WCK timing entries. Generic compatibility does not establish qualification with every PHY.

The first release excludes patrol scrubbing, optional RFM extensions, and deep power-down. It retains inline/link ECC, correction-triggered scrub, normal refresh, normal power-down, self-refresh, DVFS, and any refresh management required by supported device/operating configurations. JESD209-5C makes RFM conditional on device capabilities and refresh rate; optional deferral cannot disable mandatory protection. The agreed burst default is 64 bytes, retaining 32-byte support.

PHY design and PHY training algorithms remain outside the supplied controller scope; controller-side handshakes and training hooks are described in the drafts. The supplied JESD209-5C has been reviewed in selected sections, revealing corrections to command duration/pairing, split BL32 transfers, WCK sync, refresh and link ECC. A targeted review of the supplied DFI 5.0 copy adds corrections to CA packing, timing units, per-slice data completion, boot ownership, updates and frequency changes. The review records the evidence and remaining `[VERIFY]` items, source inconsistencies and integration gaps. Use this material to refine the implementation agreement before starting RTL.
