# nle-bench-audit

Audit material for the paper

> **Red-Teaming the Grader: Mutation-Validated Constraint Checking for Tool-Agent Benchmarks**
> Junha Jang (Someple Inc.)
> Accepted as a poster at the NeurIPS 2026 Workshop "Who Verifies the Agents?" (non-archival).

This repository is the record of one audit of the grader of NLE-Bench, a
tool-agent benchmark for symbolic non-linear video editing. It is not a release
of the benchmark and has no tagged version; the paper cites its first commit.
The licence is in `LICENSE`; its terms are in `LICENSE.md`.

**Scope: the dev split only.** The benchmark's corpus has 800 scenarios: 200 in
the dev split and 600 in the test split. This repository holds no item of a
test-split scenario. The totals printed in the paper include both splits; only
their dev-split components can be recounted here.

## Quick start

Python 3.11 or later. `all` reads only this checkout.

```
python -m venv .venv
.venv/bin/pip install -r requirements-observed.txt
.venv/bin/python -m nlebench_audit all
.venv/bin/python -m pytest
```

`all` runs the first five commands below; `fingerprint` needs a corpus
directory, which is not included.

| Command | What it does |
|---|---|
| `verify` | checks every file against `MANIFEST.json` and the recorded hashes |
| `tables` | recounts the dev-split verdicts and candidate sites |
| `fixtures` | runs the 69 predicate test cases and their meta-tests |
| `cases` | replays three probe cases at the ten stages held here |
| `stored-cases` | judges two stored agent runs under the review snapshot's scorer file and the development pin's |
| `fingerprint --corpus DIR --name NAME` | fingerprints a scenario folder |

None of them runs an agent or a model. A pass means that this copy of the
files, run here, returns the recorded values. It is not an independent
verification. `requirements-observed.txt` lists the dependency versions of the
one environment the October 2026 runs used.

## What is here

| Group | Path | Content |
|---|---|---|
| Run | `nlebench_audit/`, `tests/` | the commands and their tests |
| Run | `fixtures/` | the predicate test cases |
| Evidence | `cases/` | five worked cases |
| Evidence | `sites/` | the 139 dev-split candidate sites |
| Evidence | `rejudging/` | the dev-split per-record verdicts |
| Evidence | `checks/` | null-agent, history, path and direction checks |
| Pinned inputs | `scorers/` | three scorer files and module diffs |
| Pinned inputs | `runtime/` | benchmark modules, per commit |
| Pinned inputs | `sources/` | probe scripts and dev-split ledgers |
| Records | `provenance/` | where each file came from, and what was changed |
| Records | `inventory/` | what the submitted version promised; known issues |
| Records | `MANIFEST.json`, `expected*.json` | file hashes; the recorded counts |
| Maintainer | `tools/` | export and packaging tools |

## The benchmark versions

The audit ran on two pinned versions of the benchmark:

| Pin | What it is | Commit |
|---|---|---|
| review snapshot | the version packaged for review | `58afb977` |
| development | the tree the audit ended on (not public) | `206e1838` |

The commits identify the versions (full hashes in `runtime/PINS.json`); the
modules that the commands load from them are held here under `runtime/pins/`. The retained files of the review snapshot
are in https://github.com/Junha-Jang/nle-bench-artifact. In the records, the
review snapshot is called `published`. Three more development commits appear
because a worked case is replayed on the code of the commit it was recorded at
(`runtime/PINS.json`).

The audited versions are work in development; known open problems of the
benchmark are listed in `inventory/issues/`. Nothing here measures how well an
agent performs.

## What the numbers are

They come from runs on stored records in October 2026, on a Someple Inc. server,
executed through a coding agent at the author's direction. No agent or model was
run again. The cause labels and written rationales in the historical adjudication
records were generated in a coding-agent workflow. The reported evidence does not
include human validation or an inter-rater agreement measurement.

What `tables` recounts for the dev split:

