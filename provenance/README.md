# Provenance

Generated files. None is edited by hand; `tools/export.py` writes them.

| File | Content |
|---|---|
| `SOURCE_MAP.json` | one entry per exported file: destination, its SHA256 and size, the source (the source package and path, or repository, commit, path and git blob), the source's SHA256 and size, and how the file was transformed |
| `TRANSFORMATIONS.json` | for each transformed file, its steps and what each changed: the rule and count of a dev-split projection; the replaced tables of `expected.json`; the pattern and count of dropped commit messages; the count of removed references; every replaced leaf or line, with its position, the rule, the SHA256 of the original string and the replacement |
| `SCAN.json` | counts of generic patterns in every file of the checkout, measured; and the files that still hold a local account name |
| `CONTENT_REVIEW.json` | which files quote scenario instructions and name scenarios, by split, measured against the corpora at the three pins |
| `../MANIFEST.json` | every file of the checkout except itself, with size, SHA256 and origin |

## Two sources

- **The source package of 2026-10-06**, identified by the SHA256 of its
  manifest (`ddbef96e…b3fa`). Its tables, cases, checks, fixtures and scorer
  files are carried over, in the scope below. Four of its files are not: its
  README and scan are replaced by the documents of this repository, its working
  inventory is not exported, and its replay tool is replaced by
  `nlebench_audit/replay.py`, which runs on the pinned modules held here.
- **Git objects** of the development repository (not public) and of the review
  snapshot, at named commits: the pinned modules, the probe scripts and the
  ledgers.

## Scope: the dev split

The corpus has 200 dev-split and 600 test-split scenarios. The split of a
scenario is read at export time from the scenario files of the three pinned
corpora; a scenario with two different splits, or a name that is not a
scenario of those corpora, stops the export.

Where an exported file names scenarios, the export keeps its dev-split part and
drops the test-split part, by rules registered per file in
`nlebench_audit/projection.py`:

- **rows**: a CSV file keeps its header and the rows of dev-split scenarios;
  every row must name scenarios of exactly one split;
- **item**: in a JSON file, an element of a list, or an entry of an object,
  matched by a registered pattern is dropped when every scenario it names, in
  its keys and values, is a test-split scenario;
- **field**: a registered value is dropped, with its key, when every scenario it
  names is a test-split scenario;
- **note**: a registered value of prose (a note, a reason, a summary line, or an
  element of a list of them) is dropped, with its key, when it names any
  test-split scenario, even if it also names dev-split ones. A sentence about a
  test-split item may restate its instruction, so the sentence goes, not only
  the name;
- **withheld_note**: a registered value of prose that concerns test-split
  scenarios without naming one, and so passes the note rule, is dropped, with
  its key, when a list kept outside the repository names its position and the
  SHA-256 of its text. Which values are listed is not published; the rule, the
  pattern and the count are (`withheld_note`: 1 value in 1 file). A listed value
  that is missing, or whose text differs, stops the export.

A scenario is named by its id or, in prose, by its bare code. An item's split is
the split of the scenario its own key names; an item that names no scenario
stops the export. After the rules a file that still names a test-split scenario
stops the export. A kept dev-split item is kept whole, except for a note in it
that names a test-split scenario: 39 such values in 7 files were removed, and
the exported reasons and limitations are therefore not complete.

For the 800-scenario corpus analyzed in the paper, scenario-level records here
cover the dev split only. The predicate-audit ledger of 2026-08 also covers the
older `scenarios_v2` corpus, read from the development pin, which has its own
dev/test split: its test-split items are excluded by the same rules, and its
dev-split scenario lists, per-scenario findings and repair-probe records are
kept. They are outside the paper's dev/test population and the dev-split
recount. The leak gate also fails if an id of the older corpus appears together
with a scenario of the pinned corpora in one row or object.

28 files are reduced this way. `TRANSFORMATIONS.json` records, for each, the
rule, the pattern and the number of items it removed; it does not record which
items, their positions or their scenarios.

Not reduced, and kept as recorded: aggregate values that name no scenario,
such as the totals of the 2026-10 analysis outputs (`rejudging/rejudging.json`,
`rejudging/runs/`, `rejudging/rows.csv`), the summary fields of the 2026-08
ledgers and of `sites/candidate_reconciliation.json`. They cover both splits
and cannot be recounted from the files here.

`expected.json` keeps its structure. Its `tables` are the dev-split counts,
counted by the export from the full per-record file and site table of the source package
(`tools/export.py`, `dev_tables`); `tables` recounts them from the exported
dev-split files, with code written apart from the export's.

For the 800-scenario corpus, scenario-level records here cover the dev split
only. The full-corpus totals cannot be recounted from them, and the recorded
judgments for omitted items cannot be inspected from them.

## What else was changed, and only that

Of the 145 exported files, 90 are byte-identical to their source. Besides the
dev-split projection and the recount of `expected.json`, in this order:

- **Commit messages** (`nlebench_audit/internal.py`, rule `commit_message`). The
  subjects of development commits and one excerpt of a commit message are left
  out, with their keys: 39 subjects and 1 excerpt in 3 files, at registered
  patterns. The commits stay identified by hash and date. The claims of the
  dropped messages cannot be read here; `checks/direction_constraints.json`
  still names a commit "whose message states 115", which cannot be checked from
  these files.
