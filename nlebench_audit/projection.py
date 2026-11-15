"""The dev-split projection: what of each exported file is kept, and the checks of it.

This repository holds no item of a test-split scenario. A scenario's split is read from
the pinned corpora at export time (`tools/export.py`), not from this repository. Where an
exported file names scenarios, the export keeps the parts that belong to dev-split
scenarios and drops the parts that belong to test-split scenarios, by the rules below:

  - `rows`: a CSV file (or a gzip of one) keeps its header and the rows whose scenario is in
    the dev split. Every row must name exactly one scenario, in one split.
  - `item`: in a JSON file, the elements of a list, or the entries of an object, matched by
    the pattern are items. An item is dropped when its split (below) is the test split. An item
    that names scenarios of both splits is kept for a deeper rule; an item that names no
    scenario stops the export.
  - `field`: the value at the pattern is dropped, with its key, when every scenario it names
    is in the test split. A value that names both splits is an error.
  - `note`: a value of prose at the pattern (a note, a reason, a summary line, or an element of
    a list of them) is dropped, with its key, when it names any test-split scenario, even if it
    also names dev-split ones: a sentence about a test-split item may restate its instruction,
    so the sentence goes, not only the name.
  - `withheld_note`: a value of prose at the pattern that concerns test-split scenarios without
    naming any, and so passes the `note` rule, is dropped, with its key, when the maintainer's
    map kept outside the repository lists its position and the SHA-256 of its text. Which values are
    listed is not published,
    like the split itself. A listed value that is missing, or whose text differs, stops the export.

A scenario is named by its id or, in prose, by its bare code ("CX-CO-002"); a bare code counts
only when it is the code of a scenario of the corpora. An item's split is the split of the
scenario its own key names (`scenario`, `scenario_id`, `legacy_scenario_id`, `시나리오`), or of
the key of an object entry, or, for an item without such a key, of every scenario it names.

Rules of one file run in the order listed, an outer item before the items inside it. After
the rules, a file that still names a test-split scenario anywhere, in a key or a value, is an
error: the projection fails closed. A scenario name that is not a scenario of the pinned
corpora is an error too.

The check (`check_json`, `check_rows`) does not re-run these rules. It compares the original
with the derived copy and asks: does the copy name no test-split scenario; does it name every
dev-split scenario as often as the original does, apart from the counted removals of fields;
does every difference lie inside a registered container; and does each container hold its
original dev items, in order and unchanged?

What is published about a projection: the rule, the pattern and how many items it removed.
Not published: which items, their positions or their scenarios. Those follow from the
sources, which are not included here, and are the maintainer's record.
"""
from __future__ import annotations

import copy
import csv
import gzip
import hashlib
import io
import json
import re

from . import integrity

# a scenario name in any of its forms: "NLB-v3-CX-CO-002" (the corpus id) or "NLEB-CX-CO-002" (the id of the
# per-scenario results); the bare code "CX-CO-002" is what identifies the scenario
SCENARIO = re.compile(r"(?<![A-Za-z0-9])(?:NLB-v3-|NLEB-)([A-Z]{2}-[A-Z]{2}-\d{3})(?![0-9])")
# an id of the older corpus scenarios_v2 ("NLB-043"), which has its own dev/test split; its code is the whole id
OLDER = re.compile(r"(?<![A-Za-z0-9-])(NLB-\d{3})(?![0-9])")


def code_of(name: str):
    """The code of a scenario id of either corpus, or None."""
    found = SCENARIO.fullmatch(name) or OLDER.fullmatch(name)
    return found.group(1) if found else None

ROWS, ITEM, FIELD, NOTE, WITHHELD = "rows", "item", "field", "note", "withheld_note"
BARE = re.compile(r"(?<![A-Za-z0-9-])([A-Z]{2}-[A-Z]{2}-\d{3})(?![0-9])")
ITEM_KEYS = ("scenario", "scenario_id", "legacy_scenario_id", "시나리오")
TRANSFORMATION = "dev_split_projection"

_ADJ = "sources/ledgers/adjudication/206e1838224a/"
_REC = "sources/ledgers/records_2026_08/206e1838224a/"
_PROBE_FINDINGS = ((ITEM, "findings/*/#"),)
_CASE_NOTES = ((NOTE, "recorded_2026_08/author_rationale_2026_08/*"),)

