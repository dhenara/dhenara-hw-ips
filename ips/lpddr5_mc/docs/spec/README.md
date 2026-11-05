# LPDDR5 specification index

These two files are the customer-supplied draft references imported on 2026-11-05. They are preserved byte-for-byte, including their original formatting and `[VERIFY]` markers.

| Document | Role | Status |
| --- | --- | --- |
| [Microarchitecture specification](lpddr5_mc_microarch_spec.md) | Base controller scope, interfaces, architecture, and verification considerations | Draft v0.1 |
| [Microarchitecture addendum](lpddr5_mc_microarch_addendum_v0.2.md) | Scheduler pipeline, DFI timing model, and proposed CSR map | Draft v0.2; extends the base document |

The base specification was supplied twice with identical contents; only one copy is tracked. The addendum is not a complete replacement specification. Differences and proposed corrections are tracked in the [review](review.md), which also records subsequent owner decisions. Neither imported file has been silently rewritten to resolve those differences.

## Import integrity

[SHA256SUMS](SHA256SUMS) records the original SHA-256 digests. From this directory, verify the retained originals with:

```sh
sha256sum -c SHA256SUMS
```

When a consolidated specification is agreed, identify its version, superseded documents, and resolved decisions here. Keep the original references available in Git history and distinguish normative requirements from implementation proposals.

## External references

The owner supplied JESD209-5C (June 2023) on 2026-11-05 as `scratchpad/uploads/cd6d9f71-884f-4f5f-a8c5-2383b8c6cede/JESD209-5C.pdf`, SHA-256 `76457d4019dc40c57b550c10dc3416cfae4d6ea1984de8f5c42e3e34d00ed031`. It is retained as an external reference and is not copied into this repository. The [review](review.md#supplied-jedec-reference-and-review-limits) records inspected sections, corrections, and the limits of that targeted review. LPDDR5X is not added to scope.

DFI 5.0 remains unavailable. Its exact command/data representation, timing and controller/PHY responsibilities must be checked before freezing interfaces. Relevant AXI/APB references, the complete supported configuration/timing matrix, and independent verification models also remain to be qualified. The supplied JEDEC PDF does not establish controller compliance or a PHY integration contract.
