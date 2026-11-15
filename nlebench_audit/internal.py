"""Working notes in the exported copies: commit messages, and listed references to material outside this record.

Some records hold text that belongs to the development work rather than to the audit: the subjects and
messages of development commits, and a few sentences that refer to reviews, decisions or documents that are
not part of this record. In the copies held here:

  - `commit_message`: the value at a registered pattern (`MESSAGES`) is dropped, with its key. The commits
    stay identified by their hash and date. A registered pattern that matches no value stops the export.
  - `internal_reference`: a span listed in the maintainer's private map is removed from one string value of a
    JSON file, or from one line of a text file. The map gives the file, the position (or the line), the
    SHA-256 of the original value (or line) and of the span, and the span's offsets. A span that is the whole
    value drops the value with its key. In a text file a listed line may also be dropped whole; it is listed by
    its number and the SHA-256 of its text. A listed span or line that is missing, or whose text differs,
    stops the export.

What is published: the rule, the file and how many values or spans each rule removed. Not published: the
removed text and, for `internal_reference`, its position.

The checks (`check_messages`, `check_references`, `check_reference_lines`) compare an original with its copy:
every difference must be one that the rule explains, and nothing else may differ.
"""
from __future__ import annotations

import copy
import re

from . import integrity

MESSAGE, REFERENCE = "commit_message", "internal_reference"
REMOVED_TEXT = "removed_text"   # an entry of the maintainer's private log only, never of the published log

# file (exact relative path) -> complete position patterns of a commit subject or message
MESSAGES: dict[str, tuple[str, ...]] = {
    "checks/constraint_history.json": (
        "commits/#/subject", "checks_against_the_submission_text/other_commits_with_a_removed_entry/#/subject"),
    "checks/direction_constraints.json": ("commit_message_6b4db68",),
    "sources/ledgers/records_2026_08/206e1838224a/constraint_addition_ledger.json": ("per_commit/#/subject",),
}


def _sha(text: str) -> str:
    return integrity.sha256_bytes(text.encode("utf-8"))


def _drop(document, position: tuple) -> None:
    parent = integrity.get_at(document, position[:-1])
    if isinstance(parent, dict):
        del parent[position[-1]]
    else:
        raise ValueError(f"a value inside a list cannot be dropped with a key: {integrity.position_text(position)}")


def drop_messages(name: str, document) -> tuple[object, list[dict]]:
    """The copy without the commit messages registered for this file, and the log: one entry per pattern."""
    patterns = MESSAGES.get(name, ())
    result = copy.deepcopy(document)
    log = []
    for pattern in patterns:
        found = [position for position, _ in integrity.string_leaves(document) if integrity.matches(position, pattern)]
        if not found:
            raise ValueError(f"{name}: the commit-message pattern {pattern} matches no value")
        for position in found:
            _drop(result, position)
        log.append({"rule": MESSAGE, "pattern": pattern, "removed": len(found)})
    return result, log


def check_messages(name: str, original, derived, log: list[dict]) -> list[str]:
    """Problems between an original and its copy for `drop_messages`. Every difference must be a string of
    the original, at a registered pattern, that is absent from the copy; every such string must be absent."""
    patterns = MESSAGES.get(name, ())
    problems = []
    expected = {pattern: [p for p, _ in integrity.string_leaves(original) if integrity.matches(p, pattern)]
                for pattern in patterns}
    for position, (before, after) in integrity.differing_leaves(original, derived).items():
        where = integrity.position_text(position)
        if not any(integrity.matches(position, pattern) for pattern in patterns):
            problems.append(f"{name}: differs outside a commit-message pattern, at {where}")
        elif not isinstance(before, str) or after is not integrity.MISSING:
            problems.append(f"{name}: {where} is not a dropped string")
    for pattern, positions in expected.items():
        for position in positions:
            if integrity.get_at(derived, position) is not integrity.MISSING:
                problems.append(f"{name}: a commit message remains at {integrity.position_text(position)}")
    logged = {entry["pattern"]: entry["removed"] for entry in log}
    if logged != {pattern: len(positions) for pattern, positions in expected.items()}:
        problems.append(f"{name}: the commit-message log does not count the registered values")
    return problems


