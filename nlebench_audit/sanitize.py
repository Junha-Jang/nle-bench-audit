"""Removal of local paths from derived copies, at registered positions only.

(What of a file is exported at all, its dev-split part, is decided before this, in
`projection`. No item of a test-split scenario reaches this step.)

Run stamps of the 2026-10 analysis record the interpreter, the argument list,
the import location and two environment variables of the machine that ran
them. Those strings identify a local directory layout and a local account and
carry no result. In the copies kept in this repository they are replaced by
placeholders. The name of one folder of the development repository,
which held the probe scripts and their ledgers, is replaced the same way, by
<LEDGER_DIR>, wherever a record names a file in it, and the name of the folder
that holds it and the other working records of the development, by <NOTES_DIR>.
Nothing else in those files is changed.

How a transformation is constrained:

  - It applies to a named file and, inside it, to the complete positions listed
    in `JSON_POSITIONS` (or, for a text file, to the line numbers listed in the
    export plan). A local path anywhere else is an error, not a second chance.
  - A placeholder replaces a literal directory prefix. The literal prefixes are
    not in this repository: the maintainer passes them at export time in a
    private map. With that map the replacement can be inverted and compared
    with the original leaf, which is what `check_json` does.
  - A leaf whose last segment is in `WHOLE_VALUE_SEGMENTS` (the PATH variable)
    is replaced as a whole. It cannot be inverted; its original is identified
    by the hash recorded in the transformation log.

The comparison is typed (see `integrity`): a transformed leaf must be a string
before and after, and every leaf that is not registered must be the same JSON
value in the original and in the copy.
"""
from __future__ import annotations

import copy
import json
import re

from . import integrity

STAMP_BLOCK = ("command/#", "execution/env/P3_EXPORT_ROOT", "execution/env/PATH", "runtime/argv/#",
               "runtime/executable", "runtime/nlebench_file", "runtime/sys_path/#")
IDENTITY_BLOCK = ("argv/#", "executable", "nlebench_file", "sys_path/#")
ENV_BLOCK = ("execution/env/P3_EXPORT_ROOT", "execution/env/PATH")


