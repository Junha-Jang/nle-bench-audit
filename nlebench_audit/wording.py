"""Descriptive wording in the exported copies: the review snapshot is called the review snapshot.

Records of the 2026-08 ledgers and of the 2026-10 runs describe the benchmark's review snapshot,
commit 58afb977, in their own words. In the copies held here those descriptions use the term of
the paper, by a fixed list of phrase replacements (`PHRASES`), applied to string values only.
Quotations of code or of commit messages, and the code files themselves, keep their words.
Keys, column names, identifiers, commit hashes and paths are not changed: a value that is one of
the identifiers in `KEPT_VALUES` is left alone, and a phrase is replaced only where it appears
whole. The sources keep the original wording.

One phrase of the 2026-10 analysis attributed the 2026-08 rationales to a single author; it is
replaced in the same way by the paper's attribution, a coding-agent workflow.

The log records, for every changed value, its position, the hash of the original string and
the new string; the original string is not recorded. The check (`check`) compares an original
with its copy: every difference must be a logged position whose copy is the original with the
listed phrases replaced, and no listed phrase may remain anywhere in a string value.
"""
from __future__ import annotations

import copy
import re

from . import integrity

TRANSFORMATION = "descriptive_wording"

# longest first: a phrase is never replaced inside a longer phrase that has its own replacement
PHRASES: tuple[tuple[str, str], ...] = (
    ("scenarios_v3_1 (published artifact copy)", "scenarios_v3_1 (snapshot copy)"),
    ("public artifact (published copy of v3.1)", "review snapshot (snapshot copy of v3.1)"),
    ("corpus of the public artifact (published copy of v3.1)", "corpus of the review snapshot (snapshot copy of v3.1)"),
    ("published harness and corpus, public rescoring script, scorer of the public artifact",
     "snapshot harness and corpus, snapshot rescoring script, scorer of the review snapshot"),
    ("published harness and corpus, public rescoring script", "snapshot harness and corpus, snapshot rescoring script"),
    ("published artifact copy", "snapshot copy"),
    ("published copy of v3.1", "snapshot copy of v3.1"),
    ("published per-record verdicts", "snapshot per-record verdicts"),
    ("the public artifact tree", "the snapshot tree"),
    ("scorer of the public artifact", "scorer of the review snapshot"),
    ("unchanged from the public artifact", "unchanged from the review snapshot"),
    ("of the published harness", "of the snapshot harness"),
    ("inside the published harness", "inside the snapshot harness"),
    ("published harness", "snapshot harness"),
    ("published corpus", "snapshot corpus"),
    ("public rescoring script", "snapshot rescoring script"),
    ("the public script", "the snapshot script"),
    ("public script", "snapshot script"),
    ("public scorer", "snapshot scorer"),
    ("public review snapshot", "review snapshot"),
    ("public artifact", "review snapshot"),
    ("the published file was produced", "the snapshot's file was produced"),
    ("(as published)", "(as in the review snapshot)"),
    ("the official published rescore", "the official snapshot rescore"),
    ("single-author judgment of 2026-08", "judgment recorded in 2026-08 in the audit's coding-agent workflow"),
)
PHRASES = tuple(sorted(PHRASES, key=lambda item: -len(item[0])))

# string values that are identifiers, not descriptions
KEPT_VALUES = frozenset({"published"})

# what must not remain in a string value of a copy
RESIDUE = re.compile(r"(?i)\bpublic(?:ly)?\s+(?:artifact|review|snapshot|script|scorer|rescoring|copy)"
                     r"|\bpublished\s+(?:artifact|harness|corpus|copy|per-record|verdict|scorer|script)"
                     r"|\bsingle-author\s+judgment")


def replace(text: str) -> str:
    if text in KEPT_VALUES:
        return text
    out, index = [], 0
    while index < len(text):
        for old, new in PHRASES:
            if text.startswith(old, index):
                out.append(new)
                index += len(old)
                break
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def apply(name: str, document) -> tuple[object, list[dict]]:
    result = copy.deepcopy(document)
    log = []
    for position, value in list(integrity.string_leaves(document)):
        new = replace(value)
        if new == value:
            continue
        parent = integrity.get_at(result, position[:-1])
        parent[position[-1]] = new
        log.append({"position": integrity.position_text(position), "path": list(position),
                    "rule": TRANSFORMATION, "original_sha256": integrity.sha256_bytes(value.encode("utf-8")),
                    "replacement": new})
    return result, log


def residue(document) -> list[str]:
    return [integrity.position_text(position) for position, value in integrity.string_leaves(document)
            if RESIDUE.search(value)]


def check(name: str, original, derived, log: list[dict]) -> list[str]:
    problems = []
    logged = {entry["position"]: entry for entry in log if entry.get("rule") == TRANSFORMATION}
    differing = integrity.differing_leaves(original, derived)
    for position, (before, after) in differing.items():
        where = integrity.position_text(position)
        entry = logged.get(where)
        if entry is None:
            problems.append(f"{name}: differs at {where} without a logged wording change")
        elif not isinstance(before, str) or replace(before) != after or entry["replacement"] != after \
                or entry["original_sha256"] != integrity.sha256_bytes(before.encode("utf-8")):
            problems.append(f"{name}: {where} is not the logged phrase replacement of its original")
    for where in sorted(set(logged) - {integrity.position_text(position) for position in differing}):
        problems.append(f"{name}: {where} is logged but does not differ")
    for where in residue(derived):
        problems.append(f"{name}: a listed phrase remains at {where}")
    return problems