- **References to material outside this record** (rule `internal_reference`).
  A span listed outside the repository, with the SHA-256 of the value and of
  the span and its offsets in characters, is removed from a value or a line:
  12 values in 9 JSON files (three of them dropped whole, with their keys; one of
  those named a dev-split scenario that the same file still names elsewhere), and in
  the two copies of `sources/probes/*/false_success_probe.py` seven docstring
  lines and two spans each. One of those spans is in the string the script
  writes as the "why" of its ledger. The spans are not published. The export
  stops if a listed span is missing or differs. Build and check also reject
  reappearance of removed texts of at least eight characters after trimming
  whitespace in the derived files and the provenance files generated by the
  export; shorter texts and other checkout files are outside this check.
- **Local paths.** 20 JSON files whose run stamps, records or ledgers recorded
  local paths (the interpreter, the argument list, the import location, two
  environment variables, directories read from), and 4 probe scripts with three
  lines naming a local checkout or the ledger folder. A local directory prefix
  became a placeholder (`<EXPORT_ROOT>`, `<EXPORT_ROOT_FULL>`,
  `<ANALYSIS_CHECKOUT>`, `<DEV_CHECKOUT>`, `<ARTIFACT_CHECKOUT>`), and the
  `PATH` variable as a whole became `<LOCAL_PATH_ENV>`. The folder of the
  development repository that held the probe scripts and their ledgers became
  `<LEDGER_DIR>`, and the folder that holds it and the other working records
  `<NOTES_DIR>`, there, in the ledgers and in the source paths of
  `SOURCE_MAP.json`; their names carry no result. Each replaced value carries
  the hash of its original string.
- **Descriptive wording.** In 14 JSON files, 907 values were reworded by a fixed
  list of phrases (`nlebench_audit/wording.py`): 905 values that describe the
  review snapshot use the term of the paper, and 2 notes of the worked cases that
  attributed a 2026-08 rationale to the author alone use the paper's attribution,
  a coding-agent workflow. Keys, column names, identifiers, commit hashes, paths, and
  quotations of code or of commit messages keep their words; so do the code
  files themselves. Each changed value carries the hash of its original string.
- **Account name.** In 6 scenario files of the probe cases the one list item
  under `metadata/annotators` became `<ANNOTATOR_ACCOUNT>`; the list is kept.

Apart from these registered removals and replacements, no number, label,
identifier, verdict, fixture, constraint or instruction of a kept item was
changed; a kept item may have lost a note that named a test-split scenario, a
commit message, or a span that referred to material outside this record.

**A scenario file that was changed no longer has the hash of its source.** The
2026-10 runs, `expected.json` and each `case.json` record the hash of the
source file, and the file name carries its first twelve characters.
`cases/SCENARIO_IDENTITIES.json` gives both hashes for every scenario file and
says which are identical to their source. `cases` and `verify` check the held
file against the held hash and the recorded stage identity against the source
hash, and `cases` then re-runs every stage on the held file.

The rules that make this checkable:

- a replacement is allowed only at a complete position registered for that
  file, never by matching the end of a path (`nlebench_audit/sanitize.py`);
- a projection runs only the rules registered for that file, and fails closed
  (`nlebench_audit/projection.py`);
- a commit message is dropped only at a registered pattern, and a span only
  where the list names it and both hashes match; every listed span must be
  used (`nlebench_audit/internal.py`);
- the comparison of copy and source is typed: a boolean is not a number, `null`
  is only `null`, and a missing key is not a present `null`;
- each step is checked by comparing its input and its output. For the
  projection, the check derives the dev-split copy a second way, with its own
  loop, and requires the two to be equal; no test-split scenario remains; no
  dev-split scenario is named more often than before, and less often only where
  a note was dropped; every difference lies inside a registered container.

## What a reader can and cannot check

From the checkout alone, `python -m nlebench_audit verify` checks that every
file has its recorded hash, that a file recorded as unchanged has the hash of
its source, that each logged replacement is in place, that no phrase of the
earlier descriptive wording remains, that no file names a scenario outside the
200 of `rejudging/membership.csv`, and that no local path remains.

Whether a file really equals its source, and whether a projection dropped
exactly the test-split items, can be checked only with the sources:
`python tools/export.py check`, run by the maintainer. Source identifiers and
hashes do not supply a file that is not held here.

## `CONTENT_REVIEW.json`

`tools/export.py content-review` reads the scenario corpora at the three pins
from git objects into a temporary directory and records, for every file here,
which scenarios' instructions it quotes verbatim and which scenarios it names,
by split; how many of the quoted texts are not in the review snapshot; the
values of every `annotators` field; and which model responses are kept and
why. It also lists what it did not examine. It is a measurement to read, not a
certificate. Dev-split membership determines which items are retained;
registered notes referring to test-split scenarios are omitted as described
above, while the retained values are preserved.

Measured on this checkout against the corpora at the three pins: 17 files quote
the instructions of 83 dev-split scenarios, and 54 files name scenarios by
identifier, 200 of the dev split and 0 of the test split. The quote measure
also counts 13 test-split scenarios: a turn of their instruction is, word for
word, an instruction of a kept dev-split item. Those dev-split items are kept.
The exclusion is by item, so this is not a statement that no text of a
test-split scenario occurs here.

## Kept as recorded, on purpose

- Short labels of analysis steps in a few records, explained in the top-level
  README.
- Retained Korean notes.
- The `instruction` fields of the adjudication ledgers, for the dev-split items
  kept.
