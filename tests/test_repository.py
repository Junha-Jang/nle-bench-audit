"""The commands on this checkout, and what they do when the checkout is damaged.

The second half are negative controls: each removes or changes one thing in a
disposable copy and requires the matching command to fail. A command that still
passed there would be passing without looking.
"""
from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nlebench_audit import integrity, replay, verify

COMMANDS = ("verify", "tables", "fixtures", "cases", "stored-cases")


def run(root: Path, command: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "nlebench_audit", "--root", str(root), command],
                          capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent)


@pytest.mark.parametrize("command", COMMANDS)
def test_command_passes_on_this_checkout(root, command):
    done = run(root, command)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    assert f"{command}: PASS" in done.stdout
    assert "FAIL" not in done.stdout


def test_counts_that_must_not_be_zero(root):
    records = replay.count_records(root / "rejudging" / "per_record.csv.gz")
    sites = replay.count_sites(root / "sites" / "candidate_keys.csv")
    # the dev split: 200 scenarios, 29,400 of the 117,600 stored records, 139 of the 527 candidate sites
    assert records["records"] == 29400
    assert records["disposition_S"] + records["disposition_D"] == 21570 + 5484
    assert records["disposition_FS"] + records["disposition_FD"] == 2346
    assert records["feasible"] == 23520 and records["feasible_disposition_FS"] == 1950
    assert sites["sites"] == 139
    fixtures = json.loads((root / "fixtures" / "predicate_fixtures.json").read_text(encoding="utf-8"))
    assert json.loads((root / "expected.json").read_text(encoding="utf-8"))["fixtures"]["fixture_cases"] == 69
    assert fixtures


def test_the_probe_cases_hold_the_stages_the_readme_says(root):
    expected = json.loads((root / "expected.json").read_text(encoding="utf-8"))["cases"]
    held = {case["scenario"]: len(case["stages"]) for case in expected["probe"]}
    assert held == {"NLB-v3-CX-CO-002": 4, "NLB-v3-EX-DE-008": 4, "NLB-v3-CX-AT-003": 2}
    assert sum(held.values()) == replay.PROBE_STAGES_EXPECTED
    not_held = {case["scenario"]: [item["stage"] for item in case.get("stages_not_replayable_from_pinned_files", [])]
                for case in expected["probe"]}
    assert not_held["NLB-v3-CX-CO-002"] == ["constraint_axis_alone"]
    assert not_held["NLB-v3-EX-DE-008"] == ["constraint_axis_alone"]
    assert len(expected["stored_run"]) * 2 == replay.STORED_JUDGMENTS_EXPECTED


def test_pinned_modules_are_the_recorded_blobs_and_nothing_else(root):
    pins = json.loads((root / "runtime" / "PINS.json").read_text(encoding="utf-8"))["pins"]
    assert len(pins) == 5
    for name, pin in pins.items():
        folder = root / "runtime" / "pins" / name
        for item in pin["files"]:
            data = (folder / item["path"]).read_bytes()
            assert integrity.sha256_bytes(data) == item["sha256"]
            # a git blob id is the SHA-1 of "blob <size>\0" followed by the content
            import hashlib
            assert hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == item["blob"]


def test_every_exported_file_is_mapped_and_unchanged_files_keep_their_hash(root):
    source_map = json.loads((root / "provenance" / "SOURCE_MAP.json").read_text(encoding="utf-8"))["files"]
    transformed = [entry for entry in source_map if entry["transformation"]]
    assert len(source_map) >= 140 and len(transformed) == 55
    assert sum(1 for entry in transformed if entry["transformation"] == "yaml_annotator_metadata") == 6
    assert sum(1 for entry in transformed if "dev_split_projection" in entry["transformation"].split("+")) == 28
    assert [entry["destination"] for entry in transformed if "dev_split_reaggregate" in entry["transformation"]] \
        == ["expected.json"]
    for entry in source_map:
        assert integrity.sha256_file(root / entry["destination"]) == entry["destination_sha256"]
        if entry["transformation"] is None:
            assert entry["destination_sha256"] == entry["source_sha256"]
        else:
            assert entry["destination_sha256"] != entry["source_sha256"]


