# Dhenara Hardware IPs

A repository for reusable hardware IP, with each IP's specification, RTL, verification, constraints, and software kept together under `ips/<ip_name>/`.

## IP catalog

| IP | Purpose | Current status |
| --- | --- | --- |
| [LPDDR5 memory controller](ips/lpddr5_mc/README.md) | AXI4-to-DFI memory controller targeting LPDDR5 | Combined RTL and independent simulation checks present; [controller qualification remains incomplete](ips/lpddr5_mc/docs/verification/qualification.md) |

## Repository layout

```text
docs/development/        Repository-wide engineering conventions
ips/<ip_name>/           Independently organized IP
  README.md              Status, interfaces, configurations, and entry points
  docs/                  Specifications and design documentation
  rtl/                   Synthesizable implementation
  dv/                    Simulation environment, models, assertions, and tests
  formal/                Formal properties, harnesses, and proof configuration
  constraints/           Clock, reset, timing, and integration constraints
  syn/                   Synthesis scripts and source manifests
  sw/                    Register definitions, drivers, and examples
  config/                Supported configurations and canonical input manifests
```

See [repository conventions](docs/development/repository-layout.md) for ownership boundaries, shared components, generated files, and the expected contents of an IP. Start with [Contributing](CONTRIBUTING.md) when adding or changing an IP.

The initial LPDDR5 documents are preserved as supplied. Read the [specification index](ips/lpddr5_mc/docs/spec/README.md) for the current contracts and historical review. The [contract check flow](ips/lpddr5_mc/docs/verification/contracts.md) checks models, interfaces and generated sources; the [system guide](ips/lpddr5_mc/docs/verification/system.md) describes checks against combined RTL. The [qualification ledger](ips/lpddr5_mc/docs/verification/qualification.md) records executed results, source applicability and remaining release checks.
