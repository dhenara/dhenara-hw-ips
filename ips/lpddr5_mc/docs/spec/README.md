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

The [controller contracts](contracts.md) and their linked supported profiles, interfaces, host/ECC, lifecycle and performance pages are the reviewed implementation basis. Combined host/control, channel and ECC RTL is implemented and simulation-verified at accepted source `ba8f5142fd9f76809a78badfd2a7c06736e2f187`. The [initial release evidence](../verification/releases/initial/README.md) records the completed exact-final reproduction and review. Use the current contract pages for resolved decisions and requirements; the imported drafts and historical review remain byte-for-byte preserved as inputs, including their original open items.

For integration, follow the [RTL block diagram and module map](../architecture/implementation.md#4-top-level-organization), then the [testbench diagram and test plan](../verification/system.md#controller-testbench-and-test-plan). They show where host requests enter, where memory commands leave, and which checks observe both boundaries.

## External references

The owner supplied JESD209-5C (June 2023) on 2026-11-05 as `scratchpad/uploads/cd6d9f71-884f-4f5f-a8c5-2383b8c6cede/JESD209-5C.pdf`, SHA-256 `76457d4019dc40c57b550c10dc3416cfae4d6ea1984de8f5c42e3e34d00ed031`. It is retained as an external reference and is not copied into this repository. The [review](review.md#supplied-jedec-reference-and-review-limits) records inspected sections, corrections, and the limits of that targeted review. LPDDR5X is not added to scope.

A DFI 5.0 reference dated April 27, 2018 was supplied as `scratchpad/uploads/ea823e67-d6fb-47f7-841a-3bc10b90b924/774634393-DDR-PHY-Interface-Specification-v5-0.pdf`, SHA-256 `ffe2e764fce70dae4ec8e82ccdcb762acb5e3ea0cb3fe776eaf3fd0df56a84ec`, with a companion `.txt` file. The [DFI review](review.md#supplied-dfi-reference-and-review-limits) records the inspected command/data and ownership rules, the clocking conflict with our draft, and incomplete or contradictory entries in the supplied copy. It remains external to Git. The selected [engineering disposition](review.md#engineering-disposition-of-dfi-source-gaps) uses an early update-acknowledgement window and explicit, validated WCK profiles; it does not invent publisher errata or universal timing constants. The current contract pages record numeric digital profile qualification, AXI/APB references and the restricted supported configuration/timing matrix. Vendor-PHY integration requires separate qualification. The supplied JEDEC PDF does not establish controller compliance or a PHY integration contract.
