"""Integrity of the checkout, from the checkout alone.

`verify` answers: are the files here the files the records describe, and do
the records agree with each other? It does not and cannot answer whether an
exported file equals its source; that comparison needs the sources, which are not included here, and
is the maintainer's `tools/export.py check`.
"""
from __future__ import annotations

import gzip
from pathlib import Path

from . import integrity, internal, sanitize
from . import projection, wording
from .replay import Report, load_json

GENERATED = ("MANIFEST.json", "runtime/PINS.json", "cases/SCENARIO_IDENTITIES.json",
             "provenance/CONTENT_REVIEW.json", "provenance/SOURCE_MAP.json",
             "provenance/TRANSFORMATIONS.json", "provenance/SCAN.json")
PLACEHOLDERS = ("<EXPORT_ROOT_FULL>", "<EXPORT_ROOT>", "<ANALYSIS_CHECKOUT>", "<DEV_CHECKOUT>",
                "<ARTIFACT_CHECKOUT>", "<LEDGER_DIR>", "<NOTES_DIR>", "<LOCAL_PATH_ENV>", "<ANNOTATOR_ACCOUNT>")


def _text(root: Path, name: str) -> str:
    import gzip
    data = (root / name).read_bytes()
    return (gzip.decompress(data) if name.endswith(".gz") else data).decode("utf-8", "replace")


def scan_bytes(name: str, data: bytes) -> dict[str, int]:
    if name.endswith(".gz"):
        data = gzip.decompress(data)
    return integrity.scan_file(data)


def scenario_identity(root: Path, name: str) -> dict:
    """Source hash and held hash of one scenario file of the worked cases."""
    path = root / "cases" / "SCENARIO_IDENTITIES.json"
    if not path.is_file():
        return {}
    return load_json(path)["files"].get(name, {})


def scan_checkout(root: Path) -> dict[str, dict[str, int]]:
    out = {}
    for name in integrity.checkout_files(root):
        if name in ("provenance/SCAN.json", integrity.MANIFEST_NAME):
            continue
        counts = scan_bytes(name, (root / name).read_bytes())
        if counts:
            out[name] = counts
    return out