- **139 of the 527 candidate sites.** A candidate site is a place where a
  probe's mutated solution was still accepted, so 139 sites are not 139
  defects. Their recorded causes and dispositions are in `sites/candidate_keys.csv`.
- **29,400 of the 117,600 stored records**, with their verdicts in
  `rejudging/per_record.csv.gz`.
- **Four comparisons**, defined in `rejudging/rejudging.json`: the scorer-file
  comparison, the direction option, the implementation step and the corpus
  step. Their dev-split counts are in `expected.json`.

Some records are kept as recorded and report both splits together:
`rejudging/rejudging.json`, `rejudging/runs/` and `rejudging/rows.csv` from
October 2026, and the summary fields of the August 2026 ledgers and of
`sites/candidate_reconciliation.json`. They name no test-split scenario and cannot be recounted here. Two of
the probe cases hold four of their five stages; the fifth used a scenario built
for that test; its verdicts are recorded in `case.json` and cannot be replayed.

## What cannot be done here

- Recounting the test-split part of any total.
- Judging the stored records again. That needs about 1.7 GB of run logs and the
  analysis code, which are not included.
- Running the probes. They need the full corpus at each commit.
- Rebuilding the October 2026 outputs, or the null-agent and history checks under
  `checks/`.
- Comparing an exported file with its source, which is not included;
  `provenance/` records the hashes of both.

## How the files were prepared

`tools/export.py` exported the files from the audit's records. Besides keeping
only the dev split, it removed notes that name test-split items
(39 values in 7 files) and one summary note (`withheld_note`: 1 value in 1 file),
left out the messages of development commits, removed references to material
outside this record, replaced local paths and an account name with
placeholders, and reworded descriptions of the review snapshot to the paper's
term. Some retained dev-split items therefore omit notes, so the exported
reasons and limitations are not complete. Every change is registered and
counted in `provenance/TRANSFORMATIONS.json`, and `provenance/README.md` gives
the rules. The export also recounts `expected.json` for the dev split; apart
from the registered removals, replacements and this recount, measured values,
identifiers and verdicts are unchanged.

## Reading the records

- **ledger**: a JSON record written by an analysis script during the audit, in
  August 2026.
- **P1, P3, E2, E3, D1** and similar labels name steps of the October 2026
  analysis. "pre-P1" marks the scorer of development commit `6876b0dd`, before
  the August 2026 predicate repairs.
- **contract**: the declared names and parameters of the predicates.
- **run names** (`sub`, `pre`, `new`, `newm`, `d31s`, `d31p`, `d32`): defined in
  `rejudging/rejudging.json` under `runs`.

The reasons in the ledgers and tables are in Korean, as they were recorded, and
are not translated. Where a record says 저자 ("the author") or speaks in the
first person, that is the wording of the record; it does not establish who
carried out a step. The same holds for field names that contain "author", such
as `author_adjudicated_defect` and `author_rationale_2026_08`: they are the names
the records were written with. The labels and rationales were generated in a
coding-agent workflow at the author's direction; two notes in worked cases that
said otherwise are reworded to that attribution (`nlebench_audit/wording.py`).

## Licence

Copyright 2026 Someple Inc. Every file in this repository, program source
included, is licensed under CC BY-NC 4.0 (legal code in `LICENSE`), except the
files that `LICENSE.md` lists as not covered (the two stored model responses
among them). `LICENSE.md` also says how to attribute and gives an additional
permission for anyone, including for-profit organisations, to reproduce or
check the results reported in the paper. For other uses, including commercial
use, contact Someple Inc. at junha.jang@someple.com; any further permission
requires a separate written agreement.

## Corrections and citation

Open an issue in this repository; `CONTRIBUTING.md` says what a correction is. A
recorded value is never edited in place; a correction is a new commit with a
note of what changed. To cite, see `CITATION.cff`; no DOI or proceedings entry
exists, and none is given.