# file (exact relative path) -> rules in order
PROJECTION: dict[str, tuple[tuple[str, str], ...]] = {
    "checks/null_guard.csv": ((ROWS, "scenario_id"),),
    "rejudging/diff_A.csv": ((ROWS, "scenario_id"),),
    "rejudging/diff_A_prime.csv": ((ROWS, "scenario_id"),),
    "rejudging/diff_BC.csv": ((ROWS, "scenario_id"),),
    "rejudging/diff_harness_new_to_dev_tree.csv": ((ROWS, "scenario_id"),),
    "rejudging/membership.csv": ((ROWS, "scenario_id"),),
    "rejudging/per_record.csv.gz": ((ROWS, "scenario_id"),),
    "sites/candidate_keys.csv": ((ROWS, "scenario"),),
    "sites/recovery_168.csv": ((ROWS, "key_str"),),
    "checks/constraint_history.json": ((ITEM, "commits/#/examples/#"),),
    "checks/null_guard.json": (
        (ITEM, "feasible_null_passes/#"),
        (ITEM, "checks_against_the_submission_text/recorded_no_op_pass_ids/*/#"),
    ),
    "checks/path_pinning.json": (
        (ITEM, "items/*"),
        (ITEM, "final_ledger_file_path_survivors_not_targets/#"),
    ),
    "sites/candidate_reconciliation.json": ((ITEM, "cross_round/cause_conflicts_on_shared_sites/#"),),
    "cases/NLB-v3-CX-CO-002/case.json": _CASE_NOTES,
    "cases/NLB-v3-EX-DE-008/case.json": _CASE_NOTES,
    _ADJ + "cluster_hardening.json": (
        (ITEM, "건별/#"),
        (NOTE, "건별/#/*"),
        (NOTE, "재실행_잔여/*"),
        (NOTE, "_limits/*/#"),
        (NOTE, "_limits/*"),
    ),
    _ADJ + "false_success_triage.json": (
        (ITEM, "findings/*/#"),
        (NOTE, "findings/*/#/*"),
        (NOTE, "공통_패턴/#/해당/#"),
        (NOTE, "공통_패턴/#/*"),
        (NOTE, "가장_시급한_보강_셋/#"),
        (NOTE, "_limits/#"),
    ),
    _ADJ + "constraint_hardening.json": (
        (ITEM, "항목/#"),
        (ITEM, "보강하지_않은_것/#"),
        (ITEM, "정답_수정/#"),
        (NOTE, "관찰_이번에_손대지_않은_것/#"),
        (FIELD, "보강_후_변이검정_재실행/남은_UNDER"),
        (NOTE, "항목/#/*"),
        (NOTE, "패턴별/*/*"),
        (NOTE, "_limits/#"),
    ),
    _ADJ + "false_success_closeout.json": (
        (ITEM, "항목/#"),
        (ITEM, "수정한_파일/정답_정리(53건 대상)/#"),
        (ITEM, "수정한_파일/제약_보강(53건 대상)/#"),
        (ITEM, "수정한_파일/제약_보강(확장 프로브 신규 발견)/시나리오/*/#"),
        (ITEM, "확장_보강_검증_로그/*/#"),
        (ITEM, "저작_권고(제약·정답으로는 못 고치는 것)/#"),
        (ITEM, "저작_권고(제약·정답으로는 못 고치는 것)/#/시나리오/#"),
        (NOTE, "항목/#/*"),
        (NOTE, "저작_권고(제약·정답으로는 못 고치는 것)/#/*"),
        (NOTE, "프로브_확장/이번에_닫은_것/*/*"),
        (NOTE, "프로브_확장/남은_381건의_분류(클러스터 단위, 이번에 손대지 않음)/#/*"),
        (WITHHELD, "프로브_확장/남은_381건의_분류(클러스터 단위, 이번에 손대지 않음)/#/설명"),
        (NOTE, "최종_확인/*/*/#"),
        (NOTE, "최종_확인/*/*"),
        (NOTE, "최종_확인/*"),
        (NOTE, "_limits/#"),
    ),
    # The ledger also covers the older corpus scenarios_v2 ("NLB-043"), which has its own dev/test split
    # (read from the same development pin); its test-split items are dropped by the same rules.
    _REC + "constraint_predicate_audit.json": tuple(
        (ITEM, f"{prefix}/{corpus}/#")
        for prefix in ("predicates/*/corpus/scenario_ids", "predicates/*/noop_behaviour/noop_pass_scenario_ids",
                       "predicates/*/noop_behaviour/error_scenario_ids", "whole_scenario_noop/*")
        for corpus in ("scenarios_v2", "scenarios_v3", "scenarios_v3_1")
    ) + (
        (ITEM, "whole_scenario_noop/newly_visible_v2_no_gap/#"),
        (FIELD, "predicates/*/corpus/observed_keysets/#/example"),
        (ITEM, "scenario_level_findings/#"),
        (ITEM, "scenario_level_findings/#/scenario_ids/#"),
        (ITEM, "scenario_level_findings/#/candidate_repair/*"),
        (NOTE, "scenario_level_findings/#/*"),
        (ITEM, "effect_removed_repair_probe/*"),
        (ITEM, "fixture_build_failures/#"),
    ),
    _REC + "stored_row_rescore.json": (
        (ITEM, "ledgers/*/#"),
        (ITEM, "corpus/divergence/constraints_differ_scenario_ids/#"),
    ),
}
for _stage in ("S0_first_ledger_5d2f3a6175ad", "S1_after_fixture_deepcopy_0b3a4adb7086",
               "S2_after_wave1_c8be3e83aa0f", "S3_final_28a5f11c6f26"):
    PROJECTION[f"sources/ledgers/probe_round1/{_stage}/false_success_probe.json"] = _PROBE_FINDINGS