def _under(prefix: str, block: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{prefix}/{item}" if prefix else item for item in block)


# file (exact relative path) -> complete position patterns that may hold a local path
JSON_POSITIONS: dict[str, tuple[str, ...]] = {
    "checks/null_guard.json": _under("stamps/*", STAMP_BLOCK),
    "checks/path_pinning.json": _under("execution/identity", IDENTITY_BLOCK)
    + _under("execution/run", ("command/#",) + ENV_BLOCK),
    "fixtures/predicate_fixtures.json": _under("stamp", STAMP_BLOCK),
}
for _case in ("NLB-v3-CX-AT-003", "NLB-v3-CX-CO-002", "NLB-v3-EX-CO-004", "NLB-v3-EX-CO-009", "NLB-v3-EX-DE-008"):
    JSON_POSITIONS[f"cases/{_case}/case.json"] = _under(
        "reexecuted_2026_10/identities/*", ENV_BLOCK + _under("runtime", IDENTITY_BLOCK))
for _run in ("d31p", "d31s", "d32", "new", "newm", "pre", "sub"):
    JSON_POSITIONS[f"rejudging/runs/{_run}.json"] = STAMP_BLOCK
_RESCORING = "sources/ledgers/records_2026_08/206e1838224a/"
JSON_POSITIONS[_RESCORING + "contract_rescore_v32.json"] = ("_meta/reads_only/#",)
JSON_POSITIONS[_RESCORING + "stored_row_rescore.json"] = (
    "_meta/reads_only/#", "corpus/dev_worktree/root", "corpus/published/root",
    "stored_state_sources/rows/*/resolved_under")

# The folder of the development repository that held the probe scripts and their
# ledgers is named by the placeholder <LEDGER_DIR>, like a local prefix, wherever a record names
# a file in it. Its name carries no result.
_PROBE_FILES = ("benchmark_code/files/probe/path", "benchmark_code/files/probe_full/path")
_CASE_PROBE_FILES = _under("reexecuted_2026_10/identities/*/tree/files", ("probe/path", "probe_full/path"))
JSON_POSITIONS["checks/path_pinning.json"] += _PROBE_FILES
for _run in ("d31p", "d31s", "d32"):
    JSON_POSITIONS[f"rejudging/runs/{_run}.json"] += _PROBE_FILES
JSON_POSITIONS["cases/NLB-v3-CX-AT-003/case.json"] += _CASE_PROBE_FILES
for _case in ("NLB-v3-CX-CO-002", "NLB-v3-EX-DE-008"):
    JSON_POSITIONS[f"cases/{_case}/case.json"] += _CASE_PROBE_FILES + ("recorded_2026_08/ledger/path",)
_ADJUDICATION = "sources/ledgers/adjudication/206e1838224a/"
# two ledgers serialised in their own style; the export writes them in that style (`projection.dumps_like`)
JSON_POSITIONS[_ADJUDICATION + "cluster_hardening.json"] = ("_meta/input", "재실행_잔여/명령")
JSON_POSITIONS[_ADJUDICATION + "false_success_closeout.json"] = ("프로브_확장/무엇을_했나/파일",
                                                                 "수정한_파일/신규_스크립트/#")
# the folder of the development's working records (<NOTES_DIR>), where a record names a file in it and the
# position is not registered above for a local path
JSON_POSITIONS["checks/constraint_history.json"] = ("recorded_ledger/source",)

WHOLE_VALUE_SEGMENTS = {"PATH": "<LOCAL_PATH_ENV>"}

# Scenario files of the probe cases: the one metadata field that named a local account.
# file (exact relative path) -> (1-based line that is rewritten, complete position of the value)
ANNOTATOR_POSITION = "metadata/annotators/#"
YAML_ANNOTATOR_LINES: dict[str, int] = {
    "cases/NLB-v3-CX-AT-003/scenario_473d3b54ecd4.yaml": 42,
    "cases/NLB-v3-CX-CO-002/scenario_6a6dd54230aa.yaml": 52,
    "cases/NLB-v3-CX-CO-002/scenario_b0c48f988109.yaml": 48,
    "cases/NLB-v3-EX-DE-008/scenario_25c92c33cfab.yaml": 43,
    "cases/NLB-v3-EX-DE-008/scenario_61b686d4ec14.yaml": 50,
    "cases/NLB-v3-EX-DE-008/scenario_abf8dc2d3c14.yaml": 47,
}


class PrivateMap:
    """Literal local prefixes and account names, read from a file kept outside the repository; the
    notes withheld by the projection (`projection.WITHHELD`): file, position and SHA-256 of the text; and
    the spans removed as references to material outside this record (`internal.REFERENCE`)."""

    def __init__(self, prefixes: list[tuple[str, str]], private_tokens: list[str],
                 withheld_notes: list[dict] | None = None, internal_references: list[dict] | None = None,
                 origin_words: list[list[str]] | None = None) -> None:
        if not prefixes or not private_tokens:
            raise ValueError("the private map needs at least one prefix and one private token")
        # longest literal first, so a prefix never shadows a longer one
        self.prefixes = sorted(prefixes, key=lambda item: -len(item[1]))
        self.private_tokens = list(private_tokens)
        self.withheld_notes: dict[str, dict[str, str]] = {}
        for entry in withheld_notes or []:
            if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                raise ValueError(f"a withheld note needs the SHA-256 of its text: {entry['file']}")
            listed = self.withheld_notes.setdefault(entry["file"], {})
            if entry["position"] in listed:
                raise ValueError(f"a withheld note is listed twice: {entry['file']}")
            listed[entry["position"]] = entry["sha256"]
        from . import internal
        self.internal_references: dict[str, dict] = internal.validate(internal_references or [])
        # words of the source package's origin descriptions and how SOURCE_MAP.json writes them
        self.origin_words: list[tuple[str, str]] = []
        for pair in origin_words or []:
            if len(pair) != 2 or not all(isinstance(word, str) and word for word in pair) or pair[0] == pair[1]:
                raise ValueError("an origin-word entry needs two different non-empty strings")
            self.origin_words.append((pair[0], pair[1]))

    @classmethod
    def load(cls, path) -> "PrivateMap":
        raw = json.loads(open(path, encoding="utf-8").read())
        return cls([(item["placeholder"], item["literal"]) for item in raw["prefixes"]], raw["private_tokens"],
                   raw.get("withheld_notes", []), raw.get("internal_references", []), raw.get("origin_words", []))

    def references(self, name: str) -> dict:
        """The spans listed for one exported file: position text (JSON) or line number (text) -> entry."""
        return dict(self.internal_references.get(name, {}))

    def withheld(self, name: str) -> dict[str, str]:
        """The withheld notes of one exported file: position -> SHA-256 of the text."""
        return dict(self.withheld_notes.get(name, {}))

    def forward(self, text: str) -> str:
        for placeholder, literal in self.prefixes:
            text = text.replace(literal, placeholder)
        return text

    def backward(self, text: str) -> str:
        for placeholder, literal in self.prefixes:
            text = text.replace(placeholder, literal)
        return text

    def private_hits(self, text: str) -> int:
        return sum(text.count(token) for token in self.private_tokens)

    def literals(self) -> list[str]:
        """Every literal that must not appear in the repository: the prefixes behind the placeholders and the
        origin words that SOURCE_MAP.json writes otherwise."""
        return [literal for _, literal in self.prefixes] + [old for old, _ in self.origin_words]

    def literal_hits(self, text: str) -> int:
        """Occurrences of a literal behind a placeholder, such as a folder name that is not a local path."""
        return sum(text.count(literal) for literal in self.literals())

    def placeholders(self) -> list[str]:
        return sorted({placeholder for placeholder, _ in self.prefixes} | set(WHOLE_VALUE_SEGMENTS.values()))


def registered(name: str, position: tuple) -> bool:
    return any(integrity.matches(position, pattern) for pattern in JSON_POSITIONS.get(name, ()))


def local_looking(text: str, private: PrivateMap) -> bool:
    return bool(private.private_hits(text) or private.literal_hits(text)
                or integrity.SCAN_PATTERNS["home_path"].search(text)
                or integrity.SCAN_PATTERNS["tmp_path"].search(text))


def apply_json(name: str, document, private: PrivateMap) -> tuple[object, list[dict]]:
    """The copy of the document with registered local paths replaced, and the log of what changed."""
    result = copy.deepcopy(document)
    log = []
    for position, value in list(integrity.string_leaves(document)):
        if not registered(name, position):
            if local_looking(value, private):
                raise ValueError(f"{name}: local path at an unregistered position "
                                 f"{integrity.position_text(position)}")
            continue
        whole = WHOLE_VALUE_SEGMENTS.get(position[-1]) if isinstance(position[-1], str) else None
        new = whole if whole is not None else private.forward(value)
        if new == value:
            continue
        if local_looking(new, private):
            raise ValueError(f"{name}: a local path remains at {integrity.position_text(position)}")
        parent = integrity.get_at(result, position[:-1])
        parent[position[-1]] = new
        log.append({"position": integrity.position_text(position), "path": list(position),
                    "rule": "whole_value" if whole else "prefix",
                    "original_sha256": integrity.sha256_bytes(value.encode("utf-8")), "replacement": new})
    return result, log


def check_json(name: str, original, derived, private: PrivateMap, log: list[dict]) -> list[str]:
    """Problems between an original document and its derived copy. Empty means the copy is the
    original with exactly the logged, registered replacements."""
    problems = []
    logged = {entry["position"]: entry for entry in log}
    differing = integrity.differing_leaves(original, derived)
    for position, (before, after) in differing.items():
        where = integrity.position_text(position)
        if not registered(name, position):
            problems.append(f"{name}: differs at an unregistered position {where}: {before!r} -> {after!r}")
            continue
        if not isinstance(before, str) or not isinstance(after, str):
            problems.append(f"{name}: {where} is not a string on both sides: {before!r} -> {after!r}")
            continue
        entry = logged.get(where)
        if entry is None:
            problems.append(f"{name}: {where} differs but is not in the transformation log")
            continue
        if entry["replacement"] != after:
            problems.append(f"{name}: {where} is not the logged replacement")
        if entry["original_sha256"] != integrity.sha256_bytes(before.encode("utf-8")):
            problems.append(f"{name}: {where} does not come from the logged original")
        whole = WHOLE_VALUE_SEGMENTS.get(position[-1]) if isinstance(position[-1], str) else None
        if whole is not None:
            if after != whole:
                problems.append(f"{name}: {where} should be {whole}")
        elif private.backward(after) != before:
            problems.append(f"{name}: {where} does not invert to the original")
    for where in sorted(set(logged) - {integrity.position_text(position) for position in differing}):
        problems.append(f"{name}: {where} is logged but does not differ")
    for position, value in integrity.string_leaves(derived):
        if local_looking(value, private):
            problems.append(f"{name}: a local path remains at {integrity.position_text(position)}")
    return problems


def apply_json_lines(name: str, data: bytes, private: PrivateMap) -> tuple[bytes, list[dict]]:
    """`apply_json` for a file that is not in a serialisation this tool writes: the same leaves are
    replaced, each on its own line of the text, and every other byte is kept. Lines are matched to
    values in document order."""
    document = json.loads(data.decode("utf-8"))
    derived_document, log = apply_json(name, document, private)
    rows = data.decode("utf-8").split("\n")
    cursor = 0
    for entry in log:
        position = tuple(entry["path"])
        old = json.dumps(integrity.get_at(document, position), ensure_ascii=False)
        new = json.dumps(entry["replacement"], ensure_ascii=False)
        start = old if isinstance(position[-1], int) else json.dumps(position[-1], ensure_ascii=False) + ": " + old
        index = next((number for number in range(cursor, len(rows))
                      if rows[number].strip() in (start, start + ",")), None)
        if index is None:
            raise ValueError(f"{name}: no line holds the value at {entry['position']}")
        cursor = index + 1
        rows[index] = rows[index].replace(old, new)
        entry["line"] = index + 1
    derived = "\n".join(rows).encode("utf-8")
    if not integrity.same_json(json.loads(derived.decode("utf-8")), derived_document):
        raise ValueError(f"{name}: the rewritten text is not the document with the logged replacements")
    return derived, log


def check_json_lines(name: str, original: bytes, derived: bytes, log: list[dict]) -> list[str]:
    """For a file rewritten by `apply_json_lines`: the lines that differ are the logged lines.
    The leaves are compared by `check_json`."""
    before, after = original.decode("utf-8").split("\n"), derived.decode("utf-8").split("\n")
    if len(before) != len(after):
        return [f"{name}: line count differs ({len(before)} -> {len(after)})"]
    changed = sorted(number for number, (old, new) in enumerate(zip(before, after), start=1) if old != new)
    if changed != sorted(entry.get("line", 0) for entry in log):
        return [f"{name}: the lines that differ are not the logged lines"]
    return []


def apply_lines(name: str, data: bytes, lines: tuple[int, ...], private: PrivateMap) -> tuple[bytes, list[dict]]:
    """A text file with the listed 1-based lines rewritten by the prefix map; every other byte kept."""
    rows = data.decode("utf-8").split("\n")
    log = []
    for number, row in enumerate(rows, start=1):
        if number in lines:
            new = private.forward(row)
            if new == row or local_looking(new, private):
                raise ValueError(f"{name}: line {number} is listed but holds no replaceable local path")
            rows[number - 1] = new
            log.append({"position": f"line {number}", "line": number, "rule": "prefix",
                        "original_sha256": integrity.sha256_bytes(row.encode("utf-8")), "replacement": new})
        elif local_looking(row, private):
            raise ValueError(f"{name}: local path on unlisted line {number}")
    return "\n".join(rows).encode("utf-8"), log


def check_yaml_metadata(name: str, original: bytes, derived: bytes, private: PrivateMap, log: list[dict]) -> list[str]:
    """Problems between a scenario file and its derived copy.

    Two independent comparisons. As text: only the one registered line may differ, and it must
    invert to the original. As parsed YAML, by the typed rules: every differing leaf must be at
    the registered annotator position of this exact file and be a string on both sides. A change
    of fixture, constraints, turns or any other field fails, whatever line it is on.
    """
    import yaml
    if name not in YAML_ANNOTATOR_LINES:
        return [f"{name}: no annotator line is registered for this file"]
    problems = check_lines(name, original, derived, (YAML_ANNOTATOR_LINES[name],), private, log)
    before, after = yaml.safe_load(original.decode("utf-8")), yaml.safe_load(derived.decode("utf-8"))
    differing = integrity.differing_leaves(before, after)
    if not differing:
        problems.append(f"{name}: the parsed documents do not differ although a replacement is logged")
    for position, (old, new) in differing.items():
        where = integrity.position_text(position)
        if not integrity.matches(position, ANNOTATOR_POSITION):
            problems.append(f"{name}: parsed value differs outside the annotator field, at {where}")
        elif not isinstance(old, str) or not isinstance(new, str):
            problems.append(f"{name}: {where} is not a string on both sides")
        elif private.backward(new) != old:
            problems.append(f"{name}: {where} does not invert to the original")
    for key in ("fixture", "constraints", "turns"):
        if not integrity.same_json(before.get(key, integrity.MISSING), after.get(key, integrity.MISSING)):
            problems.append(f"{name}: the identity field `{key}` changed")
    return problems


def check_lines(name: str, original: bytes, derived: bytes, lines: tuple[int, ...], private: PrivateMap,
                log: list[dict]) -> list[str]:
    before, after = original.decode("utf-8").split("\n"), derived.decode("utf-8").split("\n")
    if len(before) != len(after):
        return [f"{name}: line count differs ({len(before)} -> {len(after)})"]
    problems = []
    logged = {entry["position"]: entry for entry in log}
    for number, (old, new) in enumerate(zip(before, after), start=1):
        if old == new:
            if number in lines:
                problems.append(f"{name}: line {number} is listed but unchanged")
            continue
        entry = logged.get(f"line {number}")
        if number not in lines or entry is None:
            problems.append(f"{name}: line {number} differs and is not a listed, logged line")
        elif entry["replacement"] != new or private.backward(new) != old \
                or entry["original_sha256"] != integrity.sha256_bytes(old.encode("utf-8")):
            problems.append(f"{name}: line {number} is not the logged, invertible replacement")
    if local_looking(derived.decode("utf-8"), private):
        problems.append(f"{name}: a local path remains")
    return problems