def test_scenario_identities_keep_source_and_held_hash_apart(root):
    import yaml
    table = json.loads((root / "cases" / "SCENARIO_IDENTITIES.json").read_text(encoding="utf-8"))["files"]
    source_map = {entry["destination"]: entry for entry in
                  json.loads((root / "provenance" / "SOURCE_MAP.json").read_text(encoding="utf-8"))["files"]}
    changed = sorted(name for name, item in table.items() if not item["identical_to_source"])
    assert len(table) == 8 and len(changed) == 6
    for name, item in table.items():
        assert integrity.sha256_file(root / name) == item["sha256_in_this_repository"]
        assert item["source_sha256"] == source_map[name]["source_sha256"]
        # the file name carries the identity of the SOURCE, as the recorded runs do
        if "scenario_published" not in name:
            assert Path(name).name == f"scenario_{item['source_sha256'][:12]}.yaml"
        document = yaml.safe_load((root / name).read_text(encoding="utf-8"))
        if item["identical_to_source"]:
            assert item["sha256_in_this_repository"] == item["source_sha256"]
        else:
            assert item["sha256_in_this_repository"] != item["source_sha256"]
            assert item["differs_only_at"] == "metadata/annotators/#"
            assert document["metadata"]["annotators"] == ["<ANNOTATOR_ACCOUNT>"]
    expected = json.loads((root / "expected.json").read_text(encoding="utf-8"))["cases"]["probe"]
    for case in expected:
        for stage in case["stages"]:
            assert table[f"cases/{case['scenario']}/{stage['scenario_file']}"]["source_sha256"] == stage["scenario_sha256"]


def test_no_local_path_in_any_file(root):
    scan = verify.scan_checkout(root)
    with_paths = sorted(name for name, counts in scan.items() if counts.get("home_path") or counts.get("tmp_path"))
    assert with_paths == []


# ---------------------------------------------------------------------------
# negative controls
# ---------------------------------------------------------------------------

def failing(copy: Path, command: str) -> str:
    done = run(copy, command)
    assert done.returncode != 0, f"{command} passed on a damaged checkout:\n{done.stdout[-1500:]}"
    return done.stdout + done.stderr


def test_control_a_copy_passes_before_it_is_damaged(copy):
    for command in COMMANDS:
        assert run(copy, command).returncode == 0


def test_control_missing_pinned_module_fails_cases_and_verify(copy):
    (copy / "runtime" / "pins" / "dev-28a5f11c6f26" / "nlebench" / "tools" / "executor.py").unlink()
    assert "could not run on dev-28a5f11c6f26" in failing(copy, "cases")
    assert "files present equal files listed" in failing(copy, "verify")


def test_control_missing_pin_directory_fails_cases(copy):
    shutil.rmtree(copy / "runtime" / "pins" / "dev-5d2f3a6175ad")
    out = failing(copy, "cases")
    assert "NLB-v3-CX-AT-003:pre" in out and "stages replayed" in out


def test_control_without_any_pinned_module_tables_recounts_and_verify_fails(copy):
    """The whole of runtime/pins/ removed. The recount needs none of it and still passes; `verify`
    checks the full candidate and must not pass. A release without the pinned modules is therefore
    not this candidate with a folder deleted."""
    shutil.rmtree(copy / "runtime" / "pins")
    recount = run(copy, "tables")
    assert recount.returncode == 0, recount.stdout[-1500:] + recount.stderr[-1500:]
    assert "tables: PASS" in recount.stdout
    assert run(copy, "verify").returncode != 0
    for command in ("fixtures", "cases", "stored-cases"):
        assert run(copy, command).returncode != 0, command


def test_control_missing_harness_fails_stored_cases(copy):
    (copy / "runtime" / "pins" / "artifact-58afb977515b" / "nlebench" / "models.py").unlink()
    assert "judgments made" in failing(copy, "stored-cases")


def test_control_missing_validator_fails_fixtures(copy):
    (copy / "runtime" / "pins" / "dev-206e1838224a" / "nlebench" / "runner" / "validator.py").unlink()
    failing(copy, "fixtures")


def test_control_changed_scorer_fails_verify_and_stored_cases(copy):
    path = copy / "scorers" / "current_constraints.py"
    path.write_bytes(path.read_bytes() + b"\n# changed\n")
    assert "scorer current: file hash" in failing(copy, "verify")
    assert "does not have the recorded hash" in failing(copy, "stored-cases")


def test_control_changed_scenario_fails_cases(copy):
    path = copy / "cases" / "NLB-v3-CX-CO-002" / "scenario_6a6dd54230aa.yaml"
    path.write_bytes(path.read_bytes().replace(b"cross_dissolve", b"dip_to_black"))
    assert "does not have the recorded hash" in failing(copy, "cases")