for _stage in ("T0_first_ledger_28a5f11c6f26", "T1_after_immediate_closes_942b45a19a52", "T2_final_8d0ca7e0da61"):
    PROJECTION[f"sources/ledgers/probe_round2/{_stage}/false_success_probe_full.json"] = _PROBE_FINDINGS


class Splits:
    """The split of every scenario of the pinned corpora, by its bare code."""

    def __init__(self, split_of: dict[str, str]) -> None:
        self.of: dict[str, str] = {}
        for name, split in split_of.items():
            code = code_of(name)
            if not code or split not in ("dev", "test"):
                raise ValueError(f"not a scenario id with a split: {name!r} {split!r}")
            if self.of.setdefault(code, split) != split:
                raise ValueError(f"{name}: two splits")

    def named(self, text: str) -> set[str]:
        """The scenarios a string names, by id or by bare code. An id that is not a scenario of the
        corpora is an error; a bare code counts only when it is one."""
        codes = set(SCENARIO.findall(text))
        if any(code.startswith("NLB-") for code in self.of):
            codes |= set(OLDER.findall(text))
        unknown = sorted(code for code in codes if code not in self.of)
        if unknown:
            raise ValueError(f"names a scenario that is not in the pinned corpora: {unknown[:3]}")
        return codes | {code for code in BARE.findall(text) if code in self.of}

    def item_split(self, position: tuple, value) -> set[str]:
        """The split of an item: by its own scenario key, else by the key of its entry, else by all
        it names."""
        if isinstance(value, dict):
            for key in ITEM_KEYS:
                if isinstance(value.get(key), str):
                    codes = self.named(value[key])
                    if len(codes) == 1:
                        return {self.of[next(iter(codes))]}
        if isinstance(position[-1], str):
            codes = self.named(position[-1])
            if codes:
                return {self.of[code] for code in codes}
        return self.splits_named(value)

    def splits_named(self, value) -> set[str]:
        """The splits of the scenarios a JSON value names, in its keys and its strings."""
        return {self.of[code] for code in self.codes_in(value)}

    def codes_in(self, value) -> set[str]:
        if isinstance(value, dict):
            out: set[str] = set()
            for key, item in value.items():
                out |= self.named(key) | self.codes_in(item)
            return out
        if isinstance(value, list):
            out = set()
            for item in value:
                out |= self.codes_in(item)
            return out
        return self.named(value) if isinstance(value, str) else set()

    def test_positions(self, value, position: tuple = ()) -> list[str]:
        """Every position whose key or string names a test-split scenario."""
        found = []
        if isinstance(value, dict):
            for key, item in value.items():
                if any(self.of[code] == "test" for code in self.named(key)):
                    found.append(integrity.position_text(position + (key,)) + " (key)")
                found += self.test_positions(item, position + (key,))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                found += self.test_positions(item, position + (index,))
        elif isinstance(value, str) and any(self.of[code] == "test" for code in self.named(value)):
            found.append(integrity.position_text(position))
        return found


def _positions(document, pattern: str) -> list[tuple]:
    """Every concrete position in the document that the pattern matches."""
    segments = pattern.split("/")
    found = []

    def walk(value, position: tuple) -> None:
        depth = len(position)
        if depth == len(segments):
            found.append(position)
            return
        want = segments[depth]
        if isinstance(value, dict):
            for key in value:
                if want == "*" or want == key:
                    walk(value[key], position + (key,))
        elif isinstance(value, list) and want == "#":
            for index in range(len(value)):
                walk(value[index], position + (index,))

    walk(document, ())
    return found


