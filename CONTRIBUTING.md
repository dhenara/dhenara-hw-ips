# Contributing

Use [the repository layout](docs/development/repository-layout.md) when adding an IP. Keep each contribution focused on its owning IP and update the documentation that describes its behavior. Shared components need explicit dependencies and their own verification; a common directory is not a substitute for ownership.

## Starting an IP

Create `ips/<lower_snake_case_name>/` and add it to the root IP catalog. Its README should identify its purpose, current maturity, interfaces, supported configurations, dependencies, and available build and verification commands. Create implementation subdirectories as needed; a placeholder directory does not establish implemented capability.

Keep imported specifications versioned and distinguish drafts, reviewed requirements, implemented behavior, and verified results. For LPDDR5, preserve the two supplied originals listed in its [specification index](ips/lpddr5_mc/docs/spec/README.md); use its linked reviewed contracts as the implementation basis and keep the historical review intact.

## Source and checks

Use IP-prefixed module and package names, explicit source lists, and reproducible configuration. Keep testbench code out of synthesizable source lists. Put register definitions in one canonical source once a register-generation tool is selected, and document how generated RTL, software headers, and documentation are reproduced.

Changes to behavior should include focused checks in the owning simulation or formal environment, including relevant failure cases. Record the source commit, configuration, tool versions, command, seed where applicable, and result so another engineer can reproduce the evidence. A compile or lint pass alone does not establish functional correctness or timing closure.

For documentation-only changes, check relative links, `git diff --check`, and any source-document digests. The LPDDR5 import can be checked from the repository root with:

```sh
(cd ips/lpddr5_mc/docs/spec && sha256sum -c SHA256SUMS)
git diff --check
```

LPDDR5 has a scoped [reference-contract check flow](ips/lpddr5_mc/docs/verification/contracts.md), including a pinned simulator recipe and CI entry point, plus [combined controller checks](ips/lpddr5_mc/docs/verification/system.md). The [qualification ledger](ips/lpddr5_mc/docs/verification/qualification.md) preserves the executed evidence and development history; the [final verification report](ips/lpddr5_mc/docs/verification/releases/initial/README.md) records the accepted release status. No repository-wide EDA release flow or formatter convention is established. Keep proprietary tool installations, licensed external models, generated outputs, and credentials out of source commits; record authorized external dependencies and their versions instead.

## License

Unless explicitly stated otherwise, contributions intentionally submitted for inclusion in this project are provided under the [Apache License 2.0](LICENSE). Preserve applicable third-party notices and include only material you have the right to contribute.
