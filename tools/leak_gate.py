"""Maintainer tool: the test-split leak gate, run after an export and on any tree that is to be published.

    python tools/leak_gate.py --tree DIR [--git DIR] [--also FILE ...]
                              --rc DIR --dev-repo DIR --artifact-repo DIR --private-map FILE

It needs the sources, which are not included here, like `tools/export.py`; a reader of the repository
cannot run it.

What it looks for, in every file of the tree (gzip opened; JSON parsed, keys and values; file names
and paths), in the commit messages and ref names of `--git`, and in each `--also` file (a PDF is read
as text):

  - a test-split scenario, by any of its names: "NLB-v3-XX-YY-NNN", "NLEB-XX-YY-NNN" or the bare
    code "XX-YY-NNN"; and a test-split scenario of the older corpus scenarios_v2, which has its own
    dev/test split at the development pin, by its id "NLB-NNN";
  - a test-split instruction, in any wording recorded for it: every turn of the scenario in each of
    the three pinned corpora and in scenarios_v2, and every instruction the source ledgers
    quote for a test-split item (later wordings among them), white space collapsed, from
    MIN_WORDING characters on.

YAML files (`.yaml`, `.yml`, gzip opened) and JSON-lines records are parsed like JSON: every key and string
value is scanned decoded, by its position. A YAML file is also scanned as written: for test ids in all of its
text, and for test wordings in its text outside the spans of the allowed instruction values (`_own_spans`), so
that a comment or anything outside the parsed values is seen.

The exceptions: a turn of a test-split instruction that occurs, word for word, inside an instruction
recorded for a dev-split scenario may occur
  - where a kept dev-split item quotes its own instruction, at the instruction positions listed in
    `SHARED_POSITIONS`, in an item that names that dev-split scenario;
  - in a scenario file (YAML) or a sample record (JSON lines) of a dev-split scenario, at the positions
    listed in `OWN_TURNS`, where the whole value is an instruction recorded for that scenario.
Anywhere else it is a hit, also a second occurrence in another field of the same file or record. The
exceptions are by position, not by text. And the ids declared in `SYNTHETIC_IDS` may occur in the one file
named there, only while that file has the reviewed bytes.

Also a hit, whatever its split: an id of the older corpus scenarios_v2 ("NLB-043") in the same line, the
same object's own string values, or the same string as a pinned-corpus id or scenario file path; such a
pairing would tie an older item to an item of the pinned corpora.

The gate passes at zero hits. It is a scan that complements the structural checks of
`tools/export.py check`; it does not replace them, and zero hits does not show that no paraphrase or
reason about a test-split item remains.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from nlebench_audit import integrity, projection  # noqa: E402

MIN_WORDING = 20
_ADJ = "sources/ledgers/adjudication/206e1838224a/"
# file -> (instruction position pattern, key of the scenario in the nearest enclosing object)
SHARED_POSITIONS = {
    _ADJ + "cluster_hardening.json": ("건별/#/instruction", "scenario"),
    _ADJ + "false_success_triage.json": ("findings/*/#/instruction", "scenario"),
    _ADJ + "false_success_closeout.json": ("항목/#/지시문_요지", "시나리오"),
    "checks/null_guard.json": ("feasible_null_passes/#/instruction/#", "scenario_id"),
}
# the positions, inside a dev-split scenario file or sample record, of that scenario's own instruction turns
OWN_TURNS = ("turns/#/instruction", "scenario/turns/#/instruction")
OWN_FORMATS = (".yaml", ".yml", ".jsonl")
# Declared exceptions, by file: ids that occur only as synthetic examples, not as items of a corpus.
# code/tests/test_scenario_loader.py of the review snapshot builds in-memory scenarios with these ids; their
# contents differ from the older corpus's files of the same name (checked on 2026-10-29). The
# exception holds only for the reviewed bytes of that file (sha256); with any other bytes it fails closed.
# (the ids are assembled here so that this file does not itself name them)
SYNTHETIC_IDS = {"code/tests/test_scenario_loader.py": (
    "962c5a13fc9965267f48b8ed8578156f4cac8562865b0891557f1af37fa65a6b",
    {"NLB-" + number for number in ("001", "042", "501")})}
BARE = re.compile(r"(?<![A-Za-z0-9-])([A-Z]{2}-[A-Z]{2}-\d{3})(?![0-9])")
# an id of the older corpus scenarios_v2 ("NLB-043"); a dev-split one may remain, but only where nothing ties it to
# a scenario of the pinned corpora
OLD_ID = re.compile(r"(?<![A-Za-z0-9-])NLB-\d{3}(?![0-9])")
TIED = re.compile(r"(?:NLB-v3-|NLEB-)[A-Z]{2}-[A-Z]{2}-\d{3}|scenarios_v3[^\s\"']*\.ya?ml")


def old_id_tie(values: list[str]) -> bool:
    """An old-corpus id and a pinned-corpus id or scenario file path in one row, one object's own string
    values, or one string."""
    joined = "\n".join(values)
    return bool(OLD_ID.search(joined) and TIED.search(joined))


def flat(text: str) -> str:
    return " ".join(text.split())


class Target:
    """What a test-split item may look like: its codes and every recorded wording."""

    def __init__(self, corpora, ledger_wordings: dict[str, set[str]]) -> None:
        self.splits = corpora.splits
        self.test_codes = {code for code, split in self.splits.of.items() if split == "test"}
        self.dev_turns: dict[str, set[str]] = {}
        self.test_turns: set[str] = set()
        for name, texts in corpora.wordings.items():
            code = projection.code_of(name)
            turns = {flat(turn) for text in texts for turn in text.split(" || ")}
            if self.splits.of[code] == "test":
                self.test_turns |= turns
            else:
                self.dev_turns.setdefault(code, set()).update(turns)
        for name, texts in ledger_wordings.items():
            code = projection.code_of(name)
            turns = {flat(turn) for text in texts for turn in re.split(r" \|\|? ", text)}
            if self.splits.of.get(code) == "test":
                self.test_turns |= turns
            else:
                # a dev-split item's own recorded wording, whole and by turn
                self.dev_turns.setdefault(code, set()).update(turns | {flat(text) for text in texts})
        self.test_turns = {turn for turn in self.test_turns if len(turn) >= MIN_WORDING}
        self.shared = {turn for turn in self.test_turns
                       if any(turn in dev for turns in self.dev_turns.values() for dev in turns)}

    def codes_in(self, text: str) -> set[str]:
        found = set(projection.SCENARIO.findall(text)) | set(BARE.findall(text)) | set(projection.OLDER.findall(text))
        return found & self.test_codes

    def wordings_in(self, text: str) -> set[str]:
        text = flat(text)
        return {turn for turn in self.test_turns if turn in text}


def ledger_wordings(sources) -> dict[str, set[str]]:
    """Every instruction the source ledgers quote, by the scenario of its item (originals)."""
    out: dict[str, set[str]] = {}
    import export
    for name, (pattern, key) in SHARED_POSITIONS.items():
        entry = next((item for item in export.GIT_FILES if item[0] == name), None)
        data = sources.git_bytes(entry[1], entry[2], entry[3])[0] if entry else sources.rc_bytes(name)
        document = json.loads(data.decode("utf-8"))
        for position, value in integrity.string_leaves(document):
            if integrity.matches(position, pattern):
                scenario = _scenario_of(document, position, key)
                if scenario:
                    out.setdefault(scenario, set()).add(value)
    return out


def _scenario_of(document, position: tuple, key: str):
    for depth in range(len(position) - 1, -1, -1):
        item = integrity.get_at(document, position[:depth])
        if isinstance(item, dict) and key in item:
            return item[key]
    return None


def _allowed(name: str, document, position: tuple, wording: str, target: Target) -> bool:
    if name not in SHARED_POSITIONS or wording not in target.shared:
        return False
    pattern, key = SHARED_POSITIONS[name]
    if not integrity.matches(position, pattern):
        return False
    scenario = _scenario_of(document, position, key)
    code = projection.code_of(str(scenario or ""))
    if not code or target.splits.of.get(code) != "dev":
        return False
    # the wording lies inside an instruction recorded for that dev-split scenario
    return any(wording in own for own in target.dev_turns.get(code, set()))


def scan_text(label: str, text: str, target: Target, synthetic: set[str] = frozenset()) -> list[str]:
    hits = [f"{label}: names {len(codes)} test-split scenario(s)"
            for codes in [target.codes_in(text) - set(synthetic)] if codes]
    wordings = target.wordings_in(text)
    if wordings:
        hits.append(f"{label}: holds {len(wordings)} test-split instruction wording(s)")
    return hits


def _own_value(record, position: tuple, value: str, target: Target) -> bool:
    """Whether a value is the own instruction of a dev-split scenario file or sample record: at one of
    `OWN_TURNS`, and the whole value an instruction recorded for that scenario (pinned corpora, source ledgers)."""
    if not any(integrity.matches(position, own) for own in OWN_TURNS) or not isinstance(record, dict):
        return False
    code = projection.code_of(str(record.get("id") or record.get("scenario_id") or ""))
    if not code or target.splits.of.get(code) != "dev":
        return False
    return flat(value) in target.dev_turns.get(code, set())


def _own(record, position: tuple, value: str, wording: str, target: Target) -> bool:
    """A shared wording inside an own instruction value (`_own_value`)."""
    return wording in target.shared and _own_value(record, position, value, target)


def _own_spans(node, record, target: Target, position: tuple = ()) -> list[tuple[int, int]]:
    """The spans, in the YAML text, of the scalars that are own instruction values (`_own_value`), from the
    composed node tree; the text outside them is scanned for wordings as written."""
    import yaml
    if isinstance(node, yaml.MappingNode):
        return [span for key, value in node.value
                for span in _own_spans(value, record, target, position + (key.value if isinstance(key, yaml.ScalarNode)
                                                                         else None,))]
    if isinstance(node, yaml.SequenceNode):
        return [span for index, value in enumerate(node.value)
                for span in _own_spans(value, record, target, position + (index,))]
    if isinstance(node, yaml.ScalarNode) and isinstance(node.value, str) and \
            _own_value(record, position, node.value, target):
        return [(node.start_mark.index, node.end_mark.index)]
    return []


def _walk(label: str, document, target: Target, permit) -> tuple[list[str], dict[str, int]]:
    """Every key and string value of a parsed document, by position; `permit(position, value, wording)`
    says whether a wording is allowed there. Returns the hits and the allowed wordings, counted."""
    hits: list[str] = []
    allowed: dict[str, int] = {}

    def walk(value, position: tuple) -> None:
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if isinstance(value, dict):
            own = [str(key) for key in value] + [inner for inner in value.values() if isinstance(inner, str)]
            if old_id_tie(own):
                hits.append(f"{label}: an old-corpus id tied to a pinned scenario at "
                            f"{integrity.position_text(position)}")
            for key, inner in value.items():
                hits.extend(scan_text(f"{label}: key at {integrity.position_text(position)}", str(key), target))
                walk(inner, position + (key,))
        elif isinstance(value, list):
            for index, inner in enumerate(value):
                walk(inner, position + (index,))
        elif isinstance(value, str):
            where = integrity.position_text(position)
            if old_id_tie([value]):
                hits.append(f"{label}: an old-corpus id tied to a pinned scenario in one string at {where}")
            if target.codes_in(value):
                hits.append(f"{label}: names a test-split scenario at {where}")
            for wording in target.wordings_in(value):
                if permit(position, value, wording):
                    allowed[wording] = allowed.get(wording, 0) + flat(value).count(wording)
                else:
                    hits.append(f"{label}: a test-split instruction wording at {where}")

    walk(document, ())
    return hits, allowed


def scan_file(name: str, data: bytes, target: Target) -> tuple[list[str], int]:
    """Hits in one file, and how many shared wordings were found at allowed positions."""
    hits = scan_text(f"{name} (file name)", name, target)
    declared = SYNTHETIC_IDS.get(name)
    synthetic = declared[1] if declared and hashlib.sha256(data).hexdigest() == declared[0] else set()
    inner = name
    if name.endswith(".gz"):
        data = gzip.decompress(data)
        inner = name[:-3]
    text = data.decode("utf-8", "replace")
    if inner.endswith(".json"):
        try:
            document = json.loads(text)
        except ValueError:
            # a file named as JSON that does not parse is reported, not scanned as text and passed
            return hits + [f"{name}: does not parse as JSON"] + scan_text(name, text, target), 0
        found, allowed = _walk(name, document, target,
                               lambda position, value, wording: _allowed(name, document, position, wording, target))
        return hits + found, sum(allowed.values())
    rows = [line for line in text.split("\n") if old_id_tie([line])]
    if rows:
        hits.append(f"{name}: {len(rows)} line(s) hold an old-corpus id together with a pinned scenario")
    if inner.endswith(OWN_FORMATS[:2]):
        import yaml
        try:
            documents = list(yaml.safe_load_all(text))
            nodes = list(yaml.compose_all(text))
        except yaml.YAMLError:
            return hits + [f"{name}: does not parse as YAML"] + scan_text(name, text, target), 0
        allowed: dict[str, int] = {}
        spans: list[tuple[int, int]] = []
        for document, node in zip(documents, nodes):
            spans += _own_spans(node, document, target)
        for number, document in enumerate(documents):
            label = name if len(documents) == 1 else f"{name}: document {number}"
            found, own = _walk(label, document, target,
                               lambda position, value, wording, record=document: _own(record, position, value,
                                                                                      wording, target))
            hits += found
            for wording, count in own.items():
                allowed[wording] = allowed.get(wording, 0) + count
        # the text as written: a comment, or anything outside the parsed values; for wordings, the text outside
        # the spans of the own instruction values, whatever their notation
        hits += [f"{name}: names {len(codes)} test-split scenario(s) in its text"
                 for codes in [target.codes_in(text)] if codes]
        outside, last = [], 0
        for start, end in sorted(spans):
            outside.append(text[last:max(start, last)])
            last = max(last, end)
        outside.append(text[last:])
        extra = target.wordings_in("\n".join(outside))
        if extra:
            hits.append(f"{name}: holds {len(extra)} test-split instruction wording(s) outside its own turns")
        return hits, sum(allowed.values())
    if inner.endswith(OWN_FORMATS[2]):
        total = 0
        for number, line in enumerate(text.split("\n"), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                hits.append(f"{name}: line {number} does not parse as JSON")
                continue
            found, own = _walk(f"{name}: line {number}", record, target,
                               lambda position, value, wording, record=record: _own(record, position, value,
                                                                                    wording, target))
            hits += found
            total += sum(own.values())
        return hits, total
    return hits + scan_text(name, text, target, synthetic), 0


def scan_tree(tree: Path, target: Target) -> tuple[list[str], int, int]:
    hits, allowed, files = [], 0, 0
    for path in sorted(tree.rglob("*")):
        if not path.is_file() or ".git" in path.relative_to(tree).parts:
            continue
        files += 1
        found, ok = scan_file(path.relative_to(tree).as_posix(), path.read_bytes(), target)
        hits += found
        allowed += ok
    return hits, allowed, files


def scan_git(repo: Path, target: Target) -> list[str]:
    def run(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout
    hits = scan_text("ref names", run("for-each-ref", "--format=%(refname)"), target)
    hits += scan_text("commit messages", run("log", "--all", "--format=%B"), target)
    return hits


def scan_also(path: Path, target: Target) -> list[str]:
    if path.suffix.lower() == ".pdf":
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True,
                              text=True).stdout
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return scan_text(path.name, text, target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", required=True)
    parser.add_argument("--git")
    parser.add_argument("--also", nargs="*", default=[])
    for name in ("--rc", "--dev-repo", "--artifact-repo", "--private-map"):
        parser.add_argument(name, required=True)
    args = parser.parse_args()
    import export
    sources = export.Sources(args)
    target = Target(sources.corpora(), ledger_wordings(sources))
    hits, allowed, files = scan_tree(Path(args.tree), target)
    if args.git:
        hits += scan_git(Path(args.git), target)
    for name in args.also:
        hits += scan_also(Path(name), target)
    for hit in hits:
        print("  HIT", hit)
    print(f"leak gate: {files} files of {args.tree}, {len(args.also)} other files"
          f"{', commit messages and ref names' if args.git else ''}; {len(target.test_codes)} test-split scenarios, "
          f"{len(target.test_turns)} recorded test-split wordings ({len(target.shared)} shared word for word with a "
          f"dev-split instruction); {allowed} shared wordings at allowed positions; {len(hits)} hits; "
          f"{'PASS' if not hits else 'FAIL'}")
    return 0 if not hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