def _drop(document, positions: list[tuple]) -> None:
    """Remove the values at the positions; list elements from the highest index down."""
    for position in sorted(positions, key=lambda p: [(0, s) if isinstance(s, int) else (1, s) for s in p],
                           reverse=True):
        parent = integrity.get_at(document, position[:-1])
        del parent[position[-1]]


def unregistered_withheld(names) -> list[str]:
    """Files for which notes are listed as withheld but no withheld_note rule is registered. Such a
    listing would never reach `project_json` and so would never be checked; the export refuses it."""
    return sorted(name for name in names
                  if not any(kind == WITHHELD for kind, _ in PROJECTION.get(name, ())))


def _text_sha256(value) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if isinstance(value, str) else None


def project_json(name: str, document, splits: Splits,
                 withheld: dict[str, str] | None = None) -> tuple[object, list[dict], list[dict]]:
    """The dev-split copy of a JSON document, the public log (rule, pattern, count) and the
    private log (every removed position with its scenarios, or with its hash for a withheld note).
    `withheld` maps a position of this file to the SHA-256 of the note withheld there."""
    withheld = dict(withheld or {})
    if withheld and not any(kind == WITHHELD for kind, _ in PROJECTION[name]):
        raise ValueError(f"{name}: withheld notes are listed but no withheld_note rule is registered")
    found: set[str] = set()
    derived = copy.deepcopy(document)
    public, private = [], []
    for kind, pattern in PROJECTION[name]:
        removed = []
        for position in _positions(derived, pattern):
            value = integrity.get_at(derived, position)
            if kind == WITHHELD:
                where = integrity.position_text(position)
                if where in withheld:
                    if _text_sha256(value) != withheld[where]:
                        raise ValueError(f"{name}: the withheld note at a listed position has other text")
                    removed.append(position)
                    found.add(where)
                continue
            if kind == ITEM:
                named = splits.item_split(position, value)
                if not named:
                    raise ValueError(f"{name}: an item names no scenario, at {integrity.position_text(position)}")
            else:
                named = splits.splits_named(value)
            if named == {"test"} or (kind == NOTE and "test" in named):
                removed.append(position)
            elif kind == FIELD and named == {"dev", "test"}:
                raise ValueError(f"{name}: a field names both splits at {integrity.position_text(position)}")
        for position in removed:
            value = integrity.get_at(derived, position)
            if kind == WITHHELD:
                private.append({"rule": kind, "pattern": pattern, "position": integrity.position_text(position),
                                "sha256": _text_sha256(value)})
                continue
            private.append({"rule": kind, "pattern": pattern, "position": integrity.position_text(position),
                            "scenarios": sorted(splits.codes_in(
                                {position[-1]: value} if isinstance(position[-1], str) else value))})
        _drop(derived, removed)
        public.append({"rule": TRANSFORMATION, "kind": kind, "pattern": pattern, "removed": len(removed)})
    missing = sorted(set(withheld) - found)
    if missing:
        raise ValueError(f"{name}: {len(missing)} listed withheld note(s) not found at a withheld_note pattern")
    remaining = splits.test_positions(derived)
    if remaining:
        raise ValueError(f"{name}: names a test-split scenario after the projection, at {remaining[:3]}")
    return derived, public, private


def project_rows(name: str, data: bytes, splits: Splits) -> tuple[bytes, list[dict], list[dict]]:
    """The dev-split copy of a CSV file (gzip when the name ends in .gz): the header and the dev
    rows, each byte of a kept line unchanged."""
    zipped = name.endswith(".gz")
    text = (gzip.decompress(data) if zipped else data).decode("utf-8")
    lines = text.split("\n")
    trailing = lines[-1] == ""
    body = lines[:-1] if trailing else lines
    column = PROJECTION[name][0][1]
    header = next(csv.reader([body[0]]))
    if column not in header:
        raise ValueError(f"{name}: no column {column!r}")
    kept, removed = [body[0]], []
    for number, line in enumerate(body[1:], start=2):
        row = next(csv.reader([line]))
        if len(row) != len(header):
            raise ValueError(f"{name}: line {number} does not hold one complete row")
        codes = set()
        for cell in row:
            codes |= splits.named(cell)
        named = {splits.of[code] for code in codes}
        if len(named) != 1 or not splits.named(row[header.index(column)]):
            raise ValueError(f"{name}: line {number} does not name scenarios of exactly one split")
        if named == {"test"}:
            removed.append({"rule": ROWS, "pattern": column, "line": number, "scenarios": sorted(codes)})
        else:
            kept.append(line)
    out = ("\n".join(kept) + ("\n" if trailing else "")).encode("utf-8")
    if zipped:
        out = gzip.compress(out, mtime=0)
    return out, [{"rule": TRANSFORMATION, "kind": ROWS, "pattern": column, "removed": len(removed)}], removed