def verify(root: Path) -> int:
    report = Report("verify")

    problems = integrity.compare_manifest(root)
    for problem in problems[:20]:
        print("  manifest:", problem)
    report.check("MANIFEST.json equals the files present", 0, len(problems))
    manifest = load_json(root / "MANIFEST.json")["files"] if (root / "MANIFEST.json").is_file() else {}

    # pinned modules
    pins = load_json(root / "runtime" / "PINS.json")["pins"]
    report.check("pinned module sets", True, len(pins) > 0)
    for name, pin in sorted(pins.items()):
        folder = root / "runtime" / "pins" / name
        listed = {item["path"]: item for item in pin["files"]}
        present = {path.relative_to(folder).as_posix() for path in folder.rglob("*")
                   if path.is_file() and "__pycache__" not in path.parts} if folder.is_dir() else set()
        report.check(f"pin {name}: files present equal files listed", sorted(listed), sorted(present))
        wrong = [path for path, item in listed.items() if path in present
                 and integrity.sha256_file(folder / path) != item["sha256"]]
        report.check(f"pin {name}: file hashes", [], wrong)

    # source map
    source_map = load_json(root / "provenance" / "SOURCE_MAP.json")["files"]
    report.check("source map entries", True, len(source_map) > 0)
    wrong, not_identical = [], []
    for entry in source_map:
        path = root / entry["destination"]
        if not path.is_file() or integrity.sha256_file(path) != entry["destination_sha256"]:
            wrong.append(entry["destination"])
        if entry["transformation"] is None and entry["destination_sha256"] != entry["source_sha256"]:
            not_identical.append(entry["destination"])
    report.check("exported files have the recorded hash", [], wrong)
    report.check("files recorded as unchanged have the hash of their source", [], not_identical)
    mapped = {entry["destination"] for entry in source_map}
    recorded_exported = {name for name, item in manifest.items() if item.get("origin") == "exported"}
    report.check("files marked exported in the manifest equal the source map", sorted(mapped),
                 sorted(recorded_exported))

    # scorer identity
    identity = load_json(root / "scorers" / "IDENTITY.json")["scorers"]
    expected = load_json(root / "expected.json")
    for name, item in sorted(identity.items()):
        report.check(f"scorer {name}: file hash", item["sha256"], integrity.sha256_file(root / item["file"]))
        report.check(f"scorer {name}: hash reported by the 2026-10 run", item["sha256"],
                     item["sha256_reported_by_the_2026_10_run"])
    for name in ("current", "published"):
        pin = next((key for key, value in pins.items() if value["commit"] == identity[name]["commit"]), None)
        report.check(f"scorer {name}: a pinned module set exists for its commit", True, pin is not None)
        if pin is not None:
            report.check(f"scorer {name}: equals constraints.py of pin {pin}", identity[name]["sha256"],
                         integrity.sha256_file(root / "runtime" / "pins" / pin / "nlebench" / "runner" / "constraints.py"))
    for case in expected["cases"]["stored_run"]:
        for name, item in sorted(case["scorers"].items()):
            report.check(f"{case['scenario']}: expected hash of the {name} scorer", identity[name]["sha256"],
                         item["sha256"])
    commits = {pin["commit"] for pin in pins.values()}
    stages = [(case["scenario"], stage) for case in expected["cases"]["probe"] for stage in case["stages"]]
    report.check("probe stages recorded", True, len(stages) > 0)
    for scenario, stage in stages:
        report.check(f"{scenario}:{stage['stage']}: pinned modules for its commit", True,
                     stage["code_commit"] in commits)
        identity = scenario_identity(root, f"cases/{scenario}/{stage['scenario_file']}")
        report.check(f"{scenario}:{stage['stage']}: recorded stage identity is the source hash of the file",
                     stage["scenario_sha256"], identity.get("source_sha256"))
        report.check(f"{scenario}:{stage['stage']}: scenario file hash", identity.get("sha256_in_this_repository"),
                     integrity.sha256_file(root / "cases" / scenario / stage["scenario_file"]))

    # transformations: the repository-only half of the typed check
    transformations = load_json(root / "provenance" / "TRANSFORMATIONS.json")["files"]
    by_destination = {entry["destination"]: entry for entry in source_map}
    for name, item in sorted(transformations.items()):
        entry = by_destination.get(name, {})
        report.check(f"{name}: recorded as transformed in the source map", item["transformation"],
                     entry.get("transformation"))
        bad = []
        steps = item["transformation"].split("+")
        if name.endswith(".json") and not set(steps) <= {projection.TRANSFORMATION, "dev_split_reaggregate",
                                                         "json_local_paths", wording.TRANSFORMATION,
                                                         internal.MESSAGE, internal.REFERENCE}:
            bad.append(f"unknown transformation {item['transformation']}")
        replacements = [change for change in item["changes"] if "replacement" in change]
        if name.endswith(".json"):
            document = load_json(root / name)
            for change in replacements:
                position = tuple(change["path"])
                if change["rule"] in ("prefix", "whole_value") and not sanitize.registered(name, position):
                    bad.append(change["position"])
                if not integrity.same_json(integrity.get_at(document, position), change["replacement"]):
                    bad.append(change["position"])
            report.check(f"{name}: no phrase of the earlier descriptive wording remains", [],
                         wording.residue(document))
            if internal.MESSAGE in steps:
                report.check(f"{name}: no commit message remains at the registered patterns", [],
                             [integrity.position_text(position) for position, _ in integrity.string_leaves(document)
                              if any(integrity.matches(position, p) for p in internal.MESSAGES.get(name, ()))])
            if projection.TRANSFORMATION in steps:
                logged = [(change["kind"], change["pattern"]) for change in item["changes"]
                          if change.get("rule") == projection.TRANSFORMATION]
                report.check(f"{name}: the projection log lists the registered rules",
                             [list(rule) for rule in projection.PROJECTION[name]], [list(rule) for rule in logged])
        elif item["transformation"] == projection.TRANSFORMATION:
            logged = [(change["kind"], change["pattern"]) for change in item["changes"]]
            report.check(f"{name}: the projection log lists the registered rule",
                         [list(rule) for rule in projection.PROJECTION[name]], [list(rule) for rule in logged])
        else:
            rows = (root / name).read_text(encoding="utf-8").split("\n")
            for change in item["changes"]:
                if change.get("rule") == internal.REFERENCE:
                    continue
                # where listed lines were dropped after the path step, the line moved in the copy
                number = change.get("line_in_copy", change["line"])
                if not (0 < number <= len(rows)) or rows[number - 1] != change["replacement"]:
                    bad.append(change["position"])
            if item["transformation"] == "yaml_annotator_metadata":
                import yaml
                document = yaml.safe_load((root / name).read_text(encoding="utf-8"))
                values = (document.get("metadata") or {}).get("annotators")
                report.check(f"{name}: the registered line is the registered annotator line",
                             [sanitize.YAML_ANNOTATOR_LINES.get(name)], [change["line"] for change in item["changes"]])
                report.check(f"{name}: the annotator list keeps its structure and holds only the placeholder",
                             ["<ANNOTATOR_ACCOUNT>"], values)
                report.check(f"{name}: differs from its source by the identity table", False,
                             scenario_identity(root, name).get("identical_to_source"))
        report.check(f"{name}: logged replacements are in place at registered positions", [], bad)
        report.check(f"{name}: path replacements use only the declared placeholders", [],
                     sorted({change["replacement"] for change in item["changes"]
                             if change.get("rule") in ("prefix", "whole_value", None) and "replacement" in change
                             and not any(mark in change["replacement"] for mark in PLACEHOLDERS)}))
        counts = scan_bytes(name, (root / name).read_bytes())
        report.check(f"{name}: no local path remains", 0, counts.get("home_path", 0) + counts.get("tmp_path", 0))

    # scenarios: every scenario a file names is one of the dev-split scenarios of rejudging/membership.csv
    import csv
    with open(root / "rejudging" / "membership.csv", encoding="utf-8", newline="") as handle:
        dev = {row["scenario_id"] for row in csv.DictReader(handle)}
    report.check("rejudging/membership.csv lists the 200 dev-split scenarios", 200, len(dev))
    known = {projection.SCENARIO.fullmatch(name).group(1) for name in dev if projection.SCENARIO.fullmatch(name)}
    outside = sorted(name for name in integrity.checkout_files(root)
                     if set(projection.SCENARIO.findall(_text(root, name))) - known)
    # a bare code ("XX-YY-NNN") that is not a dev-split scenario's code is reported too
    bare = sorted(name for name in integrity.checkout_files(root)
                  if set(projection.BARE.findall(_text(root, name))) - known)
    report.check("files with a bare scenario-like code outside the dev split", [], bare)
    report.check("files that name a scenario outside the dev split", [], outside)

    # scan
    recorded_scan = load_json(root / "provenance" / "SCAN.json")
    report.check("recorded scan equals a scan of the files present", True,
                 integrity.same_json(recorded_scan["generic_patterns"], scan_checkout(root)))
    return report.finish()
