# Reproducing the contract experiments

This flow checks the available reference models and draft type representation. It does not instantiate a controller, qualify a vendor PHY or close the incomplete [implementation contracts](../spec/contracts.md).

## Tools and setup

The exercised environment is Linux amd64, Debian 12 userspace, Python 3.13.18, GNU make and g++ 12.2.0. The flow pins Debian's Verilator 5.006-3 binary package, SHA-256 `97239c73375f4e3c6d5e04afd7afdcae133e1b25bf0d876f81af04f09961c6bf`. This older distro version was selected for this Debian environment and the limited package/type smoke; it is not asserted to be the newest Verilator. Its compatibility must be re-evaluated for the later production regression and required language features. No proprietary simulator or host Docker capability is needed.

Run from the repository root:

```sh
export DHENARA_AGENT_TOOL_STORAGE_ROOT=/absolute/path/to/disposable-tools
make -f ips/lpddr5_mc/Makefile prepare
make -f ips/lpddr5_mc/Makefile check REPORT=/absolute/path/to/evidence/contracts.json
```

Preparation uses Python's standard library, HTTPS access to the recorded Debian package, and `dpkg-deb` to extract it without privilege. It verifies the package checksum before extraction. The check resolves the extracted simulator explicitly and rejects a different version. Compiler output and bytecode remain beneath the supplied tool-storage directory. `REPORT` must be an explicit evidence path; use a separate location for each retained run. Preparation also installs the pinned Ruff 0.17.4 wheel with pip into tool storage for Python lint and formatting; the behavioral tests themselves use only the standard library. PDF inspection tools were used during source review but are not test dependencies.

Run `make -f ips/lpddr5_mc/Makefile format` for local Python lint fixes and formatting. The owning check is non-mutating for authored source and checks Ruff lint/format separately. It checks relative Markdown file links (not anchors or authenticated external links), all three accepted-document hashes, retained shaping ancestry and `git diff --check`; runs the focused Python tests; compiles the explicit SystemVerilog list with assertions and warnings as errors; runs its representation smoke; and writes a JSON report only after all steps succeed. It refuses to overwrite an existing report so that a failed rerun cannot be mistaken for an earlier success. A failed command propagates failure. CI runs the same entry point; CI execution itself has not been observed in this environment.

## Behavior and sensitivity

| Test family | Positive evidence | Deliberate failures |
| --- | --- | --- |
| WCK | Three numeric row cases, paired reads/writes, BL16/32, ideal half/full-rate edges | Every principal event moved early/late by half a WCK, altered path delay, missing/extra edges, short postamble, premature disable, missing/invalid offsets, unsupported profiles |
| Update | Ignored opportunity, legal early acknowledgement and earliest/latest registered releases | Acknowledgement at/after minimum, unsolicited/stuck/reasserted acknowledgement, completion beyond release margin, undrained/insufficient-refresh admission |
| Link ECC | Each of 137 data and 22 DMI single-bit locations corrected; all 9,547 two-bit pairs detected; 128 seeded payloads per code; source column anchors and parity beat placement | Corrupted payload/check columns, including check-bit errors; no claim about triple errors |
| Read completion | Two interleaved BL32 identities, words/slices, two/four actual word lanes, cyclic wrap, skew, gaps, DNV and explicit pointer reset | Wrong/noncontiguous word rotation, early/late/missing/extra valid, duplicate enable metadata, repeated snapshots and resynchronization before drain |
| DVFS handshake | Sampled low-then-high acknowledgement, unchanged-high decline and stable target settings | Boundary/late acknowledgement, completion timeout/early high, settings changed, premature clock change/release, request reassertion and WCK enabled at transfer |
| Schedule | Single-group and interleaved BG row-hit occupancy | Illegal same-group spacing, duplicate identities, cross-group pin collision |
| Representation | Full 16-bit IDs, 40-bit addresses, 64-bit tags, 16-bit ages, CA edge packing, max granule data/strobes and word/slice dimensions | Compile/lint/assertion failures stop the flow; this is not a protocol simulation |

The random seed is 2095. The ECC payload seed is deterministic, and the location sweep is exhaustive for one- and two-bit faults in each single codeword. The independently supplied matrix is shared reference data; the checker is not copied from production RTL because no production algorithm exists. This does not replace later vectors against the actual encoder/decoder and PHY transport.

## Evidence interpretation and next checks

Reports identify the source commit, dirty-worktree status, source-file hashes, initial/shaping ancestry, accepted-document digests, tool versions, executed commands and durations, seed and explicit qualification limits. A dirty development report is iteration evidence only. The final review must use the committed source and a clean reproduction. The build cache is reusable disposable output, not evidence. Retain the command log and final report with the submitted source.

The next qualification must add the fast-sync/reuse/rank/ownership WCK matrix; full command/data-path timing and enable generation; host and APB/CDC semantics; inline/link ECC transaction lifetimes; mandatory refresh/RFM/thermal behavior; boot/PM/DVFS failure and recovery; complete register/configuration generation; and a periodic legal schedule including maintenance and feedback. Coverage of these items is currently absent, not implicitly passed.
