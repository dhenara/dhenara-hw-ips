# Hardware IP repository conventions

This page defines the initial repository organization for maintainers adding independent hardware IPs. LPDDR5 has canonical contracts, generated registers, numeric digital fixtures and independent reference checks; controller implementation remains pending.

## Organize by IP

Each `ips/<ip_name>/` directory owns the source, tests, constraints, and documentation needed to understand and verify that IP. Avoid repository-wide `rtl/` and `tests/` trees that mix unrelated blocks. Use lower snake case for IP and directory names, and prefix public SystemVerilog modules and packages with the IP name to avoid integration collisions.

| Location within an IP | Responsibility |
| --- | --- |
| `README.md` | Integration entry point, maturity, supported configurations, dependencies, and commands |
| `docs/spec/` | Specifications, provenance, and specification review decisions |
| `docs/` | Architecture, verification plan, programming guide, and integration guidance as they are authored |
| `rtl/` | Synthesizable modules, packages, and interfaces |
| `dv/` | Simulation testbenches, agents, environments, models, scoreboards, assertions, and tests |
| `formal/` | Formal harnesses, properties, assumptions, and proof scripts |
| `constraints/` | Clock and reset definitions, timing constraints, and documented integration assumptions |
| `syn/` | Synthesis flow scripts and explicit source manifests |
| `sw/` | Software interface headers, drivers, and programming examples |
| `config/` | Canonical configuration, register-description inputs, and supported parameter sets |

The LPDDR5 scaffold contains these directories. `.gitkeep` files retain currently empty directories; they can be removed when real content is added. Create deeper divisions such as `dv/tb/`, `dv/env/`, `dv/tests/`, `dv/models/`, and `dv/assertions/` only when there is content to own.

## Shared code and integration

Treat reusable synthesizable blocks as separate IPs under `ips/`, with explicit dependencies, documented interfaces, and tests. A consumer's source manifest must name its dependencies and supported configuration; do not depend on a recursive wildcard over every RTL file in the repository.

Add repository-level `tools/` for tools genuinely shared by several IPs and `verification/` for shared verification infrastructure when needed. Add `integrations/<system>/` for system wrappers, address maps, and tests that compose several IPs. These directories are conventions for future content and are not created by this scaffold. IP-specific tools and protocol behavior stay with their IP until reuse warrants a shared owner.

Keep technology-independent RTL separate from technology adapters and target constraints. Record technology-specific qualification separately; do not imply a portable design has met a physical timing, power, or area target without the corresponding flow and evidence.

## Reproducibility and artifacts

Pin external source revisions and tool versions when selecting a flow. Record licensed dependencies without copying them into this repository unless redistribution is authorized. Keep source manifests, constraint sources, register schemas, and build scripts in Git. Document the canonical input and regeneration command for every generated deliverable.

Write disposable build and simulation output to a configured output directory outside authored source. Use the execution environment's supplied tool-storage location when provided, or an ignored `build/` directory for a local flow. Archive selected reports with source revision, tool version, configuration, seed, and command in the team's evidence store. Do not commit compiler caches or waveform dumps as design source.

## Review and delivery

An IP's documented configuration matrix should distinguish supported combinations from experimental parameters. Review interface and register changes with their consumers, and update tests and integration instructions together. Before a reusable release, record the exact source revision, included dependencies, checked configurations, known limitations, and relevant verification and synthesis evidence. Repository presence, a draft specification, or an empty directory alone does not indicate release readiness.

Add actual maintainer teams and review rules when they are assigned; this scaffold does not invent owners or enforce branch protection. Return to the [repository entry point](../../README.md) for the current IP catalog.
