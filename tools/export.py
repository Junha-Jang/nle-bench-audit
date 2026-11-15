"""Maintainer tool: derive the exported files of this repository from their sources.

    python tools/export.py build    --rc DIR --dev-repo DIR --artifact-repo DIR --private-map FILE
    python tools/export.py check    --rc DIR --dev-repo DIR --artifact-repo DIR --private-map FILE
    python tools/export.py finalize --private-map FILE

This is the only program here that needs anything outside the checkout, and a
reader of the repository cannot run it: its sources are not included here. It is
kept so that the derivation is written down and repeatable by the maintainer.

Sources
  --rc             the source package of 2026-10-06, identified by the SHA256
                   of its MANIFEST.json (RC_MANIFEST_SHA256). It is read, never
                   written.
  --dev-repo       a clone holding the commits of the development repository
                   `nle-bench-dev` named in PLAN. Only `git show` and
                   `git rev-parse` are run on it.
  --artifact-repo  a clone holding the review snapshot `nle-bench-artifact` at its pinned commit.
  --private-map    JSON kept outside the repository: the literal behind each
                   placeholder (local path prefixes, the account name, the names of
                   two folders of the development repository), the local account
                   names, the notes withheld by the projection and the spans removed
                   by `nlebench_audit.internal`.

build     writes every exported file, `runtime/PINS.json`, `provenance/SOURCE_MAP.json`
          and `provenance/TRANSFORMATIONS.json`. It never overwrites a file that
          exists with different bytes.
check     derives everything again in memory and compares it with the checkout,
          then compares each transformed copy with its original, step by step, by the
          typed rules of `nlebench_audit.projection`, `nlebench_audit.internal`,
          `nlebench_audit.sanitize` and `nlebench_audit.wording`. It writes nothing, unless
          --private-log names a file outside the checkout for the maintainer's record of every
          removed item.

What is exported of a file that names scenarios: its dev-split part only
(`nlebench_audit.projection`). The split of a scenario is read from the pinned
corpora. `expected.json` keeps its structure, with the tables recounted on the
dev split from the full source files.
finalize  measures every file of the checkout and writes `provenance/SCAN.json` and
          `MANIFEST.json`. Run it last, after any change.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nlebench_audit import internal, integrity, projection, sanitize, verify, wording  # noqa: E402

RC_MANIFEST_SHA256 = "ddbef96eef6d5b628da951e0423de9e8faf2d48cd6a0000269a35d62f807b3fa"
RC_NAME = "source package of 2026-10-06"
# Files of the accepted package that are not carried over. Each is replaced by something
# written for this repository; none of them holds a result.
RC_NOT_EXPORTED = {
    "README.md": "replaced by README.md of this repository",
    "RELEASE_INVENTORY.md": "a working inventory of the package; what this repository holds of the submitted "
                            "version's material is stated in inventory/promises/",
    "SCAN.json": "replaced by provenance/SCAN.json, measured on this checkout",
    "replay.py": "replaced by nlebench_audit/replay.py, which runs on the pinned modules held here",
}

DEV = "nle-bench-dev"
ARTIFACT = "nle-bench-artifact"
DEV_PIN = "206e1838224add4d7d685349ab1daaf4ab717c6f"
ARTIFACT_PIN = "58afb977515b6cc827377bd1de6cba56330c2d7b"

# The modules each replay command loads at each commit, as observed by tracing the commands
# on full trees of those commits (see runtime/README.md). Paths are relative to the source prefix.
CLOSURE_CASES = ("nlebench/__init__.py", "nlebench/config.py", "nlebench/models.py", "nlebench/protocols.py",
                 "nlebench/dataset/__init__.py", "nlebench/dataset/fixtures/__init__.py",
                 "nlebench/dataset/fixtures/json/simple_sequence.json", "nlebench/runner/__init__.py",
                 "nlebench/runner/constraints.py", "nlebench/runner/scenario_loader.py",
                 "nlebench/tools/__init__.py", "nlebench/tools/executor.py", "nlebench/tools/schema.py")
CLOSURE_FIXTURES = ("nlebench/runner/validator.py", "nlebench/dataset/fixtures/json/with_transitions.json")
CLOSURE_JUDGE = ("nlebench/__init__.py", "nlebench/config.py", "nlebench/models.py",
                 "nlebench/runner/__init__.py", "nlebench/runner/constraints.py")

PINS = {
    "dev-206e1838224a": {"repo": DEV, "commit": DEV_PIN, "prefix": "src/",
                         "files": CLOSURE_CASES + CLOSURE_FIXTURES,
                         "used_by": ["fixtures", "cases: stage current_pin of all three probe cases"]},
    "dev-28a5f11c6f26": {"repo": DEV, "commit": "28a5f11c6f26f3858dbc1c533e525b022b90801a", "prefix": "src/",
                         "files": CLOSURE_CASES,
                         "used_by": ["cases: stages code_axis_alone and post of NLB-v3-CX-CO-002 and NLB-v3-EX-DE-008"]},
    "dev-69f8d58a1382": {"repo": DEV, "commit": "69f8d58a1382b4014d12c13db1e73aa0dd18becc", "prefix": "src/",
                         "files": CLOSURE_CASES,
                         "used_by": ["cases: stage pre of NLB-v3-CX-CO-002 and NLB-v3-EX-DE-008"]},
    "dev-5d2f3a6175ad": {"repo": DEV, "commit": "5d2f3a6175ad3295a884535c578ce58b5c01893f", "prefix": "src/",
                         "files": CLOSURE_CASES, "used_by": ["cases: stage pre of NLB-v3-CX-AT-003"]},
    "artifact-58afb977515b": {"repo": ARTIFACT, "commit": ARTIFACT_PIN, "prefix": "code/src/",
                              "files": CLOSURE_JUDGE,
                              "used_by": ["stored-cases: the snapshot harness, under the snapshot scorer and "
                                          "with constraints.py replaced by scorers/current_constraints.py"]},
}

# The folder of the development repository that holds the probe scripts and ledgers. Its name is
# behind a placeholder (`sanitize`); `Sources.git_bytes` resolves it with the private map.
LEDGER_DIR = "<LEDGER_DIR>/"
PROBE_LINES = {"false_success_probe.py": (37, 44, 170), "false_success_probe_full.py": (37, 44, 45)}
# (destination, repository, commit, path, lines to rewrite or None)
GIT_FILES: list[tuple[str, str, str, str, tuple[int, ...] | None]] = []
for _commit, _name in (("5d2f3a6175ad3295a884535c578ce58b5c01893f", "false_success_probe.py"),
                       (DEV_PIN, "false_success_probe.py"),
                       ("28a5f11c6f26f3858dbc1c533e525b022b90801a", "false_success_probe_full.py"),
                       (DEV_PIN, "false_success_probe_full.py")):
    GIT_FILES.append((f"sources/probes/{_commit[:12]}/{_name}", DEV, _commit, LEDGER_DIR + _name, PROBE_LINES[_name]))
for _stage, _commit in (("S0_first_ledger", "5d2f3a6175ad3295a884535c578ce58b5c01893f"),
                        ("S1_after_fixture_deepcopy", "0b3a4adb7086a05caf01d5e156ef1c4085c7d408"),
                        ("S2_after_wave1", "c8be3e83aa0f24cc4039b9db23a852e918679677"),
                        ("S3_final", "28a5f11c6f26f3858dbc1c533e525b022b90801a")):
    GIT_FILES.append((f"sources/ledgers/probe_round1/{_stage}_{_commit[:12]}/false_success_probe.json", DEV, _commit,
                      LEDGER_DIR + "false_success_probe.json", None))
for _stage, _commit in (("T0_first_ledger", "28a5f11c6f26f3858dbc1c533e525b022b90801a"),
                        ("T1_after_immediate_closes", "942b45a19a5219abd98d382cb9321888cc0ef2e4"),
                        ("T2_final", "8d0ca7e0da61f9cb45887c60f13e6c6fa4786891")):
    GIT_FILES.append((f"sources/ledgers/probe_round2/{_stage}_{_commit[:12]}/false_success_probe_full.json", DEV,
                      _commit, LEDGER_DIR + "false_success_probe_full.json", None))
for _name in ("false_success_triage.json", "false_success_closeout.json", "constraint_hardening.json",
              "cluster_hardening.json"):
    GIT_FILES.append((f"sources/ledgers/adjudication/{DEV_PIN[:12]}/{_name}", DEV, DEV_PIN, LEDGER_DIR + _name, None))
# The folder of the development repository that holds its working records, behind a placeholder like LEDGER_DIR.
NOTES_DIR = "<NOTES_DIR>/"
for _path in (NOTES_DIR + "p1_2026-08/stored_row_rescore.json", NOTES_DIR + "roadmap_2026-08/contract_rescore_v32.json",
              NOTES_DIR + "roadmap_2026-08/constraint_addition_ledger.json",
              NOTES_DIR + "p1_2026-08/constraint_predicate_audit.json",
              NOTES_DIR + "roadmap_2026-08/preservation_annex.json"):
    GIT_FILES.append((f"sources/ledgers/records_2026_08/{DEV_PIN[:12]}/{_path.rsplit('/', 1)[1]}", DEV, DEV_PIN,
                      _path, None))


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


class Sources:
    def __init__(self, args) -> None:
        self.rc = Path(args.rc)
        self.repos = {DEV: Path(args.dev_repo), ARTIFACT: Path(args.artifact_repo)}
        self.private = sanitize.PrivateMap.load(args.private_map)
        unregistered = projection.unregistered_withheld(self.private.withheld_notes)
        if unregistered:
            raise SystemExit(f"withheld notes are listed for files without a withheld_note rule: {unregistered}")
        manifest = self.rc / "MANIFEST.json"
        if integrity.sha256_file(manifest) != RC_MANIFEST_SHA256:
            raise SystemExit("--rc is not the accepted package: its MANIFEST.json has a different hash")
        self.rc_files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
        self._corpora = None

    def corpora(self) -> "Corpora":
        if self._corpora is None:
            self._corpora = read_corpora(self.repos)
        return self._corpora

    def splits(self) -> projection.Splits:
        return self.corpora().splits

    def rc_bytes(self, name: str) -> bytes:
        data = (self.rc / name).read_bytes()
        if integrity.sha256_bytes(data) != self.rc_files[name]["sha256"]:
            raise SystemExit(f"the accepted package is not intact: {name}")
        return data

    def git_bytes(self, repo: str, commit: str, path: str) -> tuple[bytes, str]:
        """The blob at `path`, which may hold a placeholder; the source map records it as given."""
        blob = git(self.repos[repo], "rev-parse", f"{commit}:{self.private.backward(path)}").decode().strip()
        return git(self.repos[repo], "cat-file", "blob", blob), blob


REAGGREGATE = "dev_split_reaggregate"
def origin_words(text: str, pairs: list[tuple[str, str]], used: set[int]) -> str:
    """A package manifest's description of a file's origin, as SOURCE_MAP.json writes it: the words listed in the
    private map (`origin_words`) replaced. The indexes of the pairs that applied are added to `used`."""
    for index, (old, new) in enumerate(pairs):
        if old in text:
            text = text.replace(old, new)
            used.add(index)
    return text


def dev_tables(per_record_gz: bytes, candidate_keys: bytes, splits: projection.Splits, keys: dict) -> dict:
    """The tables of expected.json on the dev split, counted from the full private files.

    Written apart from `nlebench_audit.replay.count_records`, which recounts the exported
    dev-split files in a checkout; the two must agree. `keys` gives the names of the original
    tables, which the dev tables keep (zero where the dev split has none)."""
    import csv
    import io
    records: dict[str, int] = {}

    def bump(key: str, by: int = 1) -> None:
        records[key] = records.get(key, 0) + by

    rows = csv.reader(io.StringIO(gzip.decompress(per_record_gz).decode("utf-8")))
    header = next(rows)
    at = {column: header.index(column) for column in header}
    group_of = {"constraints_only": "B", "input_changed": "C", "unchanged": "U"}
    for row in rows:
        codes = splits.named(row[at["scenario_id"]])
        if {splits.of[code] for code in codes} != {"dev"}:
            continue
        value = lambda column: row[at[column]]  # noqa: E731
        bump("records")
        bump("disposition_" + value("d32_disposition"))
        bump("published_verdict_reproduced", int(value("sub_official") == value("published_success")))
        bump("A_prime_changed", int(value("sub_official_unenforced") != value("sub_official")))
        if value("arm") != "feasible":
            continue
        bump("feasible")
        for label, column in (("sub", "sub_official"), ("new", "new_official"), ("dev_v31", "d31s_success"),
                              ("dev_v32", "d32_success")):
            bump("success_" + label, int(value(column) == "True"))
        for label, before, after in (("A", "sub_official", "new_official"), ("H", "new_official", "d31p_success"),
                                     ("BC", "d31s_success", "d32_success")):
            if value(before) != value(after):
                bump(label + "_changed")
                bump(label + ("_fail_to_pass" if value(after) == "True" else "_pass_to_fail"))
        group = group_of[value("membership_v31s_to_v32")]
        bump(group + "_feasible")
        bump(group + "_rejudged", int(value("d31s_disposition") == "S" and value("d32_disposition") == "S"))
        if value("d31s_success") != value("d32_success"):
            bump(group + "_changed")
            bump(group + ("_fail_to_pass" if value("d32_success") == "True" else "_pass_to_fail"))
    sites: dict[str, int] = {}
    table = csv.reader(io.StringIO(candidate_keys.decode("utf-8")))
    header = next(table)
    for row in table:
        item = dict(zip(header, row))
        if {splits.of[code] for code in splits.named(item["scenario"])} != {"dev"}:
            continue
        for key in ("sites", "cause_" + item["cause"], item["cause"] + "|" + item["disposition"]):
            sites[key] = sites.get(key, 0) + 1
    extra = sorted(set(records) - set(keys["stored_records"])) + sorted(set(sites) - set(keys["sites"]))
    if extra:
        raise SystemExit(f"the dev tables hold a count the original tables do not name: {extra}")
    return {"stored_records": {key: records.get(key, 0) for key in keys["stored_records"]},
            "sites": {key: sites.get(key, 0) for key in keys["sites"]}}


def reaggregate(document: dict, sources: "Sources") -> tuple[dict, list[dict]]:
    """expected.json with its tables counted on the dev split; every other value unchanged."""
    import copy
    tables = dev_tables(sources.rc_bytes("rejudging/per_record.csv.gz"), sources.rc_bytes("sites/candidate_keys.csv"),
                        sources.splits(), document["tables"])
    out = copy.deepcopy(document)
    out["tables"] = tables
    return out, [{"rule": REAGGREGATE, "position": "tables",
                  "original_sha256": integrity.sha256_bytes(integrity.dump_json(document["tables"])),
                  "basis": ("the dev-split rows of rejudging/per_record.csv.gz and sites/candidate_keys.csv of the "
                            "accepted package, counted by tools/export.py:dev_tables")}]


def transform(name: str, original: bytes, sources: "Sources") -> tuple[bytes, str | None, list | None, list]:
    """The derived copy of a file, its transformation (steps joined by "+"), the log kept in the repository and
    the private log of removed items. JSON steps, in order: the dev-split projection, the dev
    recount of expected.json, local paths, descriptive wording; the copy keeps the serialisation
    style of its source."""
    rules = projection.PROJECTION.get(name)
    if rules and rules[0][0] == projection.ROWS:
        derived, log, detail = projection.project_rows(name, original, sources.splits())
        return derived, projection.TRANSFORMATION, log, detail
    if not name.endswith(".json"):
        return original, None, None, []
    text = original.decode("utf-8")
    document = json.loads(text)
    steps, changes, detail = [], [], []
    if rules:
        document, log, detail = projection.project_json(name, document, sources.splits(),
                                                        sources.private.withheld(name))
        steps.append(projection.TRANSFORMATION)
        changes += log
    if name == "expected.json":
        document, log = reaggregate(document, sources)
        steps.append(REAGGREGATE)
        changes += log
    if name in internal.MESSAGES:
        detail += [{"rule": internal.REMOVED_TEXT, "text": text} for text in internal.message_texts(name, document)]
        document, log = internal.drop_messages(name, document)
        steps.append(internal.MESSAGE)
        changes += log
    listed = sources.private.references(name)
    if listed:
        detail += [{"rule": internal.REMOVED_TEXT, "text": text} for text in internal.span_texts(name, document, listed)]
        document, log = internal.apply_references(name, document, listed)
        steps.append(internal.REFERENCE)
        changes += log
    if name in sanitize.JSON_POSITIONS:
        document, log = sanitize.apply_json(name, document, sources.private)
        if log:
            steps.append("json_local_paths")
            changes += log
    document, log = wording.apply(name, document)
    if log:
        steps.append(wording.TRANSFORMATION)
        changes += log
    if not steps:
        return original, None, None, []
    return projection.dumps_like(text, document).encode("utf-8"), "+".join(steps), changes, detail


def derive(sources: Sources) -> tuple[dict[str, bytes], list[dict], dict, dict, dict[str, bytes], list[dict]]:
    """Every exported file, the source map, the transformation log, the pin table, the originals
    of the transformed files and the private log of removed items."""
    files: dict[str, bytes] = {}
    source_map: list[dict] = []
    transformations: dict[str, dict] = {}
    originals: dict[str, bytes] = {}
    private_log: list[dict] = []
    origin_used: set[int] = set()

    def add(destination: str, data: bytes, original: bytes, transformation, changes, source: dict) -> None:
        if destination in files:
            raise SystemExit(f"two sources for {destination}")
        files[destination] = data
        source_map.append({"destination": destination, "destination_sha256": integrity.sha256_bytes(data),
                           "destination_bytes": len(data), "source": source,
                           "source_sha256": integrity.sha256_bytes(original), "source_bytes": len(original),
                           "transformation": transformation})
        if transformation:
            transformations[destination] = {"transformation": transformation, "changes": changes}
            originals[destination] = original

    for name in sorted(sources.rc_files):
        if name in RC_NOT_EXPORTED:
            continue
        original = sources.rc_bytes(name)
        source = {"kind": "source_package", "package_manifest_sha256": RC_MANIFEST_SHA256, "path": name,
                  "recorded_origin": origin_words(sources.rc_files[name]["source"], sources.private.origin_words,
                                                  origin_used)}
        if name in sanitize.YAML_ANNOTATOR_LINES:
            derived, log = sanitize.apply_lines(name, original, (sanitize.YAML_ANNOTATOR_LINES[name],),
                                                sources.private)
            for entry in log:
                entry["parsed_position"] = sanitize.ANNOTATOR_POSITION
            add(name, derived, original, "yaml_annotator_metadata", log, source)
            continue
        derived, transformation, log, detail = transform(name, original, sources)
        private_log.extend({"file": name, **entry} for entry in detail)
        add(name, derived, original, transformation, log, source)

    pins = {}
    for pin_name, pin in PINS.items():
        listed = []
        for path in pin["files"]:
            data, blob = sources.git_bytes(pin["repo"], pin["commit"], pin["prefix"] + path)
            destination = f"runtime/pins/{pin_name}/{path}"
            add(destination, data, data, None, None, {"kind": "git", "repository": pin["repo"],
                                                      "commit": pin["commit"], "path": pin["prefix"] + path,
                                                      "blob": blob})
            listed.append({"path": path, "sha256": integrity.sha256_bytes(data), "bytes": len(data), "blob": blob})
        pins[pin_name] = {"repository": pin["repo"], "commit": pin["commit"], "source_prefix": pin["prefix"],
                          "used_by": pin["used_by"], "files": sorted(listed, key=lambda item: item["path"])}

    for destination, repo, commit, path, lines in GIT_FILES:
        original, blob = sources.git_bytes(repo, commit, path)
        source = {"kind": "git", "repository": repo, "commit": commit, "path": path, "blob": blob}
        if lines:
            derived, log = sanitize.apply_lines(destination, original, lines, sources.private)
            listed = sources.private.references(destination)
            if listed:
                private_log.extend({"file": destination, "rule": internal.REMOVED_TEXT, "text": text}
                                   for text in internal.line_texts(destination, derived, listed))
                derived, more = internal.apply_reference_lines(destination, derived, listed)
                dropped = sorted(number for number, entry in listed.items() if entry.get("drop"))
                for entry in log:
                    entry["line_in_copy"] = entry["line"] - sum(1 for number in dropped if number < entry["line"])
                add(destination, derived, original, "text_path_lines+" + internal.REFERENCE, log + more, source)
            else:
                add(destination, derived, original, "text_path_lines", log, source)
            continue
        derived, transformation, log, detail = transform(destination, original, sources)
        private_log.extend({"file": destination, **entry} for entry in detail)
        add(destination, derived, original, transformation, log, source)

    absent = sorted(set(RC_NOT_EXPORTED) - set(sources.rc_files))
    if absent:
        raise SystemExit(f"listed as not exported but not in the accepted package: {absent}")
    unregistered = sorted(set(projection.PROJECTION) - set(files))
    if unregistered:
        raise SystemExit(f"registered for the projection but not exported: {unregistered}")
    unapplied = sorted(name for name in sources.private.internal_references
                       if internal.REFERENCE not in (transformations.get(name) or {}).get("transformation", "").split("+"))
    if unapplied:
        raise SystemExit(f"references are listed for files that do not take them: {unapplied}")
    unused = sorted(set(range(len(sources.private.origin_words))) - origin_used)
    if unused:
        raise SystemExit(f"origin words are listed but never apply: entries {unused}")
    unmessaged = sorted(name for name in internal.MESSAGES
                        if internal.MESSAGE not in (transformations.get(name) or {}).get("transformation", "").split("+"))
    if unmessaged:
        raise SystemExit(f"registered for commit messages but not transformed: {unmessaged}")
    return files, source_map, transformations, pins, originals, private_log


def generated(source_map: list[dict], transformations: dict, pins: dict, private: sanitize.PrivateMap) -> dict[str, bytes]:
    not_exported = [{"path": name, "why": why} for name, why in sorted(RC_NOT_EXPORTED.items())]
    scenario_files = {}
    for entry in source_map:
        name = entry["destination"]
        if name.startswith("cases/") and name.endswith(".yaml"):
            scenario_files[name] = {
                "source_sha256": entry["source_sha256"],
                "sha256_in_this_repository": entry["destination_sha256"],
                "identical_to_source": entry["transformation"] is None,
                "differs_only_at": None if entry["transformation"] is None else sanitize.ANNOTATOR_POSITION,
            }
    return {
        "cases/SCENARIO_IDENTITIES.json": integrity.dump_json({
            "what": ("the scenario files of the worked cases: the hash of each source file, which is the "
                     "identity the 2026-10 runs and expected.json record, and the hash of the file held here. "
                     "Where they differ, the file differs from its source in one metadata value, the "
                     "annotator's account name, replaced by a placeholder; fixture, constraints and turns "
                     "are the same"),
            "files": scenario_files}),
        "runtime/PINS.json": integrity.dump_json({
            "what": "the benchmark modules held for replay, by commit; every file is the byte-identical git blob",
            "pins": pins}),
        "provenance/SOURCE_MAP.json": integrity.dump_json({
            "what": ("every exported file with its source, both hashes and whether it was transformed. "
                     "recorded_origin is the package manifest's description of the file's origin; descriptive "
                     "words in it are written as a list kept outside the repository gives them"),
            "source_package": {"name": RC_NAME, "manifest_sha256": RC_MANIFEST_SHA256,
                                         "files_not_exported": not_exported,
                                         "also_not_exported": "its MANIFEST.json, replaced by MANIFEST.json here"},
            "files": sorted(source_map, key=lambda item: item["destination"])}),
        "provenance/TRANSFORMATIONS.json": integrity.dump_json({
            "what": ("how each exported copy differs from its source. A replaced local path, folder name or account "
                     "name, and a value whose descriptive wording was replaced, carries the hash of the original "
                     "string. A dev-split projection is recorded by its rule, pattern and the number of items it "
                     "removed, not by the items. The recount of expected.json names the tables it replaced. Dropped "
                     "commit messages are counted by pattern, and removed references to material outside this record "
                     "by file, without their text or position"),
            "placeholders": private.placeholders(),
            "descriptive_wording": [{"replacement": new, "original_sha256": integrity.sha256_bytes(old.encode("utf-8"))}
                                    for old, new in wording.PHRASES],
            "files": transformations}),
    }


def private_scan(files: dict[str, bytes], private: sanitize.PrivateMap) -> dict[str, int]:
    out = {}
    for name, data in files.items():
        text = (gzip.decompress(data) if name.endswith(".gz") else data).decode("utf-8", "replace")
        hits = private.private_hits(text)
        if hits:
            out[name] = hits
    return out


def unexpected_local_strings(files: dict[str, bytes], private: sanitize.PrivateMap) -> list[str]:
    """Files that hold a local path, a local account name or another literal of the private map. None is
    expected anywhere."""
    problems = []
    for name, hits in sorted(private_scan(files, private).items()):
        problems.append(f"local account name in {name} ({hits})")
    for name, data in sorted(files.items()):
        if private.literal_hits((gzip.decompress(data) if name.endswith(".gz") else data).decode("utf-8", "replace")):
            problems.append(f"a literal of the private map in {name}")
        counts = verify.scan_bytes(name, data)
        if counts.get("home_path") or counts.get("tmp_path"):
            problems.append(f"local path in {name}")
    return problems


def reappearing(files: dict[str, bytes], private_log: list[dict]) -> list[str]:
    """Files that still hold a text that a commit-message or reference rule removed: in a later log, a generated
    file or any other copy. Texts shorter than eight characters are not searched."""
    texts = {entry["text"].strip() for entry in private_log if entry.get("rule") == internal.REMOVED_TEXT}
    texts = {text for text in texts if len(text) >= 8}
    problems = []
    for name, data in sorted(files.items()):
        content = (gzip.decompress(data) if name.endswith(".gz") else data).decode("utf-8", "replace")
        flat = json.dumps(json.loads(content), ensure_ascii=False) if name.endswith(".json") else content
        hits = sum(1 for text in texts if text in content or json.dumps(text, ensure_ascii=False)[1:-1] in flat)
        if hits:
            problems.append(f"a removed text appears again in {name} ({hits})")
    return problems


def command_build(args) -> int:
    sources = Sources(args)
    files, source_map, transformations, pins, _, private_log = derive(sources)
    files.update(generated(source_map, transformations, pins, sources.private))
    again = reappearing(files, private_log)
    if again:
        raise SystemExit("\n".join(again))
    clash = [name for name, data in files.items() if (ROOT / name).is_file() and (ROOT / name).read_bytes() != data]
    if clash and not args.replace_derived:
        print("these files exist with different bytes; nothing was written (--replace-derived rewrites them):")
        for name in clash[:30]:
            print("  ", name)
        return 1
    written = 0
    for name, data in sorted(files.items()):
        path = ROOT / name
        if not path.is_file() or name in clash:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            written += 1
    for name in clash:
        print("  rewritten:", name)
    print(f"build: {len(files)} files derived, {written} written, {len(files) - written} already identical")
    return 0


def check_steps(name: str, original: bytes, derived: bytes, transformation: str, changes: list,
                sources: Sources) -> list[str]:
    """Compare a JSON copy with its original step by step. Each intermediate document is derived
    again; each step is then checked by comparing its input and its output, not by re-running it."""
    steps = transformation.split("+")
    text = original.decode("utf-8")
    current = json.loads(text)
    problems = []
    by_rule = {
        projection.TRANSFORMATION: [c for c in changes if c.get("rule") == projection.TRANSFORMATION],
        REAGGREGATE: [c for c in changes if c.get("rule") == REAGGREGATE],
        "json_local_paths": [c for c in changes if c.get("rule") in ("prefix", "whole_value")],
        wording.TRANSFORMATION: [c for c in changes if c.get("rule") == wording.TRANSFORMATION],
        internal.MESSAGE: [c for c in changes if c.get("rule") == internal.MESSAGE],
        internal.REFERENCE: [c for c in changes if c.get("rule") == internal.REFERENCE],
    }
    if sum(len(v) for v in by_rule.values()) != len(changes):
        problems.append(f"{name}: a logged change belongs to no step")
    for step in steps:
        if step == projection.TRANSFORMATION:
            after = projection.project_json(name, current, sources.splits(), sources.private.withheld(name))[0]
            problems += projection.check_json(name, current, after, sources.splits(), by_rule[step],
                                              sources.private.withheld(name))
            logged = [(c["kind"], c["pattern"]) for c in by_rule[step]]
            if logged != list(projection.PROJECTION[name]):
                problems.append(f"{name}: the projection log does not list the registered rules in order")
        elif step == REAGGREGATE:
            after = reaggregate(current, sources)[0]
            outside = [p for p in integrity.differing_leaves(current, after) if p[:1] != ("tables",)]
            if outside:
                problems.append(f"{name}: the recount changed values outside the tables: {outside[:3]}")
            if not integrity.same_json({k: sorted(v) for k, v in current["tables"].items()},
                                       {k: sorted(v) for k, v in after["tables"].items()}):
                problems.append(f"{name}: the recounted tables do not keep the names of the original tables")
        elif step == "json_local_paths":
            after = sanitize.apply_json(name, current, sources.private)[0]
            problems += sanitize.check_json(name, current, after, sources.private, by_rule[step])
        elif step == wording.TRANSFORMATION:
            after = wording.apply(name, current)[0]
            problems += wording.check(name, current, after, by_rule[step])
        elif step == internal.MESSAGE:
            after = internal.drop_messages(name, current)[0]
            problems += internal.check_messages(name, current, after, by_rule[step])
        elif step == internal.REFERENCE:
            listed = sources.private.references(name)
            after = internal.apply_references(name, current, listed)[0]
            problems += internal.check_references(name, current, after, listed, by_rule[step])
        else:
            problems.append(f"{name}: unknown step {step}")
            return problems
        current = after
    if projection.dumps_like(text, current).encode("utf-8") != derived:
        problems.append(f"{name}: the copy is not the checked document in the style of its source")
    return problems


def command_check(args) -> int:
    sources = Sources(args)
    files, source_map, transformations, pins, originals, private_log = derive(sources)
    expected = dict(files)
    expected.update(generated(source_map, transformations, pins, sources.private))
    problems = []
    for name, data in sorted(expected.items()):
        path = ROOT / name
        if not path.is_file():
            problems.append(f"missing: {name}")
        elif path.read_bytes() != data:
            problems.append(f"differs from its derivation: {name}")
    for folder in ("scorers", "cases", "sites", "rejudging", "checks", "fixtures", "runtime/pins", "sources"):
        for path in (ROOT / folder).rglob("*"):
            relative = path.relative_to(ROOT).as_posix()
            if path.is_file() and "__pycache__" not in path.parts and relative not in expected \
                    and path.name != "README.md":
                problems.append(f"not derived from any source: {relative}")
    typed = 0
    for name, item in sorted(transformations.items()):
        derived = (ROOT / name).read_bytes() if (ROOT / name).is_file() else b""
        kind = item["transformation"]
        if kind == projection.TRANSFORMATION and projection.PROJECTION[name][0][0] == projection.ROWS:
            problems += projection.check_rows(name, originals[name], derived, sources.splits(), item["changes"])
        elif kind == "yaml_annotator_metadata":
            problems += sanitize.check_yaml_metadata(name, originals[name], derived, sources.private,
                                                     item["changes"])
        elif kind in ("text_path_lines", "text_path_lines+" + internal.REFERENCE):
            lines = next(entry[4] for entry in GIT_FILES if entry[0] == name)
            paths = [c for c in item["changes"] if c.get("rule") != internal.REFERENCE]
            if kind == "text_path_lines":
                problems += sanitize.check_lines(name, originals[name], derived, lines, sources.private, paths)
            else:
                listed = sources.private.references(name)
                middle = sanitize.apply_lines(name, originals[name], lines, sources.private)[0]
                problems += sanitize.check_lines(name, originals[name], middle, lines, sources.private, paths)
                problems += internal.check_reference_lines(
                    name, middle, derived, listed, [c for c in item["changes"] if c.get("rule") == internal.REFERENCE])
        else:
            problems += check_steps(name, originals[name], derived, kind, item["changes"], sources)
        typed += 1
    registered = {name for name in projection.PROJECTION}
    projected = {name for name, item in transformations.items()
                 if projection.TRANSFORMATION in item["transformation"].split("+")}
    if registered != projected:
        problems.append(f"files registered for the projection and files projected differ: "
                        f"{sorted(registered ^ projected)}")
    problems += unexpected_local_strings(expected, sources.private)
    problems += reappearing(expected, private_log)
    for name, data in sorted(expected.items()):
        text = (gzip.decompress(data) if name.endswith(".gz") else data).decode("utf-8", "replace")
        if any(sources.splits().of.get(code) == "test" for code in sources.splits().named(text)):
            problems.append(f"names a test-split scenario: {name}")
        if name.endswith(".json") and not name.startswith("runtime/pins/"):
            for where in wording.residue(json.loads(text)):
                problems.append(f"{name}: a descriptive phrase of the earlier wording remains at {where}")
    if args.private_log:
        target = Path(args.private_log).resolve()
        if ROOT in target.parents:
            problems.append("--private-log must name a file outside the checkout")
        else:
            target.write_bytes(integrity.dump_json({"what": "every item the dev-split projection removed; private",
                                                    "items": private_log}))
    unchanged = sum(1 for entry in source_map if entry["transformation"] is None)
    removed = sum(change.get("removed", 0) for item in transformations.values() for change in item["changes"]
                  if change.get("rule") == projection.TRANSFORMATION)
    for problem in problems:
        print("  FAIL", problem)
    print(f"check: {len(files)} exported files ({unchanged} byte-identical to their source, {typed} transformed "
          f"and compared step by step); {removed} test-split items removed in {len(projected)} files; "
          f"{'PASS' if not problems and files and typed else 'FAIL'}")
    return 0 if not problems and files and typed else 1


# the older corpus, read for its dev/test split and its wordings only (the 2026-08 predicate audit names its scenarios)
OLDER_CORPORA = (("older_v2", DEV, DEV_PIN, "src/nlebench/dataset/scenarios_v2"),)
CORPORA = (("published", ARTIFACT, ARTIFACT_PIN, "code/src/nlebench/dataset/scenarios_v3_1"),
           ("submitted_v3_1", DEV, "64668e09fb2fa4c2d1d0c03024191e94046f24b2", "src/nlebench/dataset/scenarios_v3_1"),
           ("development_v3_2", DEV, DEV_PIN, "src/nlebench/dataset/scenarios_v3_1"))


class Corpora:
    """What the export needs from the corpora at the three pins: the split of every scenario,
    and every instruction wording of every scenario (for the leak gate and the content review)."""

    def __init__(self, split_of: dict[str, str], wordings: dict[str, set[str]], read: list[dict]) -> None:
        self.split_of, self.wordings, self.read = split_of, wordings, read
        self.splits = projection.Splits(split_of)


def read_corpora(repos: dict[str, Path]) -> Corpora:
    import io
    import tarfile
    import tempfile

    import yaml
    split_of: dict[str, str] = {}
    wordings: dict[str, set[str]] = {}
    read = []
    for label, repo, commit, folder in CORPORA + OLDER_CORPORA:
        archive = git(repos[repo], "archive", "--format=tar", commit, folder)
        count = 0
        with tempfile.TemporaryDirectory() as tmp:
            tarfile.open(fileobj=io.BytesIO(archive)).extractall(tmp, filter="data")
            for path in sorted(Path(tmp).rglob("*.yaml")):
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
                key = str(doc.get("legacy_id") or doc["id"])
                split = str(doc.get("split"))
                if split_of.setdefault(key, split) != split:
                    raise SystemExit(f"scenario {key} has two different splits across the corpora")
                for turn in doc.get("turns") or []:
                    text = str(turn.get("instruction") or "")
                    if text:
                        wordings.setdefault(key, set()).add(text)
                count += 1
        read.append({"corpus": label, "repository": repo, "commit": commit, "scenario_files": count})
    return Corpora(split_of, wordings, read)


# Model response text that is kept, with the reason. Each file was read in full by the maintainer.
RESPONSES_KEPT = {
    "cases/NLB-v3-EX-CO-004/states/agent_response.txt":
        "kept: the stored response of the dev-split run this worked case judges; case.json records its hash. "
        "Read in full: task reasoning and three tool calls; no personal, contact or credential content.",
    "cases/NLB-v3-EX-CO-009/states/agent_response.txt":
        "kept: the stored response of the dev-split run this worked case judges; case.json records its hash. "
        "Read in full: task reasoning and two tool calls; no personal, contact or credential content.",
}
MIN_QUOTE = 30


def _flat(text: str) -> str:
    return " ".join(text.split())


def _file_text(name: str, data: bytes) -> str:
    """The text of a file as a reader sees it: JSON strings unescaped, gzip opened."""
    if name.endswith(".gz"):
        data = gzip.decompress(data)
    text = data.decode("utf-8", "replace")
    if name.endswith(".json"):
        try:
            text += "\n" + "\n".join(value for _, value in integrity.string_leaves(json.loads(text)))
        except ValueError:
            pass
    return _flat(text)


def command_content_review(args) -> int:
    """Measure, with the corpora at the three pins, which files quote scenario instructions and
    name scenarios of which split. Writes provenance/CONTENT_REVIEW.json. It reads the corpora
    from git objects into a temporary directory and keeps none of them."""
    import io
    import tarfile
    import tempfile

    import yaml
    private = sanitize.PrivateMap.load(args.private_map)
    repos = {DEV: Path(args.dev_repo), ARTIFACT: Path(args.artifact_repo)}
    split_of: dict[str, str] = {}
    instructions: dict[str, set[str]] = {}
    published_text: dict[str, set[str]] = {}
    corpora = []
    for label, repo, commit, folder in CORPORA:
        archive = git(repos[repo], "archive", "--format=tar", commit, folder)
        count = 0
        with tempfile.TemporaryDirectory() as tmp:
            tarfile.open(fileobj=io.BytesIO(archive)).extractall(tmp, filter="data")
            for path in sorted(Path(tmp).rglob("*.yaml")):
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
                key = str(doc.get("legacy_id") or doc["id"])
                count += 1
                split = str(doc.get("split"))
                if split_of.setdefault(key, split) != split:
                    raise SystemExit(f"scenario {key} has two different splits across the corpora")
                for turn in doc.get("turns") or []:
                    text = _flat(str(turn.get("instruction") or ""))
                    if len(text) >= MIN_QUOTE:
                        instructions.setdefault(key, set()).add(text)
                        if label == "published":
                            published_text.setdefault(key, set()).add(text)
        corpora.append({"corpus": label, "repository": repo, "commit": commit, "scenario_files": count})
    names = [name for name in integrity.checkout_files(ROOT)
             if name not in ("provenance/CONTENT_REVIEW.json", "provenance/SCAN.json", "MANIFEST.json")]
    per_file = {}
    quoted_all: dict[str, set[str]] = {"dev": set(), "test": set()}
    named_all: dict[str, set[str]] = {"dev": set(), "test": set()}
    beyond_all: dict[str, set[str]] = {"dev": set(), "test": set()}
    annotators: dict[str, list] = {}
    for name in names:
        data = (ROOT / name).read_bytes()
        text = _file_text(name, data)
        quoted = {key for key, texts in instructions.items() if any(item in text for item in texts)}
        # quoted text that is not the instruction of the same scenario in the review snapshot
        beyond = {key for key in quoted
                  if any(item in text for item in instructions[key] - published_text.get(key, set()))}
        named = set(integrity.SCAN_PATTERNS["scenario_id"].findall(text)) & set(split_of)
        entry = {}
        for label, keys, total in (("quotes_instruction_of", quoted, quoted_all),
                                   ("quotes_instruction_text_not_in_the_review_snapshot", beyond, beyond_all),
                                   ("names_scenarios", named, named_all)):
            by_split = {split: sorted(key for key in keys if split_of[key] == split) for split in ("dev", "test")}
            for split in ("dev", "test"):
                total[split] |= set(by_split[split])
            if keys:
                entry[label] = {split: len(by_split[split]) for split in ("dev", "test")}
        if name.endswith((".yaml", ".yml")):
            doc = yaml.safe_load(data.decode("utf-8"))
            values = (doc.get("metadata") or {}).get("annotators") if isinstance(doc, dict) else None
            if values is not None:
                annotators[name] = values
        if entry:
            per_file[name] = entry
    account_files = private_scan({name: (ROOT / name).read_bytes() for name in names}, private)
    review = {
        "what": ("a content review of the files of this checkout, measured against the scenario corpora at the "
                 "three pins. It records what was examined and how; it is not a privacy certificate"),
        "method": {
            "corpora_read": corpora,
            "scenarios_known": len(split_of),
            "split_counts": {split: sum(1 for value in split_of.values() if value == split)
                             for split in sorted(set(split_of.values()))},
            "instruction_quote": (f"a file quotes a scenario when an instruction of that scenario in any of the three "
                                  f"corpora, of at least {MIN_QUOTE} characters after collapsing white space, occurs "
                                  "verbatim in the file's text (JSON strings unescaped, gzip opened)"),
            "scenario_named": "an identifier of the form NLB-v3-XX-XX-NNN that is a scenario of the corpora",
            "files_examined": len(names),
        },
        "totals": {
            "files_quoting_an_instruction": sum(1 for entry in per_file.values() if "quotes_instruction_of" in entry),
            "scenarios_quoted": {split: len(keys) for split, keys in quoted_all.items()},
            "scenarios_quoted_with_text_not_in_the_review_snapshot":
                {split: len(keys) for split, keys in beyond_all.items()},
            "files_naming_scenarios": sum(1 for entry in per_file.values() if "names_scenarios" in entry),
            "scenarios_named": {split: len(keys) for split, keys in named_all.items()},
            "files_holding_a_local_account_name": len(account_files),
        },
        "files": per_file,
        "annotator_values_in_scenario_files": annotators,
        "model_responses_kept": RESPONSES_KEPT,
        "not_examined": [
            "Quotes shorter than the threshold, paraphrases, and scenario text other than instructions "
            "(constraint values, fixture names, clip and effect identifiers) are not detected by the quote measure.",
            "The Korean notes of the ledgers and tables were not translated or read line by line; they were "
            "searched for the patterns of provenance/SCAN.json and for the local account name only.",
            "No person other than the maintainer read the files.",
        ],
    }
    (ROOT / "provenance" / "CONTENT_REVIEW.json").write_bytes(integrity.dump_json(review))
    print(f"content-review: {len(names)} files; quoting an instruction: {review['totals']['files_quoting_an_instruction']} "
          f"files, scenarios quoted {review['totals']['scenarios_quoted']}; scenarios named "
          f"{review['totals']['scenarios_named']}; files with a local account name: {len(account_files)}")
    return 0


def command_finalize(args) -> int:
    private = sanitize.PrivateMap.load(args.private_map)
    source_map = json.loads((ROOT / "provenance" / "SOURCE_MAP.json").read_text(encoding="utf-8"))["files"]
    exported = {entry["destination"] for entry in source_map}
    names = [name for name in integrity.checkout_files(ROOT) if name not in ("provenance/SCAN.json", "MANIFEST.json")]
    account_hits = private_scan({name: (ROOT / name).read_bytes() for name in names}, private)
    generic = verify.scan_checkout(ROOT)
    totals: dict[str, int] = {}
    for counts in generic.values():
        for key in counts:
            totals[key] = totals.get(key, 0) + 1
    scan = {
        "what": ("counts of generic patterns in every file, measured on this checkout; and, measured at export "
                 "time with a list kept outside the repository, the files that still hold a local account name"),
        "generic_patterns": generic,
        "files_with_each_pattern": dict(sorted(totals.items())),
        "local_account_name": {"files": dict(sorted(account_hits.items())),
                               "note": "counts only; the name itself is not recorded here"},
    }
    (ROOT / "provenance" / "SCAN.json").write_bytes(integrity.dump_json(scan))
    measured = integrity.measure(ROOT)
    for name, item in measured.items():
        item["origin"] = ("exported" if name in exported else "generated" if name in verify.GENERATED
                          else "written for this repository")
    manifest = {"what": "every file of the checkout except this one, measured",
                "files": measured,
                "counts": {origin: sum(1 for item in measured.values() if item["origin"] == origin)
                           for origin in ("exported", "generated", "written for this repository")},
                "total_bytes": sum(item["bytes"] for item in measured.values())}
    (ROOT / "MANIFEST.json").write_bytes(integrity.dump_json(manifest))
    print(f"finalize: {len(measured)} files, {manifest['total_bytes']} bytes, {manifest['counts']}; "
          f"files holding a local account name: {len(account_hits)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "check"):
        one = sub.add_parser(name)
        one.add_argument("--rc", required=True)
        one.add_argument("--dev-repo", required=True)
        one.add_argument("--artifact-repo", required=True)
        one.add_argument("--private-map", required=True)
        if name == "build":
            one.add_argument("--replace-derived", action="store_true",
                             help="rewrite derived files that exist with different bytes")
        else:
            one.add_argument("--private-log", help="write every removed item here (outside the checkout)")
    sub.add_parser("finalize").add_argument("--private-map", required=True)
    review = sub.add_parser("content-review")
    review.add_argument("--dev-repo", required=True)
    review.add_argument("--artifact-repo", required=True)
    review.add_argument("--private-map", required=True)
    args = parser.parse_args()
    return {"build": command_build, "check": command_check, "finalize": command_finalize,
            "content-review": command_content_review}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
