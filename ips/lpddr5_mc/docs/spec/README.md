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

## External references still needed

The drafts cite JESD209-5 and DFI 5.0, but the uploads do not include those standards, target-device timing data, or a PHY integration contract. They have not been checked in this review. Exact standard revisions and authorized reference/model access must be established before freezing command encoding, timing, WCK behavior, training handshakes, or compliance claims.
