# Probe sources and the ledgers behind the tables

Copies of files tracked in the development repository (not public), each at a
named commit. `provenance/SOURCE_MAP.json` records commit, path, blob id and both
hashes of every one. In those paths, and wherever a record names a file of it,
the folder of that repository that held the probe scripts and their ledgers is
named by the placeholder `<LEDGER_DIR>`, and the folder that holds it and the
other working records by `<NOTES_DIR>`.

## `probes/`

| File | Commits held | Role |
|---|---|---|
| `false_success_probe.py` | `5d2f3a6175ad` (first run), `206e1838224a` (development pin) | round 1 of the mutation probe |
| `false_success_probe_full.py` | `28a5f11c6f26` (first run), `206e1838224a` | round 2 |

These are the scripts as they were, with the changes that
`provenance/TRANSFORMATIONS.json` lists for each file:

- the three lines of each file that named a local directory or that folder now
  show a placeholder;
- in both copies of `false_success_probe.py`, the opening lines of the
  docstring, which quoted a review that is not part of this record, are left
  out (7 lines), and two spans are removed: one in the docstring, and one in the
  string the script writes as the "why" of its ledger. Run as held, the script
  would write its "why" without that sentence; the four round-1 ledgers below
  lose the same sentence.

The code is otherwise unchanged. Between the first-run version and the pin
version only comments differ.

**They cannot be run from this repository.** A probe loads the benchmark from a
checkout and iterates over the 640 feasible scenarios of the corpus at its
commit. No corpus is held here. What can be replayed is three of their findings,
as the worked cases (`python -m nlebench_audit cases`).

## `ledgers/probe_round1/`, `ledgers/probe_round2/`

Every tracked version of the two probe ledgers, in commit order: four snapshots
of round 1 (`S0` to `S3`) and three of round 2 (`T0` to `T2`), each holding its
dev-split findings (`provenance/README.md`). The folder name gives the snapshot
and the commit. The 139 dev-split candidate sites of `sites/candidate_keys.csv`
are the union of the dev-split keys in these snapshots, as reconciled in
`sites/candidate_reconciliation.json`.

## `ledgers/adjudication/`

The triage, closeout and two hardening ledgers at the development pin. They
hold the label and the recorded reason for each finding. The labels and reasons
were generated in a coding-agent workflow during the audit; the reported
evidence does not include human validation or an inter-rater agreement
measurement. The notes are partly in Korean. Each holds its
dev-split items; in two of them four values that name a file of the ledger
folder show `<LEDGER_DIR>` (`provenance/README.md`).

## `ledgers/records_2026_08/`

| File | What |
|---|---|
| `stored_row_rescore.json` | per-row rescoring of the stored records, 2026-08 |
| `contract_rescore_v32.json` | rescoring on the v3.2 corpus, 2026-08 |
| `constraint_addition_ledger.json` | constraints added per commit; the commit subjects are left out |
| `constraint_predicate_audit.json` | audit of the declared predicates; one span that names a table of another document is removed |
| `preservation_annex.json` | preservation records |

These are the 2026-08 records as they were written. The 2026-10 analysis under
`rejudging/` and `checks/` re-examined several of their numbers and records
where it reads them differently; the 2026-08 net of +797, for one, is kept as
recorded and is no longer read as a corpus-only change. Lists and rows that
name scenarios hold their dev-split part; aggregate values that name no
scenario cover both splits as recorded. In the two rescoring ledgers, strings
that named a local directory were replaced by placeholders, and descriptions of
the review snapshot use the term of the paper, at the positions listed in
`provenance/TRANSFORMATIONS.json`; no number or label was touched.

## Not here

The corpus-defect audit and the corpus-identity record of 2026-08, the 13 probe
ledgers regenerated in 2026-10, the raw run logs and the analysis code.
