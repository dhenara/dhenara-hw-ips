# LPDDR5 memory controller

Technology-independent AXI4-to-DFI controller IP with a proposed, executable contract foundation. The source includes canonical interfaces/register generation,32 numeric generic-PHY fixtures, independent protocol/data/integrity/lifecycle models and periodic bandwidth schedules. Production controller RTL, complete multi-clock regression, synthesis and vendor-PHY qualification remain pending.

## Start here

- [Contract status and proposed corrected agreement](docs/spec/contracts.md)
- [Supported profiles and source traceability](docs/spec/profiles.md)
- [Canonical interfaces](docs/spec/interfaces.md)
- [AXI ordering and ECC](docs/spec/host-ecc.md)
- [Configuration, lifecycle and recovery](docs/spec/control.md)
- [Performance and fairness criteria](docs/spec/performance.md)
- [Generated register/programming reference](docs/programming/registers.md)
- [Reproduction and evidence limits](docs/verification/contracts.md)
- [Clock architecture](docs/architecture/clocking.md)
- [Preserved specifications and review](docs/spec/README.md)

The proposed matrix is one/two AXI ports, one/two independent x16 channels, one/two identical 8Gb ranks, 64/128/256-bit host data, 32/64-byte granules and bounded queues. Numeric digital fixtures keep CK/DFI at 800MHz with WCK:CK/4 (6400 BG) or2 (3200 16B). Link ECC requires a documented raw parity PHY extension at ratio 4; inline ECC has explicit reserved-capacity/RMW/scrub rules. Missing profiles or lost ownership prevent traffic.

The default two 128-bit AXI400 ports offer 12.8GB/s combined in one direction. A periodic model schedule demonstrates that target including rows, refresh, MR4 and ZQ; CK/4 selection demonstrates 11.5712GB/s. These are schedule-feasibility results, not measured controller throughput. The recommended engine runs at CK with slower selection enables and immediate pair completion.

Run the owning flow from the repository root:

```sh
export DHENARA_AGENT_TOOL_STORAGE_ROOT=/absolute/path/to/disposable-tools
make -f ips/lpddr5_mc/Makefile prepare
make -f ips/lpddr5_mc/Makefile check REPORT=/absolute/path/to/evidence/contracts.json
```

The first release retains normal refresh/ZQ/thermal handling, mandatory RFM, inline/link ECC, correction-triggered scrub, power-down/self-refresh and frequency/FSP management. PHY circuitry/training algorithms, patrol scrub, optional RFM extensions, deep power-down, LPDDR5X and technology-specific sign-off are excluded. The lead must review this foundation and the owner must accept the corrected agreement before dependent RTL release.