# ---------------------------------------------------------------------------
# checks, by comparison of original and copy
# ---------------------------------------------------------------------------

def _count_codes(value, splits: Splits) -> dict[str, int]:
    counts: dict[str, int] = {}

    def add(text: str) -> None:
        for code in splits.named(text):
            counts[code] = counts.get(code, 0) + len(re.findall(r"(?<![A-Za-z0-9])" + re.escape(code) + r"(?![0-9])", text))

    def walk(item) -> None:
        if isinstance(item, dict):
            for key, inner in item.items():
                add(key)
                walk(inner)
        elif isinstance(item, list):
            for inner in item:
                walk(inner)
        elif isinstance(item, str):
            add(item)

    walk(value)
    return counts


def expected_json(name: str, original, splits: Splits, withheld: dict[str, str] | None = None):
    """The dev-split copy derived a second way, for the check: rules taken deepest pattern first, each
    marking its positions on the current document and dropping them together; an item is judged by
    its value in the original, a field or a note by its value after the deeper rules. It shares no
    loop with `project_json`; the two must agree."""
    rules = sorted(PROJECTION[name], key=lambda rule: -len(rule[1].split("/")))
    listed = withheld or {}
    working = copy.deepcopy(original)
    for kind, pattern in rules:
        marked = []
        for position in _positions(working, pattern):
            if kind == WITHHELD:
                text = integrity.get_at(working, position)
                if isinstance(text, str) and listed.get(integrity.position_text(position)) == \
                        hashlib.sha256(text.encode("utf-8")).hexdigest():
                    marked.append(position)
                continue
            if kind == ITEM:
                before = integrity.get_at(original, position)
                if isinstance(position[-1], str):
                    split = {splits.of[c] for c in splits.named(position[-1])}
                else:
                    split = set()
                if not split and isinstance(before, dict):
                    owners = [before[key] for key in ITEM_KEYS if isinstance(before.get(key), str)
                              and len(splits.named(before[key])) == 1]
                    split = {splits.of[c] for c in splits.named(owners[0])} if owners else set()
                if not split:
                    split = {splits.of[c] for c in splits.codes_in(before)}
                if split == {"test"}:
                    marked.append(position)
            else:
                split = {splits.of[c] for c in splits.codes_in(integrity.get_at(working, position))}
                if "test" in split and (kind == NOTE or split == {"test"}):
                    marked.append(position)
        _drop(working, marked)
    return working