def message_texts(name: str, document) -> list[str]:
    """The strings `drop_messages` removes from this document."""
    return [value for position, value in integrity.string_leaves(document)
            if any(integrity.matches(position, pattern) for pattern in MESSAGES.get(name, ()))]


def span_texts(name: str, document, listed: dict[str, dict]) -> list[str]:
    """The spans `apply_references` removes from this document."""
    leaves = {integrity.position_text(position): value for position, value in integrity.string_leaves(document)}
    return [leaves[where][entry["start"]:entry["end"]] for where, entry in listed.items() if where in leaves]


def line_texts(name: str, data: bytes, listed: dict[int, dict]) -> list[str]:
    """The spans and whole lines `apply_reference_lines` removes from this text."""
    rows = data.decode("utf-8").split("\n")
    return [rows[number - 1] if entry.get("drop") else rows[number - 1][entry["start"]:entry["end"]]
            for number, entry in sorted(listed.items()) if 1 <= number <= len(rows)]


def _span(value: str, entry: dict, where: str) -> tuple[int, int]:
    start, end = entry["start"], entry["end"]
    if _sha(value) != entry["original_sha256"]:
        raise ValueError(f"{where}: the listed value differs from the source")
    if not (0 <= start < end <= len(value)) or _sha(value[start:end]) != entry["span_sha256"]:
        raise ValueError(f"{where}: the listed span is not in the value")
    return start, end


def apply_references(name: str, document, listed: dict[str, dict]) -> tuple[object, list[dict]]:
    """The copy with the listed spans removed. `listed`: position text -> its entry of the private map."""
    result = copy.deepcopy(document)
    leaves = {integrity.position_text(position): (position, value) for position, value in integrity.string_leaves(document)}
    dropped = kept = 0
    for where, entry in sorted(listed.items()):
        if where not in leaves:
            raise ValueError(f"{name}: a listed reference is at no string value: {where}")
        position, value = leaves[where]
        start, end = _span(value, entry, f"{name}: {where}")
        rest = value[:start] + value[end:]
        if rest.strip():
            integrity.get_at(result, position[:-1])[position[-1]] = rest
            kept += 1
        else:
            _drop(result, position)
            dropped += 1
    log = [{"rule": REFERENCE, "removed": len(listed), "values_dropped": dropped, "values_shortened": kept}] \
        if listed else []
    return result, log


def check_references(name: str, original, derived, listed: dict[str, dict], log: list[dict]) -> list[str]:
    """Problems between an original and its copy for `apply_references`, found without re-running it."""
    problems = []
    for position, (before, after) in integrity.differing_leaves(original, derived).items():
        where = integrity.position_text(position)
        entry = listed.get(where)
        if entry is None:
            problems.append(f"{name}: differs at {where}, which no listed reference names")
            continue
        if not isinstance(before, str) or _sha(before) != entry["original_sha256"]:
            problems.append(f"{name}: {where} is not the listed original")
            continue
        start, end = entry["start"], entry["end"]
        rest = before[:start] + before[end:]
        if _sha(before[start:end]) != entry["span_sha256"]:
            problems.append(f"{name}: the listed span is not in the value at {where}")
        elif (after is integrity.MISSING) != (not rest.strip()) or (after is not integrity.MISSING and after != rest):
            problems.append(f"{name}: {where} is not the original without the listed span")
    differing = {integrity.position_text(position) for position in integrity.differing_leaves(original, derived)}
    for where in sorted(set(listed) - differing):
        problems.append(f"{name}: the listed reference at {where} was not removed")
    if sum(entry.get("removed", 0) for entry in log if entry.get("rule") == REFERENCE) != len(listed):
        problems.append(f"{name}: the reference log does not count the listed spans")
    return problems


