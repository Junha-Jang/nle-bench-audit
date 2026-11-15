# Pinned benchmark modules

`pins/<name>/nlebench/` is a subset of the benchmark's Python package at one
commit. Every file is the byte-identical git blob of that commit; `PINS.json`
records the repository, commit, source path, SHA256 and blob id of each, and
`python -m nlebench_audit verify` compares them.

These directories exist so that the replay commands run from this checkout
alone. They are not the benchmark, and they are not installed: a command starts
a subprocess whose import path is one pin directory, and fails if `nlebench`
was imported from anywhere else.

| Pin | Commit | Used by |
|---|---|---|
| `dev-206e1838224a` | development pin | `fixtures`; stage `current_pin` of the three probe cases |
| `dev-28a5f11c6f26` | the repair commit | stages `code_axis_alone` and `post` of `NLB-v3-CX-CO-002` and `NLB-v3-EX-DE-008` |
| `dev-69f8d58a1382` | before that repair | stage `pre` of the same two cases |
| `dev-5d2f3a6175ad` | first probe commit | stage `pre` of `NLB-v3-CX-AT-003` |
| `artifact-58afb977515b` | review snapshot | `stored-cases`: the snapshot harness, under its own scorer and with `runner/constraints.py` replaced by `scorers/current_constraints.py` |

## How the file sets were chosen

Each command was run on a full tree of each commit with a hook that recorded
every module imported and every file opened under that tree. The file set of a
pin is exactly that record:

- for `cases`: the package and its `config`, `models` and `protocols` modules;
  `dataset` and `dataset/fixtures` with one fixture file; `runner` with
  `constraints` and `scenario_loader`; `tools` with `executor` and `schema`;
- for `fixtures`, in addition: `runner/validator` and a second fixture file;
- for `stored-cases`: the package, `config`, `models`, `runner` and
  `runner/constraints`.

Nothing else of the benchmark is here: no corpus, no provider or agent code, no
metrics, no scripts. The tests in `tests/test_repository.py` remove one file at
a time from a copy and require the matching command to fail.

## Limits

- The set is the import closure of the commands in this repository. A different
  command would need different files.
- Two analysed stages are not held: for `NLB-v3-CX-CO-002` and
  `NLB-v3-EX-DE-008`, the code before the repair with the new constraints. Its
  scenario was assembled for that test and is in no commit.
- Dependency versions are observed, not locked (`requirements-observed.txt`).
- The licence under which these files are shared, and the permission it rests
  on, are stated in `LICENSE.md`.
