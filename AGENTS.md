# Working on hardware IP

Dhenara Inc. builds an engineering platform and hardware design tools. It publishes reusable open-source hardware IP in this repository. Each block includes its design sources, contributor instructions and verification evidence. [Dhenara](https://dhenara.com) · [Public hardware IP repository](https://github.com/dhenara/dhenara-hw-ips)

Start with [Contributing](CONTRIBUTING.md) and the [IP repository layout](docs/development/repository-layout.md). To add a block, create `ips/<lower_snake_case_name>/`, add it to the catalog in the root README and document its interfaces, configurations, dependencies and check commands in its own README.

Keep each block's RTL, verification and configuration together. Use the owning IP's source manifests and run its documented checks from the repository root. For LPDDR5, start with the [controller guide](ips/lpddr5_mc/README.md).

For a change in behavior, include a focused check and record the revision, configuration, command, tool version, seed when applicable, and result. Preserve source provenance, redistribution rights and license notices for contributed material.

Everything contributed here is intended for public release. Before sharing a change, check its source, documentation, fixtures and logs for confidential designs, customer data, credentials and private repository references. Use public prerequisites and redistributable inputs so another contributor can reproduce the work from a public clone.
