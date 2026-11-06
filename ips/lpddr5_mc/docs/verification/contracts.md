# Contract qualification and reproduction

The owning flow qualifies canonical source, digital PHY fixtures, transaction/lifecycle reference models and independent schedule feasibility. It compiles interface types and generated software constants. It does not simulate a production controller, qualify a vendor PHY or prove real CDC/electrical behavior. The [contract index](../spec/contracts.md) and [benchmark](../spec/performance.md) define those boundaries.

## Tools and commands

The exercised environment is Linux amd64, Debian 12 userspace, Python 3.13.18, GNU make, gcc/g++ 12.2.0 and pinned Verilator 5.006-3. The Debian package SHA-256 is `97239c73375f4e3c6d5e04afd7afdcae133e1b25bf0d876f81af04f09961c6bf`. This distro version is qualified for the present package/assertion smoke; production regression language/model requirements need their own qualification. Ruff 0.17.4 is pinned with its wheel hash in requirements-dev.txt. No proprietary simulator, licensed model or Docker daemon is needed.

From a clean repository with its full Git history:

```sh
export DHENARA_AGENT_TOOL_STORAGE_ROOT=/absolute/path/to/disposable-tools
make -f ips/lpddr5_mc/Makefile prepare
make -f ips/lpddr5_mc/Makefile check REPORT=/absolute/path/to/evidence/contracts.json
```

Preparation downloads/checks/extracts the pinned Debian binary without privilege and installs Ruff into tool storage. It requires HTTPS, dpkg-deb, pip, make and a compatible C++ compiler. The check explicitly resolves these installed tools, rejects different Python/Ruff/Verilator versions, puts compilation/bytecode/cache output in tool storage and preserves reports in the explicit evidence destination. It refuses to overwrite reports. A failed subprocess propagates failure; only a completely passing flow writes the final report.

Use `make -f ips/lpddr5_mc/Makefile format` for Python format/lint fixes. Generate artifacts with `make -f ips/lpddr5_mc/Makefile generate`; the check uses `--check` and never rewrites source. CI invokes the same prepare/check entry point. Current reports describe commands actually executed; repository CI configuration alone is not evidence of a remote CI run.

The check verifies initial/shaping/predecessor ancestry, all three accepted-document hashes, relative Markdown file links, whitespace and generated products. It runs the Python suite and numeric qualification, compiles/runs a C11 header consumer with warnings as errors, and compiles/runs the explicit SV source list with assertions and warnings as errors. The generated register API intentionally waives only UNUSEDPARAM inside that generated package because consumers use subsets of its constants; width/typing/assertion warnings remain failures. Missing tests never count as passes.

## Behavior and fault sensitivity

| Family | Positive cases | Deliberate failures / limitations |
| --- | --- | --- |
| PHY profiles |32 combinations; both directions, BL16/32, both ranks, paired/standalone sync, complete WCK/data/CS paths | Missing/overflowing fields, incompatible capabilities/MRs, early/late paths, missing edges, split/CS identity, short postamble/disable; ideal digital phase only |
| Temporal completion | Same-rank reuse/expiry, fresh rank/owner restart, two/four-word rotation, skew/gaps, DNV, profile-derived enables and actual returned words | Premature half/slice retirement, duplicate/missing/early/late valid, wrong rotation, stale reset; no real PHY |
| CA and masking | Literal Table201 anchors, ACT row/bank and access operands, MRW fixed opcode, MWR16 and tCCDMW | Invalid MWR32, column alignment, bank/row bounds, pair deadline/collision; no command serializer RTL |
| Link/inline ECC | All single/double matrix faults, seeded payloads, full x16 groups, DBI/mask transforms, SECDED72, RMW/scrub/version/cache/epochs | Corrupted parity/data/DMI, reserved identities, poisoned partial physical writes, stale scrub, premature poll/ack; no triple-error guarantee |
| Host | Full burst forms, narrow/unaligned/strobes, same-ID and cross-port hazards, exclusives, errors and complete fatal settlement | AW-before-W hazard, W order/count errors, 4KB/overflow, unsolicited response/early recovery; transaction-level reference |
| APB/CDC protocol | Strobes, reset/locks, shared/broadcast commands, atomic all-channel commit, hardware status snapshots | Invalid access, stale/partial ack, overflow without partial side effect, stale image; metastability/reset timing requires RTL checks |
| Lifecycle | Boot/training epochs, thermal intervals/derating, REFpb, mandatory RFM, extra SRX refresh, long SR, update/PM/FSP/master contracts | Late/stuck/ignored handshakes, false write drain, refresh budget/retention loss, stale high DVFS completion, unknown owner/state |
| Scheduling/performance | Ages/classes/weights/replay, byte caps, fairness including trace tail; periodic reads/writes for CK/CK2/CK/4 selection | Stale/duplicate picks, empty direction, cap overshoot, starvation, missing REF/MR4/ZQ, early PRE/OFF, split collisions, row wrap |
| Representation | Maximum IDs/addresses/data/strobes, raw parity/WCK phases, DFI ratio conversion, generated C/SV constants and invalid parameters | Compile/lint/assertion failure; no functional controller test |

`dv/tests/test_corrections.py` retains owning regressions for the predecessor review: posted responses before/at/after pin completion, distinct eligibility/BVALID/handshake states, MR43 faults and ownership loss with response backpressure; complete destructive refresh-fault recovery with fresh epochs and retained diagnostics; and zero-gap CS admission, continuous split holds, explicit deassertion and rank transitions. Unsupported nonzero CS gaps fail at admission and canonical register publication. These checks supplement the original families and exercise their failure/recovery paths without instantiating controller RTL.

The deterministic random seed is 2095. Source matrices are shared JEDEC-derived reference data, while syndrome checking and waveform/resource oracles are independent of future production RTL. Some earlier focused experiments remain under `config/reference/`; they keep their narrower docstrings and invalid-traffic guard. Ordinary integration uses `config/contracts/profiles.json`, full pin qualification and atomic activation instead of those legacy experiment inputs.

## Reports and clean reproduction

`contracts.json` records the exact commit, dirty flag, source-file SHA-256s, tools, commands/durations, seed, ancestry and accepted-document digests. Its adjacent `contracts-qualification.json` records all profile names/counts, profile-source hash, representative half-WCK traces (including CS deassertion), rank-transition case counts, canonical configuration, selected benchmark profile and six periodic schedule reports. Test output lists every executed test. These are model/representation results; controller implementation and vendor qualification are explicitly false.

A development report from a dirty tree is iteration evidence only. Submit source only after committing it and rerunning the whole flow clean. Reproduce that commit in an independent local clone with complete ancestry and an isolated build directory (`BUILD_SUBDIR=clean-reproduction`). Retain both runs' reports and command logs; compare committed source identities and measured schedules. A clean clone using the same installed pinned tools proves source reproducibility in that environment, not independent host/tool installation. Remote CI and a fresh host remain distinct evidence.

Candidate evidence is retained under the ignored `ips/lpddr5_mc/evidence/` area. It is not compiler cache or deliverable source. Later RTL contributors must add owning bus/channel/CDC tests and run these present checks unchanged; final qualification runs the full combined supported matrix, fault recovery and actual RTL performance criteria.
