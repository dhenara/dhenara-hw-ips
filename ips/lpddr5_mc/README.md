# LPDDR5 memory controller

Technology-independent AXI4-to-DFI controller IP with host/control and channel RTL, inline/link ECC, canonical interfaces/register generation, 32 numeric generic-PHY fixtures and independent protocol/data/integrity/lifecycle checks. Accepted source `ba8f5142fd9f76809a78badfd2a7c06736e2f187` passes the selected simulation release checks: all 22 exact-final reproduction steps and six final-checker replays of the earlier HDL benchmark simulations passed. Independent lead review also passed 394 integration tests on the exact accepted source. The [initial release evidence](docs/verification/releases/initial/README.md) records the completed review, results and supported scope.

## Start here

- [Initial release results and evidence](docs/verification/releases/initial/README.md)
- [Contract basis and implementation status](docs/spec/contracts.md)
- [Supported profiles and source traceability](docs/spec/profiles.md)
- [Canonical interfaces](docs/spec/interfaces.md)
- [AXI ordering and ECC](docs/spec/host-ecc.md)
- [Configuration, lifecycle and recovery](docs/spec/control.md)
- [Performance and fairness criteria](docs/spec/performance.md)
- [Generated register/programming reference](docs/programming/registers.md)
- [Reproduction and evidence limits](docs/verification/contracts.md)
- [Controller RTL block diagram and module responsibilities](docs/architecture/implementation.md#4-top-level-organization)
- [Qualification ledger and final checks](docs/verification/qualification.md)
- [Clock architecture](docs/architecture/clocking.md)
- [Host/control implementation and clocks](docs/architecture/host-control.md)
- [Channel implementation](docs/architecture/channel.md)
- [Host/control owning checks](docs/verification/host-control.md)
- [Controller testbench diagram and test plan](docs/verification/system.md#controller-testbench-and-test-plan)
- [Preserved specifications and review](docs/spec/README.md)

The contract requires one/two AXI ports, one/two independent x16 channels, one/two identical 8 Gb ranks, 64/128/256-bit host data, 32/64-byte granules and bounded queues. Numeric digital fixtures keep CK/DFI at 800 MHz with WCK:CK=4 (6400 BG) or 2 (3200 16B). Link ECC requires a documented raw parity PHY extension at ratio four; inline ECC has explicit reserved-capacity/RMW/scrub rules. Missing profiles or lost ownership prevent traffic. The verification pages distinguish checked configurations from the full required matrix.

The default two 128-bit AXI400 ports offer 12.8 GB/s combined in one direction. All six read/write CK/CK2/CK4 measurements at producer `2f22274` pass the agreed threshold with finite rows, refresh, MR4 and ZQ. Reads measure 12.8 GB/s; writes measure 12.7918–12.7936 GB/s. The CK/4 reference remains 11.5712 GB/s. Final-source applicability and complete replay with the accepted source's checker passed for all six. These results use two channels, one rank/channel, plain data and the exact workload in the [qualification ledger](docs/verification/qualification.md#throughput-and-fairness). The engine runs at CK with slower selection enables and immediate pair completion.

Run the reference contracts or the combined owning and smoke flow from the repository root:

```sh
export DHENARA_AGENT_TOOL_STORAGE_ROOT=/absolute/path/to/disposable-tools
make prepare
make -f ips/lpddr5_mc/Makefile check REPORT=/absolute/path/to/evidence/contracts.json
make regress ARTIFACT_DIR="$PWD/ips/lpddr5_mc/evidence/controller-run"
```

`make regress` runs the host aggregate (including the foundation), channel/REFpb checks, independent system fixtures and representative connected tests. `SUITE=owning` or `SUITE=connected` selects a partial suite; only the default `SUITE=all` runs both. Add `CHECK_ARGS=--require-clean` for committed-source reproduction. The [system guide](docs/verification/system.md) describes evidence and remaining qualification limits. The GitHub workflow invokes the same full command; configuring it does not establish a remote CI result.

The optional [Dhenara test manifest](tools/dhenara/hw-regress.yaml) groups the notification and coordinator tests into `control-smoke`. Each target compiles the shared [controller RTL file list](rtl/files.f) followed by its own [SystemVerilog bench](dv/integration/). The [repository instructions](../../README.md#run-the-checks) describe standalone and managed runs.

The first release includes normal refresh/ZQ/thermal handling, mandatory RFM, inline/link ECC, correction-triggered scrub, power-down/self-refresh and frequency/FSP management. PHY circuitry/training algorithms, patrol scrub, optional RFM extensions, deep power-down and LPDDR5X are excluded. Qualification covers the selected digital profiles and behavior families; it does not establish a full configuration cross-product, synthesis/PPA, physical CDC/timing, silicon, vendor-PHY integration or fresh-host qualification. The release evidence preserves the detailed limits and original failures alongside their corrections.
