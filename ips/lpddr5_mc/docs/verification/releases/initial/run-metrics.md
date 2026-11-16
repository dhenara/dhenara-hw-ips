# Recorded execution and model usage

These observations describe the LPDDR5 delivery and its subsequent public-source publication, as recorded on 16 November 2026. The accepted implementation is `ba8f5142fd9f76809a78badfd2a7c06736e2f187`; published merge `f324c093dba01debfc393744301e4d2fd18805aa` preserves the identical tree. Model usage does not establish hardware qualification; the final review and source-bound reports own that evidence.

## Scope and reconciliation

The canonical shared Mission spend pool includes the adopted setup/discussion Session, the Mission lead and its work performers. Its recorded total at 20:53:36 UTC was **US$1,172.143066**, displayed as **US$1,172.14**. The budget allowance was **US$1,500**, comprising the original US$1,000 plus a subsequent US$500 grant; it is a limit, not recorded usage.

| Work included | Recorded API-equivalent USD |
| --- | ---: |
| Initial Mission lead and performers | 1,099.839546 |
| Adopted setup/discussion Session | 34.016330 |
| Initial delivery, including the final closeout turn | **1,133.855876** |
| Subsequent public-repository publication turn | 38.287190 |
| Dashboard total through publication | **1,172.143066** |

Amounts are summed at recorded precision and then rounded for display. The final closeout turn settled at 13:05:19 UTC and belongs to the initial delivery even though completion had been recorded during that turn. A strict 13:03 cutoff would omit some of its usage. Later engineering beyond the publication snapshot is excluded.

## Models, harness and valuation

| Recorded model | Through initial delivery | Publication work | Through publication |
| --- | ---: | ---: | ---: |
| OpenAI GPT-6 Astra | 760.646940 | 38.287190 | 798.934130 |
| OpenAI GPT-6.1 SOL | 373.208936 | 0.000000 | 373.208936 |
| Total USD | **1,133.855876** | **38.287190** | **1,172.143066** |

The retained runtime catalog and available execution receipts identify Codex through its native app-server protocol. All priced records are subscription-included usage valued at the platform's API-equivalent rates. This is not an invoice, an allocation of a subscription fee, or proof of cash paid for this project. Infrastructure, human work and other operating costs are outside this figure.

The initial full scope contains 256 priced usage records across 258 attempts. The subsequent publication turn adds one priced record. Canonical usage records are counted once; parent totals are not added again, and API-equivalent valuation is not added to the same recorded cost as a separate expense.

## Accounting completeness

The canonical pool flags **17 attempts with incomplete or unpriced usage**, all from the initial work. Fifteen retain a positive known cost while marking usage unavailable; two have no known cost. Known amounts remain included. Therefore the figure is an exact sum of recorded values, not complete metering or a claim of exact total expenditure. Missing usage must not be silently treated as zero when making a total-cost claim.

## Execution time

The Mission dashboard through publication shows **3d 17h 54m** of recorded execution. It counts the union of agent and background-job running intervals, so parallel work counts once. It is neither summed worker-hours nor total calendar duration. The earlier initial-completion snapshot showed **3d 17h 19m** of execution within **10d 22h 35m** of total elapsed time; publication contributes later activity. The README uses the through-publication dashboard scope for both headline usage and execution.

The source of the cost reconciliation is a read-only extraction of the canonical Mission spend pool and usage records. The public tables omit operational identifiers, credentials and private transcript content. Retained original technical evidence remains separate from this presentation summary. No comparative cost or speed baseline was measured.
