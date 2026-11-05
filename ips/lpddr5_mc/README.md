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

The owner confirmed a generic PHY-facing design, technology-independent implementation with power, performance, and area as optimization goals, and simulation-verified RTL as the first delivery. At DFI 1:2, the DFI payload bandwidth must match the combined bandwidth of the two AXI ports in a declared feasible configuration; reduced throughput at DFI 1:4 is acceptable. The review records the measurement proposal and remaining protocol prerequisites. The draft's DFI 5.0 target still requires checking against the authoritative standard; generic compatibility does not establish qualification with every PHY.

The first release excludes patrol scrubbing and deep power-down. It retains inline/link ECC, correction-triggered scrub, normal refresh, normal power-down, self-refresh, DVFS, and RFM, with standard-dependent behavior still to be validated. The agreed burst default is 64 bytes, retaining 32-byte support.

PHY design and PHY training algorithms remain outside the supplied controller scope; controller-side handshakes and training hooks are described in the drafts. All `[VERIFY]` markers and unresolved review items remain open. Use this material to refine the implementation agreement before starting RTL.