def check_json(name: str, original, derived, splits: Splits, log: list[dict],
               withheld: dict[str, str] | None = None) -> list[str]:
    problems = []
    rules = PROJECTION.get(name)
    if rules is None:
        return [f"{name}: no projection is registered for this file"]
    expected = expected_json(name, original, splits, withheld)
    # the withheld list, checked on its own: every listed position is a position of a withheld_note rule in
    # the copy derived without the list, its text has the listed hash, and the log counts exactly those
    listed = withheld or {}
    unregistered = unregistered_withheld([name]) if listed else []
    if unregistered:
        problems.append(f"{name}: withheld notes are listed but no withheld_note rule is registered")
    matched = 0
    if listed and not unregistered:
        without = expected_json(name, original, splits)
        found = {integrity.position_text(position): integrity.get_at(without, position)
                 for kind, pattern in rules if kind == WITHHELD for position in _positions(without, pattern)}
        for where, digest in listed.items():
            if where not in found:
                problems.append(f"{name}: a listed withheld note is not at a withheld_note position")
            elif _text_sha256(found[where]) != digest:
                problems.append(f"{name}: a listed withheld note has other text")
            else:
                matched += 1
    logged_withheld = sum(entry["removed"] for entry in log
                          if entry.get("rule") == TRANSFORMATION and entry.get("kind") == WITHHELD)
    if logged_withheld != len(listed) or matched != len(listed):
        problems.append(f"{name}: the log counts {logged_withheld} withheld notes, the private map lists "
                        f"{len(listed)} and {matched} of them are found with their text")
    differing = integrity.differing_leaves(expected, derived)
    if differing:
        problems.append(f"{name}: the copy is not the dev-split copy derived independently; it differs at "
                        f"{[integrity.position_text(position) for position in list(differing)[:3]]}")
    remaining = splits.test_positions(derived)
    if remaining:
        problems.append(f"{name}: names a test-split scenario at {remaining[:3]}")
    before, after = _count_codes(original, splits), _count_codes(derived, splits)
    notes = any(kind == NOTE for kind, _ in rules)
    for code in sorted(set(before) | set(after)):
        if splits.of.get(code) != "dev":
            continue
        # a dropped note may name dev-split scenarios too; nothing may add a name
        if after.get(code, 0) > before.get(code, 0) or (not notes and after.get(code, 0) != before.get(code, 0)):
            problems.append(f"{name}: a dev-split scenario is named {before.get(code, 0)} times in the "
                            f"original and {after.get(code, 0)} times in the copy")
    # every difference lies inside a registered container
    containers = [pattern.split("/")[:-1] for _, pattern in rules]
    for position in integrity.differing_leaves(original, derived):
        if not any(len(position) > len(c) and (not c or integrity.matches(position[:len(c)], "/".join(c)))
                   for c in containers):
            problems.append(f"{name}: differs outside a registered container, at "
                            f"{integrity.position_text(position)}")
    # a list container of an innermost item rule holds items of one original container, in their
    # order and unchanged (an outer rule may have removed whole items, so containers are matched by
    # content, not by position)
    patterns = [pattern for _, pattern in rules]
    for kind, pattern in rules:
        if kind != ITEM or not pattern.endswith("/#") or any(p.startswith(pattern + "/") for p in patterns):
            continue
        originals = [integrity.get_at(original, c) for c in _positions(original, pattern[:-2])]
        originals = [value for value in originals if isinstance(value, list)]
        for container in _positions(derived, pattern[:-2]):
            new = integrity.get_at(derived, container)
            if not isinstance(new, list):
                continue
            if not any(_subsequence(new, old) for old in originals):
                problems.append(f"{name}: {integrity.position_text(container)} holds items that are not "
                                "original items in their order")
    removed = sum(entry["removed"] for entry in log if entry.get("rule") == TRANSFORMATION)
    if removed == 0:
        problems.append(f"{name}: registered for the projection but nothing was removed")
    return problems


def _subsequence(new: list, old: list) -> bool:
    cursor = 0
    for item in new:
        while cursor < len(old) and not integrity.same_json(old[cursor], item):
            cursor += 1
        if cursor == len(old):
            return False
        cursor += 1
    return True


def check_rows(name: str, original: bytes, derived: bytes, splits: Splits, log: list[dict]) -> list[str]:
    zipped = name.endswith(".gz")
    old = (gzip.decompress(original) if zipped else original).decode("utf-8").split("\n")
    new = (gzip.decompress(derived) if zipped else derived).decode("utf-8").split("\n")
    problems = []
    if old[0] != new[0]:
        problems.append(f"{name}: the header changed")
    expected = [old[0]] + [line for line in old[1:] if line and {splits.of[c] for c in splits.named(line)} == {"dev"}]
    if old[-1] == "":
        expected.append("")
    if new != expected:
        problems.append(f"{name}: the rows are not the original dev rows in order")
    if any(splits.of.get(code) == "test" for line in new for code in splits.named(line)):
        problems.append(f"{name}: a row names a test-split scenario")
    removed = sum(entry["removed"] for entry in log if entry.get("rule") == TRANSFORMATION)
    if removed != len(old) - len(new):
        problems.append(f"{name}: the log counts {removed} removed rows, the files differ by {len(old) - len(new)}")
    return problems


def dumps_like(original_text: str, document) -> str:
    """A serialisation of the document in the style of the original text: the first of the
    styles below that reproduces the original exactly. A ledger in another style is an error."""
    original = json.loads(original_text)
    for indent in (1, 2, 4):
        for sort in (False, True):
            for end in ("\n", ""):
                if json.dumps(original, ensure_ascii=False, indent=indent, sort_keys=sort) + end == original_text:
                    return json.dumps(document, ensure_ascii=False, indent=indent, sort_keys=sort) + end
    raise ValueError("the original is not in a serialisation this tool reproduces")


def leak_positions(value, splits: Splits) -> list[str]:
    return splits.test_positions(value)


def io_rows(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8"))))