def _lines_without(name: str, rows: list[str], listed: dict[int, dict]) -> list[str]:
    out = []
    for number, row in enumerate(rows, start=1):
        entry = listed.get(number)
        if entry is None:
            out.append(row)
        elif entry.get("drop"):
            if _sha(row) != entry["original_sha256"]:
                raise ValueError(f"{name}: line {number} is listed to be dropped but differs from the source")
        else:
            start, end = _span(row, entry, f"{name}: line {number}")
            out.append(row[:start] + row[end:])
    unknown = sorted(number for number in listed if not 1 <= number <= len(rows))
    if unknown:
        raise ValueError(f"{name}: a listed reference is on no line: {unknown}")
    return out


def apply_reference_lines(name: str, data: bytes, listed: dict[int, dict]) -> tuple[bytes, list[dict]]:
    """A text file with the listed spans removed from the listed 1-based lines, and the lines listed to be
    dropped left out; every other byte kept. The line numbers are those of the input."""
    rows = _lines_without(name, data.decode("utf-8").split("\n"), listed)
    dropped = sum(1 for entry in listed.values() if entry.get("drop"))
    log = [{"rule": REFERENCE, "removed": len(listed), "spans_removed": len(listed) - dropped,
            "lines_dropped": dropped}] if listed else []
    return "\n".join(rows).encode("utf-8"), log


def check_reference_lines(name: str, original: bytes, derived: bytes, listed: dict[int, dict],
                          log: list[dict]) -> list[str]:
    before, after = original.decode("utf-8").split("\n"), derived.decode("utf-8").split("\n")
    dropped = {number for number, entry in listed.items() if entry.get("drop")}
    if len(after) != len(before) - len(dropped):
        return [f"{name}: line count differs ({len(before)} -> {len(after)}, {len(dropped)} listed to be dropped)"]
    problems = []
    kept = [(number, old) for number, old in enumerate(before, start=1) if number not in dropped]
    for number in sorted(dropped):
        if number > len(before) or _sha(before[number - 1]) != listed[number]["original_sha256"]:
            problems.append(f"{name}: line {number} is listed to be dropped but is not the listed line")
    for (number, old), new in zip(kept, after):
        entry = listed.get(number)
        if entry is None:
            if old != new:
                problems.append(f"{name}: line {number} differs and no listed reference names it")
            continue
        start, end = entry["start"], entry["end"]
        if _sha(old) != entry["original_sha256"] or _sha(old[start:end]) != entry["span_sha256"] \
                or new != old[:start] + old[end:]:
            problems.append(f"{name}: line {number} is not the original without the listed span")
    if sum(entry.get("removed", 0) for entry in log if entry.get("rule") == REFERENCE) != len(listed):
        problems.append(f"{name}: the reference log does not count the listed spans")
    return problems


def validate(entries: list[dict]) -> dict[str, dict]:
    """The listed references of the private map, by file: position text or line number -> entry."""
    out: dict[str, dict] = {}
    for entry in entries:
        if ("position" in entry) == ("line" in entry):
            raise ValueError(f"a listed reference names a position or a line, not both: {entry.get('file')}")
        drop = entry.get("drop") is True
        if drop and "line" not in entry:
            raise ValueError(f"only a line of a text file can be dropped whole: {entry.get('file')}")
        for key in ("original_sha256",) if drop else ("original_sha256", "span_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get(key, ""))):
                raise ValueError(f"a listed reference needs {key}: {entry.get('file')}")
        if not drop and not (isinstance(entry.get("start"), int) and isinstance(entry.get("end"), int)
                             and 0 <= entry["start"] < entry["end"]):
            raise ValueError(f"a listed reference needs a span: {entry.get('file')}")
        where = entry["position"] if "position" in entry else int(entry["line"])
        listed = out.setdefault(entry["file"], {})
        if where in listed:
            raise ValueError(f"a reference is listed twice: {entry['file']} {where}")
        listed[where] = ({"drop": True, "original_sha256": entry["original_sha256"]} if drop else
                         {key: entry[key] for key in ("original_sha256", "span_sha256", "start", "end")})
    return out