def test_control_changed_constraint_with_a_matching_hash_still_fails_cases(copy):
    """A semantic field is changed and the identity table is updated to the new hash, so the hash
    check passes. The stage verdict must still expose it."""
    name = "cases/NLB-v3-CX-CO-002/scenario_6a6dd54230aa.yaml"
    path = copy / name
    path.write_bytes(path.read_bytes().replace(b"cross_dissolve", b"dip_to_black"))
    table_path = copy / "cases" / "SCENARIO_IDENTITIES.json"
    table = json.loads(table_path.read_text(encoding="utf-8"))
    table["files"][name]["sha256_in_this_repository"] = integrity.sha256_file(path)
    table_path.write_bytes(integrity.dump_json(table))
    out = failing(copy, "cases")
    assert "NLB-v3-CX-CO-002:current_pin" in out
    failing(copy, "verify")


def test_control_wrong_source_identity_fails_cases_and_verify(copy):
    table_path = copy / "cases" / "SCENARIO_IDENTITIES.json"
    table = json.loads(table_path.read_text(encoding="utf-8"))
    name = "cases/NLB-v3-CX-AT-003/scenario_473d3b54ecd4.yaml"
    table["files"][name]["source_sha256"] = table["files"][name]["sha256_in_this_repository"]
    table_path.write_bytes(integrity.dump_json(table))
    assert "does not name the recorded source hash" in failing(copy, "cases")
    assert "recorded stage identity is the source hash" in failing(copy, "verify")


def test_control_account_name_put_back_fails_verify(copy):
    path = copy / "cases" / "NLB-v3-EX-DE-008" / "scenario_abf8dc2d3c14.yaml"
    path.write_bytes(path.read_bytes().replace(b"<ANNOTATOR_ACCOUNT>", b"someone"))
    out = failing(copy, "verify")
    assert "logged replacements are in place" in out and "holds only the placeholder" in out


def test_control_swapped_expectation_fails_cases(copy):
    path = copy / "expected.json"
    expected = json.loads(path.read_text(encoding="utf-8"))
    stage = expected["cases"]["probe"][0]["stages"][0]
    stage["candidate_passed"] = not stage["candidate_passed"]
    path.write_text(json.dumps(expected), encoding="utf-8")
    failing(copy, "cases")


def test_control_one_changed_verdict_fails_tables(copy):
    path = copy / "rejudging" / "per_record.csv.gz"
    rows = gzip.decompress(path.read_bytes()).decode("utf-8").split("\n")
    header = rows[0].split(",")
    column = header.index("d32_success")
    # the first feasible record: only feasible records enter the success counts
    number = next(index for index, row in enumerate(rows[1:], start=1)
                  if row.split(",")[header.index("arm")] == "feasible")
    cells = rows[number].split(",")
    cells[column] = "False" if cells[column] == "True" else "True"
    rows[number] = ",".join(cells)
    path.write_bytes(gzip.compress("\n".join(rows).encode("utf-8")))
    failing(copy, "tables")


def test_control_empty_table_is_not_a_pass(copy):
    path = copy / "rejudging" / "per_record.csv.gz"
    header = gzip.decompress(path.read_bytes()).decode("utf-8").split("\n")[0]
    path.write_bytes(gzip.compress((header + "\n").encode("utf-8")))
    assert "holds no rows" in failing(copy, "tables")


def test_control_no_fixture_cases_is_not_a_pass(copy):
    path = copy / "fixtures" / "tests" / "test_predicate_fixtures.py"
    path.write_text("def test_nothing():\n    assert True\n", encoding="utf-8")
    assert "fixture cases run" in failing(copy, "fixtures")


def test_control_unlisted_file_and_changed_record_fail_verify(copy):
    (copy / "cases" / "extra.json").write_text("{}", encoding="utf-8")
    assert "present but not listed: cases/extra.json" in failing(copy, "verify")


def test_control_reintroduced_local_path_fails_verify(copy):
    path = copy / "rejudging" / "runs" / "d32.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["runtime"]["executable"] = "/home" "/someone/.venv/bin/python"
    path.write_bytes(integrity.dump_json(document))
    out = failing(copy, "verify")
    assert "logged replacements are in place" in out and "no local path remains" in out
