# LPDDR5 memory controller

Technology-independent AXI4-to-DFI controller IP under integration and simulation qualification. The source includes host/control and channel RTL, inline/link ECC, canonical interfaces/register generation, 32 numeric generic-PHY fixtures, independent protocol/data/integrity/lifecycle models and periodic bandwidth schedules. The combined RTL has focused integration evidence. Six reviewed CK/CK2/CK4 read/write throughput results bind the earlier hardware revision; the current host forwarding/merging correction has focused core and asynchronous-assembly evidence. Its combined channel/ECC applicability and final qualification remain incomplete. No synthesis or vendor-PHY qualification is claimed.

## Start here

- [Contract basis and implementation status](docs/spec/contracts.md)
- [Supported profiles and source traceability](docs/spec/profiles.md)
- [Canonical interfaces](docs/spec/interfaces.md)
- [AXI ordering and ECC](docs/spec/host-ecc.md)
- [Configuration, lifecycle and recovery](docs/spec/control.md)
- [Performance and fairness criteria](docs/spec/performance.md)
- [Generated register/programming reference](docs/programming/registers.md)
- [Reproduction and evidence limits](docs/verification/contracts.md)
- [Implemented microarchitecture](docs/architecture/implementation.md)
- [Current qualification matrix and remaining checks](docs/verification/qualification.md)
- [Clock architecture](docs/architecture/clocking.md)
- [Host/control implementation and clocks](docs/architecture/host-control.md)
- [Channel implementation](docs/architecture/channel.md)
- [Host/control owning checks](docs/verification/host-control.md)
- [Independent system checks and binding limits](docs/verification/system.md)
- [Preserved specifications and review](docs/spec/README.md)

The contract requires one/two AXI ports, one/two independent x16 channels, one/two identical 8 Gb ranks, 64/128/256-bit host data, 32/64-byte granules and bounded queues. Numeric digital fixtures keep CK/DFI at 800 MHz with WCK:CK=4 (6400 BG) or 2 (3200 16B). Link ECC requires a documented raw parity PHY extension at ratio four; inline ECC has explicit reserved-capacity/RMW/scrub rules. Missing profiles or lost ownership prevent traffic. The verification pages distinguish checked configurations from the full required matrix.

The default two 128-bit AXI400 ports offer 12.8 GB/s combined in one direction. Reviewed measurements at hardware baseline `98e8ca4` meet the agreed threshold for separate reads and writes at CK/CK2/CK4 selection, including rows, refresh, MR4 and ZQ. Reads measure 12.8 GB/s; writes measure approximately 12.79 GB/s. The CK/4 reference target remains 11.5712 GB/s. These bounded results use two channels, one rank/channel, plain data and the exact workload in the [qualification matrix](docs/verification/qualification.md#throughput-and-fairness); other configurations and release readiness require their separate evidence. The engine runs at CK with slower selection enables and immediate pair completion. These measurements have not been rerun on the changed host core; the source-impact review must establish their applicability before a current combined claim.

Run the reference contracts or the combined owning and smoke flow from the repository root. Choose new evidence and build names for each run:

```sh
export DHENARA_AGENT_TOOL_STORAGE_ROOT=/absolute/path/to/disposable-tools
make -f ips/lpddr5_mc/Makefile prepare
make -f ips/lpddr5_mc/Makefile check REPORT=/absolute/path/to/evidence/contracts.json
make -f ips/lpddr5_mc/Makefile check-controller \
  EVIDENCE="$PWD/ips/lpddr5_mc/evidence/controller-run" BUILD_SUBDIR=controller-run
```

`check-controller` runs the host aggregate (including the foundation), channel/REFpb checks, independent system fixtures and representative connected tests. `SUITE=owning` or `SUITE=connected` selects a partial suite; only the default `SUITE=all` runs both. Add `CHECK_ARGS=--require-clean` for committed-source reproduction. The [system guide](docs/verification/system.md) describes evidence and remaining qualification limits. The GitHub workflow invokes the same full command; configuring it does not establish a remote CI result.

The first release retains normal refresh/ZQ/thermal handling, mandatory RFM, inline/link ECC, correction-triggered scrub, power-down/self-refresh and frequency/FSP management. PHY circuitry/training algorithms, patrol scrub, optional RFM extensions, deep power-down, LPDDR5X and technology-specific sign-off are excluded. The reviewed contract foundation is the implementation basis; model and isolated-unit results do not establish final combined-controller qualification.
