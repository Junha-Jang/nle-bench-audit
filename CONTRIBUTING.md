# Maintenance and corrections

This repository is a fixed record for one paper. It is not where the benchmark
is developed, and it does not promise support for the benchmark.

## Who maintains it

Junha Jang, the author of the paper. Corrections are received through this
repository's issue tracker. The scope is errors of fact in the audit record.

## What a correction is

- **A recorded value is never edited in place.** Tables, cases, ledgers, pinned
  modules and scorer files are copies of dated records. If one is wrong, the
  wrong version stays reachable and a new commit adds the correction with a note
  saying what changed and why.
- **The cited history is never rewritten.** The paper cites the first commit of
  this repository. No commit is moved, amended or force-pushed; a correction is
  a new commit on top.
- **A pinned historical file is not repaired.** A scenario or module that was
  broken at its commit is the evidence. It stays broken here.
- **A change of meaning is not a correction.** Anything that alters a count, a
  label, a definition or the scope of a claim is a new result and is stated as
  one.

## How to change the repository

Branch from `main`; `main` is the only long-lived branch. Use a short-lived
topic branch named for its purpose (`feature/...`, `bugfix/...`, `docs/...`,
`chore/...`) and commit messages of the form `type(scope): summary`.

Exported files are never edited by hand:

1. Change the plan or the rules in `tools/export.py`, `nlebench_audit/projection.py`,
   `nlebench_audit/internal.py` or `nlebench_audit/sanitize.py`.
2. `python tools/export.py build ...` writes the derived files. It refuses to
   overwrite a file that differs unless told to.
3. `python tools/export.py check ...` derives everything again and compares
   each transformed copy with its source leaf by leaf.
4. `python tools/export.py content-review ...` measures against the scenario
   corpora which files quote scenario instructions, and writes
   `provenance/CONTENT_REVIEW.json`.
5. `python tools/export.py finalize ...` measures the checkout and writes
   `provenance/SCAN.json` and `MANIFEST.json`. Run it last, after any change,
   including a change to a document.

These steps need the sources, which are not included here, and a map kept
outside the repository (the literals behind the placeholders and the listed
removals). Without them, a contributor can still run everything else:

```
python -m nlebench_audit all
python -m pytest
```

Both must pass before a change is proposed. A change that touches a file listed
in `MANIFEST.json` without regenerating the manifest fails `verify`.

## How a published version is made

A published version is one commit made from the files of one reviewed state:

1. `python tools/candidate.py build --out NEW_DIRECTORY --name ... --email ...`
   writes those files into a new directory and commits them once. The address
   is the author's public no-reply address, not a personal one.
2. `python tools/candidate.py check --dir ... --private-map ... --contact ...` fails unless
   the directory is exactly one commit of exactly those files, with no other
   reference, remote, stray object, uncommitted file or link, and with no
   local path, account name or other address in any object. The one address a
   file may give is the public contact address named with `--contact`.
3. Such a commit is never amended. If a file changes before publication, a new
   commit is built in a new directory; after publication, a change is a new
   commit on top.

## What must stay true

- No local directory path or account name is added to any file.
- No licence text, DOI, release date or venue status is added that does not exist.
- `inventory/promises/` and `inventory/issues/` are brought up to date in the
  same change that alters what they describe.
- A status of `present` is never used for something that is only described.
