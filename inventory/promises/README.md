# What was promised, and what is here

`promises.json` is the machine-readable form of this page. A test fails if a
file named there does not exist.

**Reading the status column.** `present` means the named material is in this
repository as files. It does not mean released, licensed, independently
verified or runnable. `partial` and `missing` say what is absent in the
"not here" column.

## Where the promises come from

- **The paper.** The Availability paragraph of the version submitted for
  review names six things, "retained internally and planned for an anonymized
  release with the benchmark artifact upon publication": the mutation probes
  and their finding ledgers; the per-item adjudication records behind the
  ledger table; the constraint-addition ledger with its per-commit machine
  sums; the predicate-audit ledger and per-predicate regression fixtures; the
  per-record rescore ledgers; the preservation-annex records.
- **The decision.** The acceptance decision asked the authors to "provide
  inspectable examples and the promised audit artifacts, identify executable
  scorer versions".

## Paper

| | Promise | Status | Here | Not here |
|---|---|---|---|---|
| P1 | the mutation probes | **partial** | both probe scripts as source, at the first commit that ran each and at the development pin (`sources/probes/`) | they cannot be run: they iterate over the 640 feasible scenarios of a corpus that is not held. Three findings replay as worked cases |
| P2 | their finding ledgers | **partial** | every tracked version of the two probe ledgers with its dev-split findings (`sources/ledgers/probe_round1/`, `probe_round2/`) and the reconciled table of the 139 dev-split candidate sites (`sites/`) | the test-split findings and sites (388 of 527); the 13 ledgers regenerated in 2026-10. `sites/recovery_168.csv` accounts for the 36 dev-split blocks recorded at site-key grain only; it does not say those repairs were verified |
| P3 | per-item adjudication records | **partial** | triage, closeout and two hardening ledgers with their dev-split items (`sources/ledgers/adjudication/`), and the dev-split per-site labels and reasons (`sites/candidate_keys.csv`) | the test-split items; human validation or a second rater. The labels and reasons were generated in a coding-agent workflow. Notes are partly in Korean |
| P4 | constraint-addition ledger | **partial** | the 2026-08 ledger and the 2026-10 per-scenario history with its dev-split examples (`checks/constraint_history.json`) | the test-split examples; the history cannot be recomputed without every scenario file at every commit |
| P5 | predicate-audit ledger and regression fixtures | present | the audit ledger, its scenario lists reduced to the dev split; 69 fixture cases, the meta-test and the fail-closed tests, runnable here (`fixtures/`) | the test-split scenario lists; the fixtures exercise the scorer of the development pin only |
| P6 | per-record rescore ledgers | **partial** | two 2026-08 ledgers and the 2026-10 tables with one stored verdict per record and run, for the 29,400 dev-split records (`rejudging/`) | the test-split records (88,200 of 117,600); the records. `tables` re-counts stored verdicts. Judging again needs about 1.7 GB of run logs and the analysis code, which are not included |
| P7 | preservation-annex records | present | the 2026-08 record | nothing here re-derives it |

## Decision

| | Request | Status | Here | Not here |
|---|---|---|---|---|
| D1 | inspectable examples | **partial** | five worked cases with states, steps, constraints before and after and per-check verdicts (`cases/`) | the fifth stage of two probe cases: its scenario was assembled for the test and is in no commit. Five cases are examples, not a sample |
| D2 | identify executable scorer versions | present | three scorer files with commit and hash, the swapped-module diffs, and the surrounding modules of five commits as git blobs (`scorers/`, `runtime/`) | a lock file (the benchmark tracks none). The module sets are the import closure of the commands here, not the benchmark |

## Not promised, listed so that nobody assumes it

| | Thing | Status | Why |
|---|---|---|---|
| X1 | recomputing corpus fingerprints | **partial** | definitions and expected hashes are in `expected.json`; no corpus is held |
| X2 | re-judging all stored records | **missing** | needs the run logs, the analysis code and the full source trees, which are not included. Not a gate of this repository |
| X3 | independent verification of a label or number | **missing** | the labels were generated in a coding-agent workflow, and the runs were made on a Someple Inc. server through a coding agent; nothing was reproduced independently. No agent was rerun |

## Material that is not exported here

No item of the 600 test-split scenarios is exported. The corpus-defect audit
and the corpus-identity record of 2026-08 are not named in the Availability
paragraph and are not exported. The raw run logs, the analysis code and its
tests, the full source trees and the corpora are not exported.
