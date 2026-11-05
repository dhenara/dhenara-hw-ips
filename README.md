# Dhenara Hardware IPs

A repository for reusable hardware IP, with each IP's specification, RTL, verification, constraints, and software kept together under `ips/<ip_name>/`.

## IP catalog

| IP | Purpose | Current status |
| --- | --- | --- |
| [LPDDR5 memory controller](ips/lpddr5_mc/README.md) | AXI4-to-DFI memory controller targeting LPDDR5 | Draft specifications and repository scaffold; no RTL or verification implementation yet |

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

The initial LPDDR5 documents are preserved as supplied. Read the [specification index](ips/lpddr5_mc/docs/spec/README.md) and [review and scope decisions](ips/lpddr5_mc/docs/spec/review.md) before using them for design. No hardware build or regression command is available at this stage.
