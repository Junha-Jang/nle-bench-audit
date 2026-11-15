"""The typed comparison and the registered-position transformation, on small made-up documents."""
from __future__ import annotations

import hashlib
import json

import pytest

from nlebench_audit import integrity, sanitize
from nlebench_audit.integrity import MISSING, differing_leaves, matches, same_json

# A made-up local directory. It is assembled from two pieces so that this file does not itself
# contain something the scan of the checkout would count as a local path.
H = "/home" "/someone"
PRIVATE = sanitize.PrivateMap([("<EXPORT_ROOT>", f"{H}/exports"), ("<DEV_CHECKOUT>", f"{H}/dev")], ["someone"])
FILE = "rejudging/runs/d32.json"


def test_boolean_is_not_a_number():
    assert not same_json(True, 1)
    assert not same_json(False, 0)
    assert not same_json({"passed": True}, {"passed": 1})
    assert same_json(True, True)


def test_null_is_only_null_and_missing_is_not_null():
    assert not same_json(None, 0)
    assert not same_json(None, "")
    assert not same_json(None, False)
    assert differing_leaves({"a": None}, {}) == {("a",): (None, MISSING)}
    assert differing_leaves({}, {"a": None}) == {("a",): (MISSING, None)}
    assert not same_json({"a": None}, {})


def test_numbers_by_value_strings_by_characters():
    assert same_json(0, 0.0)
    assert same_json(1.5, 1.5)
    assert not same_json("1", 1)
    assert not same_json("a", "a ")


def test_lists_and_objects_recursively():
    assert same_json({"a": [1, {"b": None}]}, {"a": [1, {"b": None}]})
    assert differing_leaves({"a": [1, 2]}, {"a": [1]}) == {("a", 1): (2, MISSING)}
    assert differing_leaves({"a": [1, 2]}, {"a": [1, True]}) == {("a", 1): (2, True)}
    assert differing_leaves([], {}) == {(): ([], {})}


def test_a_literal_string_is_not_mistaken_for_missing():
    assert differing_leaves({"a": "<missing>"}, {}) == {("a",): ("<missing>", MISSING)}


def test_positions_match_whole_paths_only():
    assert matches(("runtime", "executable"), "runtime/executable")
    assert matches(("runtime", "argv", 2), "runtime/argv/#")
    assert matches(("stamps", "d32", "runtime", "executable"), "stamps/*/runtime/executable")
    # the same ending somewhere else in the document is a different position
    assert not matches(("result", "runtime", "executable"), "runtime/executable")
    assert not matches(("runtime", "argv", "2"), "runtime/argv/#")
    assert not matches(("runtime", "argv", True), "runtime/argv/#")
    assert not matches(("stamps", 0, "runtime", "executable"), "stamps/*/runtime/executable")


def document():
    return {"command": [f"{H}/dev/.venv/bin/python", "-m", "x"],
            "execution": {"env": {"P3_EXPORT_ROOT": f"{H}/exports", "PATH": f"{H}/bin:/usr/bin"}},
            "runtime": {"argv": [f"{H}/dev/worker.py"], "executable": f"{H}/dev/.venv/bin/python",
                        "nlebench_file": f"{H}/exports/abc/src/nlebench/__init__.py",
                        "sys_path": [f"{H}/exports/abc/src"], "python": "3.12.3"},
            "result": {"records": 3, "passed": True, "note": None}}


def test_transformation_replaces_registered_leaves_and_checks_out():
    original = document()
    derived, log = sanitize.apply_json(FILE, original, PRIVATE)
    assert derived["runtime"]["executable"] == "<DEV_CHECKOUT>/.venv/bin/python"
    assert derived["execution"]["env"]["PATH"] == "<LOCAL_PATH_ENV>"
    assert derived["result"] == original["result"]
    assert "someone" not in json.dumps(derived)
    assert sanitize.check_json(FILE, original, derived, PRIVATE, log) == []
    assert len(log) == 7


def test_a_local_path_outside_the_registered_positions_is_an_error():
    original = document()
    original["result"]["note"] = f"{H}/dev/results"
    with pytest.raises(ValueError, match="unregistered position"):
        sanitize.apply_json(FILE, original, PRIVATE)


def test_check_rejects_a_changed_result_even_when_python_calls_it_equal():
    original = document()
    derived, log = sanitize.apply_json(FILE, original, PRIVATE)
    derived["result"]["passed"] = 1
    problems = sanitize.check_json(FILE, original, derived, PRIVATE, log)
    assert any("unregistered position result/passed" in problem for problem in problems)


def test_check_rejects_null_turned_into_missing():
    original = document()
    derived, log = sanitize.apply_json(FILE, original, PRIVATE)
    del derived["result"]["note"]
    problems = sanitize.check_json(FILE, original, derived, PRIVATE, log)
    assert any("result/note" in problem for problem in problems)


def test_check_rejects_an_unlogged_or_wrong_replacement():
    original = document()
    derived, log = sanitize.apply_json(FILE, original, PRIVATE)
    derived["runtime"]["executable"] = "<DEV_CHECKOUT>/other/python"
    assert any("runtime/executable" in problem
               for problem in sanitize.check_json(FILE, original, derived, PRIVATE, log))
    derived, log = sanitize.apply_json(FILE, original, PRIVATE)
    shorter = [entry for entry in log if entry["position"] != "runtime/executable"]
    assert any("not in the transformation log" in problem
               for problem in sanitize.check_json(FILE, original, derived, PRIVATE, shorter))


def test_check_rejects_a_registered_leaf_that_stops_being_a_string():
    original = document()
    derived, log = sanitize.apply_json(FILE, original, PRIVATE)
    derived["runtime"]["executable"] = None
    assert any("not a string on both sides" in problem
               for problem in sanitize.check_json(FILE, original, derived, PRIVATE, log))


def test_text_lines_only_listed_lines_change():
    original = f'import sys\nsys.path.insert(0, "{H}/dev/src")\nprint(1)\n'.encode()
    derived, log = sanitize.apply_lines("probe.py", original, (2,), PRIVATE)
    assert derived == b'import sys\nsys.path.insert(0, "<DEV_CHECKOUT>/src")\nprint(1)\n'
    assert sanitize.check_lines("probe.py", original, derived, (2,), PRIVATE, log) == []
    tampered = derived.replace(b"print(1)", b"print(2)")
    assert sanitize.check_lines("probe.py", original, tampered, (2,), PRIVATE, log) != []
    with pytest.raises(ValueError, match="unlisted line"):
        sanitize.apply_lines("probe.py", original, (), PRIVATE)
    with pytest.raises(ValueError, match="listed but holds no"):
        sanitize.apply_lines("probe.py", original, (2, 3), PRIVATE)


# A made-up folder name standing for the development repository's ledger folder.
FOLDER_MAP = sanitize.PrivateMap(PRIVATE.prefixes + [("<LEDGER_DIR>", "notes/folder_x")], ["someone"])


def test_a_folder_name_is_replaced_at_registered_positions_and_found_anywhere_else():
    original = document()
    original["benchmark_code"] = {"files": {"probe": {"path": "notes/folder_x/probe.py", "sha256": "ab"}}}
    derived, log = sanitize.apply_json(FILE, original, FOLDER_MAP)
    assert derived["benchmark_code"]["files"]["probe"] == {"path": "<LEDGER_DIR>/probe.py", "sha256": "ab"}
    assert sanitize.check_json(FILE, original, derived, FOLDER_MAP, log) == []
    original["result"]["note"] = "see notes/folder_x/probe.json"
    with pytest.raises(ValueError, match="unregistered position"):
        sanitize.apply_json(FILE, original, FOLDER_MAP)


def test_a_ledger_not_in_this_tools_serialisation_is_rewritten_line_by_line():
    name = "sources/ledgers/adjudication/206e1838224a/cluster_hardening.json"
    ledger = {"_meta": {"note": "kept as written", "input": "notes/folder_x/probe_full.json 의 findings"},
              "재실행_잔여": {"명령": "python notes/folder_x/probe_full.py"}, "n": 3}
    original = (json.dumps(ledger, ensure_ascii=False, indent=3) + "\n").encode()
    derived, log = sanitize.apply_json_lines(name, original, FOLDER_MAP)
    assert derived == original.replace(b"notes/folder_x", b"<LEDGER_DIR>")
    assert [entry["line"] for entry in log] == [4, 7]
    assert sanitize.check_json(name, ledger, json.loads(derived), FOLDER_MAP, log) == []
    assert sanitize.check_json_lines(name, original, derived, log) == []
    # the same leaves, but one more line differs: only the line comparison sees it
    respaced = derived.replace(b'"n": 3', b'"n":3')
    assert sanitize.check_json(name, ledger, json.loads(respaced), FOLDER_MAP, log) == []
    assert sanitize.check_json_lines(name, original, respaced, log) != []


SCENARIO = "cases/NLB-v3-CX-AT-003/scenario_473d3b54ecd4.yaml"
ACCOUNT_MAP = sanitize.PrivateMap([("<ANNOTATOR_ACCOUNT>", "someone")], ["someone"])


def scenario_text(annotator: str = "someone", transition: str = "cross_dissolve", source: str = "seed") -> bytes:
    """A made-up scenario whose annotator sits on the line registered for SCENARIO."""
    line = sanitize.YAML_ANNOTATOR_LINES[SCENARIO]
    head = ["id: X", "fixture: simple_sequence", "turns:", "- instruction: add a transition",
            "constraints:", "  required:", f"  - type: {transition}", "metadata:", f"  source: {source}"]
    filler = [f"  note_{index}: kept" for index in range(line - 2 - len(head))]
    return ("\n".join(head + filler + ["  annotators:", f"  - {annotator}", "  iaa_primary: null"]) + "\n").encode()


def derive(original: bytes):
    return sanitize.apply_lines(SCENARIO, original, (sanitize.YAML_ANNOTATOR_LINES[SCENARIO],), ACCOUNT_MAP)


def test_annotator_metadata_is_replaced_and_nothing_else():
    original = scenario_text()
    derived, log = derive(original)
    assert b"someone" not in derived and b"<ANNOTATOR_ACCOUNT>" in derived
    assert sanitize.check_yaml_metadata(SCENARIO, original, derived, ACCOUNT_MAP, log) == []


def test_a_changed_semantic_field_fails_even_with_the_right_annotator_line():
    original = scenario_text()
    derived, log = derive(original)
    tampered = derived.replace(b"cross_dissolve", b"dip_to_black")
    problems = sanitize.check_yaml_metadata(SCENARIO, original, tampered, ACCOUNT_MAP, log)
    assert any("outside the annotator field, at constraints/required/0/type" in problem for problem in problems)
    assert any("identity field `constraints` changed" in problem for problem in problems)


def test_a_change_at_another_metadata_path_fails():
    original = scenario_text()
    derived, log = derive(original)
    tampered = derived.replace(b"source: seed", b"source: other")
    problems = sanitize.check_yaml_metadata(SCENARIO, original, tampered, ACCOUNT_MAP, log)
    assert any("outside the annotator field, at metadata/source" in problem for problem in problems)


def test_the_annotator_rule_is_bound_to_registered_files_not_to_the_key():
    original = scenario_text()
    derived, log = derive(original)
    other = "cases/NLB-v3-EX-CO-004/scenario_published.yaml"
    assert sanitize.check_yaml_metadata(other, original, derived, ACCOUNT_MAP, log) == \
        [f"{other}: no annotator line is registered for this file"]


def test_a_typed_change_in_the_annotator_list_fails():
    original = scenario_text()
    derived, log = derive(original)
    emptied = derived.replace(b"  - <ANNOTATOR_ACCOUNT>\n", b"  - null\n")
    assert any("not a string on both sides" in problem or "line" in problem
               for problem in sanitize.check_yaml_metadata(SCENARIO, original, emptied, ACCOUNT_MAP, log))
    unchanged = sanitize.check_yaml_metadata(SCENARIO, original, original, ACCOUNT_MAP, log)
    assert any("do not differ" in problem for problem in unchanged)


def test_manifest_comparison_sees_change_addition_and_removal(tmp_path):
    (tmp_path / "a.txt").write_text("one")
    (tmp_path / "b.txt").write_text("two")
    manifest = {"files": integrity.measure(tmp_path)}
    (tmp_path / "MANIFEST.json").write_bytes(integrity.dump_json(manifest))
    assert integrity.compare_manifest(tmp_path) == []
    (tmp_path / "a.txt").write_text("ONE")
    assert integrity.compare_manifest(tmp_path) == ["sha256 differs: a.txt"]
    (tmp_path / "c.txt").write_text("three")
    (tmp_path / "b.txt").unlink()
    problems = integrity.compare_manifest(tmp_path)
    assert "listed but not present: b.txt" in problems and "present but not listed: c.txt" in problems


def test_an_empty_manifest_is_a_problem_not_a_pass(tmp_path):
    (tmp_path / "MANIFEST.json").write_text('{"files": {}}')
    assert integrity.compare_manifest(tmp_path) == ["MANIFEST.json lists no files"]


# --- test-split instructions --------------------------------------------------------------------
# ---------------------------------------------------------------------------
# dev-split projection, on made-up scenarios
# ---------------------------------------------------------------------------
# Scenario names are assembled so that this file does not itself name a scenario.

from nlebench_audit import projection, wording  # noqa: E402


def sid(code: str, prefix: str = "NLB-v3-") -> str:
    return prefix + "ZZ-" + code


SPLITS = projection.Splits({sid("AA-001"): "test", sid("AA-002"): "dev", sid("AA-003"): "test", sid("AA-004"): "dev"})
ROWS_FILE, JSON_FILE = "made/up.csv", "made/up.json"


@pytest.fixture
def rules(monkeypatch):
    monkeypatch.setitem(projection.PROJECTION, ROWS_FILE, ((projection.ROWS, "scenario_id"),))
    monkeypatch.setitem(projection.PROJECTION, JSON_FILE, (
        (projection.ITEM, "items/#"),
        (projection.ITEM, "groups/#"),
        (projection.ITEM, "groups/#/members/#"),
        (projection.ITEM, "by_key/*"),
        (projection.FIELD, "keysets/#/example"),
    ))


def csv_bytes() -> bytes:
    return ("row,scenario_id,verdict\n"
            f"1,{sid('AA-001')},True\n2,{sid('AA-002')},False\n3,{sid('AA-003')},True\n4,{sid('AA-004')},True\n").encode()


def test_rows_keep_dev_rows_byte_for_byte_and_drop_test_rows(rules):
    derived, log, detail = projection.project_rows(ROWS_FILE, csv_bytes(), SPLITS)
    assert derived.decode().split("\n") == ["row,scenario_id,verdict", f"2,{sid('AA-002')},False",
                                             f"4,{sid('AA-004')},True", ""]
    assert log == [{"rule": projection.TRANSFORMATION, "kind": projection.ROWS, "pattern": "scenario_id", "removed": 2}]
    assert len(detail) == 2 and all("scenarios" in entry for entry in detail)
    assert projection.check_rows(ROWS_FILE, csv_bytes(), derived, SPLITS, log) == []


def test_rows_check_fails_on_a_dropped_dev_row_or_a_kept_test_row(rules):
    derived, log, _ = projection.project_rows(ROWS_FILE, csv_bytes(), SPLITS)
    lines = derived.decode().split("\n")
    assert projection.check_rows(ROWS_FILE, csv_bytes(), "\n".join(lines[:1] + lines[2:]).encode(), SPLITS, log)
    kept_test = "\n".join(lines[:1] + [f"1,{sid('AA-001')},True"] + lines[1:]).encode()
    assert projection.check_rows(ROWS_FILE, csv_bytes(), kept_test, SPLITS, log)


def test_a_row_of_both_splits_or_of_no_scenario_or_an_unknown_one_is_an_error(rules):
    for row in (f"5,{sid('AA-002')} {sid('AA-001')},True", "5,nothing,True", f"5,{sid('AA-099')},True"):
        with pytest.raises(ValueError):
            projection.project_rows(ROWS_FILE, csv_bytes() + (row + "\n").encode(), SPLITS)


def split_document() -> dict:
    return {
        "items": [{"scenario": sid("AA-001"), "note": "x"}, {"scenario": sid("AA-002"), "note": "y"}],
        "groups": [{"members": [sid("AA-001"), sid("AA-003")], "why": "a"},
                   {"members": [sid("AA-002"), sid("AA-001")], "why": "b"}],
        "by_key": {sid("AA-003") + "|0": {"v": 1}, sid("AA-004") + "|0": {"v": 2}},
        "keysets": [{"keys": ["a"], "count": 9, "example": "corpus/" + sid("AA-001")},
                    {"keys": ["b"], "count": 3, "example": "corpus/" + sid("AA-002")}],
        "total": 4,
    }


def test_items_fields_and_nested_lists_keep_the_dev_split(rules):
    derived, log, _ = projection.project_json(JSON_FILE, split_document(), SPLITS)
    assert derived["items"] == [{"scenario": sid("AA-002"), "note": "y"}]
    assert derived["groups"] == [{"members": [sid("AA-002")], "why": "b"}]
    assert list(derived["by_key"]) == [sid("AA-004") + "|0"]
    assert derived["keysets"] == [{"keys": ["a"], "count": 9}, {"keys": ["b"], "count": 3,
                                                                  "example": "corpus/" + sid("AA-002")}]
    assert derived["total"] == 4
    assert [entry["removed"] for entry in log] == [1, 1, 1, 1, 1]
    assert projection.check_json(JSON_FILE, split_document(), derived, SPLITS, log) == []


def test_the_check_fails_when_a_test_item_returns_or_a_dev_item_goes_or_changes(rules):
    derived, log, _ = projection.project_json(JSON_FILE, split_document(), SPLITS)
    back = json.loads(json.dumps(derived))
    back["items"].append({"scenario": sid("AA-001"), "note": "x"})
    assert projection.check_json(JSON_FILE, split_document(), back, SPLITS, log)
    gone = json.loads(json.dumps(derived))
    gone["items"] = []
    assert projection.check_json(JSON_FILE, split_document(), gone, SPLITS, log)
    changed = json.loads(json.dumps(derived))
    changed["items"][0]["note"] = "z"
    assert projection.check_json(JSON_FILE, split_document(), changed, SPLITS, log)
    outside = json.loads(json.dumps(derived))
    outside["total"] = 5
    assert projection.check_json(JSON_FILE, split_document(), outside, SPLITS, log)


def test_a_test_name_left_outside_the_rules_or_a_mixed_field_is_an_error(rules):
    loose = split_document()
    loose["notes"] = f"see {sid('AA-001')}"
    with pytest.raises(ValueError):
        projection.project_json(JSON_FILE, loose, SPLITS)
    mixed = split_document()
    mixed["keysets"][0]["example"] = f"{sid('AA-001')} and {sid('AA-002')}"
    with pytest.raises(ValueError):
        projection.project_json(JSON_FILE, mixed, SPLITS)
    other_form = split_document()
    other_form["notes"] = sid("AA-001", "NLEB-")
    with pytest.raises(ValueError):
        projection.project_json(JSON_FILE, other_form, SPLITS)


def test_a_copy_is_written_in_the_style_of_its_source():
    for indent in (1, 2):
        text = json.dumps({"b": [1, {"c": "가"}], "a": None}, ensure_ascii=False, indent=indent) + "\n"
        assert projection.dumps_like(text, json.loads(text)) == text
    with pytest.raises(ValueError):
        projection.dumps_like('{"a":1,  "b":2}', {"a": 1})


# ---------------------------------------------------------------------------
# descriptive wording
# ---------------------------------------------------------------------------

def test_phrases_are_replaced_in_values_and_keys_and_identifiers_are_kept():
    original = {"what": "scorer of the public artifact against the dev pin",
                "published": "published", "dataset": "scenarios_v3_1 (published artifact copy)"}
    derived, log = wording.apply("made/up.json", original)
    assert derived == {"what": "scorer of the review snapshot against the dev pin", "published": "published",
                       "dataset": "scenarios_v3_1 (snapshot copy)"}
    assert all("original_sha256" in entry and "public" not in entry["replacement"] for entry in log)
    assert wording.check("made/up.json", original, derived, log) == []


def test_the_wording_check_fails_on_an_unlogged_change_or_a_remaining_phrase():
    original = {"what": "the public script with the public scorer"}
    derived, log = wording.apply("made/up.json", original)
    assert wording.check("made/up.json", original, {"what": "something else"}, log)
    assert wording.check("made/up.json", original, original, [])


def test_the_independent_derivation_catches_a_changed_dev_item_in_a_file_with_notes():
    name = "sources/ledgers/adjudication/206e1838224a/cluster_hardening.json"
    original = {"건별": [{"scenario": sid("AA-001"), "judgment": "REAL", "reason": "t"},
                         {"scenario": sid("AA-002"), "judgment": "REAL", "reason": "d"}]}
    derived, log, _ = projection.project_json(name, original, SPLITS)
    assert projection.check_json(name, original, derived, SPLITS, log) == []
    changed = json.loads(json.dumps(derived))
    changed["건별"][0]["judgment"] = "FREEDOM"
    assert projection.check_json(name, original, changed, SPLITS, log)


def test_an_item_that_names_no_scenario_stops_the_projection():
    name = "sources/ledgers/adjudication/206e1838224a/cluster_hardening.json"
    with pytest.raises(ValueError, match="names no scenario"):
        projection.project_json(name, {"건별": [{"scenario": sid("AA-001")}, {"judgment": "REAL"}]}, SPLITS)


def test_the_note_counts_in_the_readme_are_the_logged_ones():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    transformations = json.loads((root / "provenance" / "TRANSFORMATIONS.json").read_text(encoding="utf-8"))["files"]
    notes = {name: sum(c["removed"] for c in item["changes"]
                       if c.get("rule") == projection.TRANSFORMATION and c.get("kind") == projection.NOTE)
             for name, item in transformations.items()}
    count, files = sum(notes.values()), sum(1 for value in notes.values() if value)
    assert f"{count} values in {files} files" in (root / "README.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# withheld notes: a summary note about test-split sites that names no scenario
# ---------------------------------------------------------------------------

CLOSEOUT = "sources/ledgers/adjudication/206e1838224a/false_success_closeout.json"
CLUSTERS = "프로브_확장/남은_381건의_분류(클러스터 단위, 이번에 손대지 않음)"


def withheld_document() -> dict:
    return {"프로브_확장": {"남은_381건의_분류(클러스터 단위, 이번에 손대지 않음)": [
        {"클러스터": "a (2건)", "건수": 2, "판정": "혼재", "설명": "a kept summary"},
        {"클러스터": "b (3건)", "건수": 3, "판정": "보류", "설명": "a summary about sites of the other split"}]}}


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_a_withheld_note_is_dropped_by_its_listed_position_and_text_and_its_text_is_never_logged():
    listed = {f"{CLUSTERS}/1/설명": text_sha("a summary about sites of the other split")}
    derived, log, detail = projection.project_json(CLOSEOUT, withheld_document(), SPLITS, listed)
    entries = derived["프로브_확장"]["남은_381건의_분류(클러스터 단위, 이번에 손대지 않음)"]
    assert entries == [{"클러스터": "a (2건)", "건수": 2, "판정": "혼재", "설명": "a kept summary"},
                       {"클러스터": "b (3건)", "건수": 3, "판정": "보류"}]
    assert [entry for entry in log if entry["kind"] == projection.WITHHELD] == [
        {"rule": projection.TRANSFORMATION, "kind": projection.WITHHELD, "pattern": f"{CLUSTERS}/#/설명", "removed": 1}]
    assert not any("other split" in json.dumps(entry, ensure_ascii=False) for entry in log + detail)
    assert projection.check_json(CLOSEOUT, withheld_document(), derived, SPLITS, log, listed) == []
    # without the listed notes, the independent derivation keeps the note: the copy fails the check
    assert projection.check_json(CLOSEOUT, withheld_document(), derived, SPLITS, log)
    # a copy that kept the note fails against the list
    kept, kept_log, _ = projection.project_json(CLOSEOUT, withheld_document(), SPLITS)
    assert projection.check_json(CLOSEOUT, withheld_document(), kept, SPLITS, kept_log, listed)


def test_a_listed_note_with_other_text_or_at_no_withheld_position_stops_the_projection():
    with pytest.raises(ValueError, match="other text"):
        projection.project_json(CLOSEOUT, withheld_document(), SPLITS, {f"{CLUSTERS}/1/설명": text_sha("changed")})
    with pytest.raises(ValueError, match="not found"):
        projection.project_json(CLOSEOUT, withheld_document(), SPLITS, {f"{CLUSTERS}/5/설명": text_sha("x")})
    with pytest.raises(ValueError, match="no withheld_note rule"):
        projection.project_json("sources/ledgers/adjudication/206e1838224a/cluster_hardening.json",
                                {"건별": [{"scenario": sid("AA-002")}]}, SPLITS, {"건별/0/note": text_sha("x")})


def test_the_private_map_reads_withheld_notes_and_refuses_a_bad_entry():
    entry = {"file": CLOSEOUT, "position": f"{CLUSTERS}/1/설명", "sha256": text_sha("t")}
    private = sanitize.PrivateMap(PRIVATE.prefixes, ["someone"], [entry])
    assert private.withheld(CLOSEOUT) == {entry["position"]: entry["sha256"]}
    assert private.withheld("other.json") == {}
    with pytest.raises(ValueError):
        sanitize.PrivateMap(PRIVATE.prefixes, ["someone"], [dict(entry, sha256="not a hash")])
    with pytest.raises(ValueError):
        sanitize.PrivateMap(PRIVATE.prefixes, ["someone"], [entry, entry])


def test_the_withheld_count_in_the_readmes_is_the_logged_one():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    transformations = json.loads((root / "provenance" / "TRANSFORMATIONS.json").read_text(encoding="utf-8"))["files"]
    counts = {name: sum(c["removed"] for c in item["changes"]
                        if c.get("rule") == projection.TRANSFORMATION and c.get("kind") == projection.WITHHELD)
              for name, item in transformations.items()}
    count, files = sum(counts.values()), sum(1 for value in counts.values() if value)
    phrase = f"`withheld_note`: {count} value{'' if count == 1 else 's'} in {files} file{'' if files == 1 else 's'}"
    for readme in ("README.md", "provenance/README.md"):
        assert phrase in (root / readme).read_text(encoding="utf-8"), readme


def test_the_export_refuses_withheld_notes_listed_for_a_file_without_the_rule(tmp_path):
    import importlib
    import sys
    import types
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    export = importlib.import_module("export")
    assert projection.unregistered_withheld([CLOSEOUT]) == []
    assert projection.unregistered_withheld([CLOSEOUT, "missing-ledger.json"]) == ["missing-ledger.json"]
    good = {"file": CLOSEOUT, "position": f"{CLUSTERS}/7/설명", "sha256": text_sha("t")}
    # a file that does not exist, an exported JSON file without the rule, and a CSV file
    for other in ("missing-ledger.json", "sources/ledgers/adjudication/206e1838224a/cluster_hardening.json",
                  "sites/candidate_keys.csv"):
        bad = {"file": other, "position": "notes/0/text", "sha256": text_sha("t")}
        private_map = tmp_path / "map.json"
        private_map.write_text(json.dumps({"prefixes": [{"placeholder": "<EXPORT_ROOT>", "literal": f"{H}/exports"}],
                                           "private_tokens": ["someone"], "withheld_notes": [good, bad]}),
                               encoding="utf-8")
        args = types.SimpleNamespace(rc=str(tmp_path / "no-package"), dev_repo=str(tmp_path),
                                     artifact_repo=str(tmp_path), private_map=str(private_map))
        with pytest.raises(SystemExit, match="without a withheld_note rule"):
            export.Sources(args)


def test_the_independent_check_finds_a_withheld_listing_with_other_text_or_at_no_position():
    note = "a summary about sites of the other split"
    unchanged = withheld_document()
    log = [{"rule": projection.TRANSFORMATION, "kind": projection.WITHHELD, "pattern": f"{CLUSTERS}/#/설명", "removed": 1}]
    dropped = withheld_document()
    del dropped["프로브_확장"]["남은_381건의_분류(클러스터 단위, 이번에 손대지 않음)"][1]["설명"]
    for listed in ({f"{CLUSTERS}/1/설명": text_sha("other text")}, {f"{CLUSTERS}/5/설명": text_sha(note)}):
        for copy_ in (unchanged, dropped):
            assert projection.check_json(CLOSEOUT, withheld_document(), copy_, SPLITS, log, listed)
    good = {f"{CLUSTERS}/1/설명": text_sha(note)}
    assert projection.check_json(CLOSEOUT, withheld_document(), dropped, SPLITS, log, good) == []
    assert projection.check_json(CLOSEOUT, withheld_document(), dropped, SPLITS, [], good)


def test_origin_words_are_read_from_the_map_scanned_as_literals_and_applied():
    import importlib
    import sys
    from pathlib import Path
    private = sanitize.PrivateMap([("<P>", "/x/y")], ["acct"], origin_words=[["kept words", "words"]])
    assert private.origin_words == [("kept words", "words")]
    assert "kept words" in private.literals() and private.literal_hits("a kept words b") == 1
    for bad in ([["same", "same"]], [["", "x"]], [["only one"]]):
        with pytest.raises(ValueError):
            sanitize.PrivateMap([("<P>", "/x/y")], ["acct"], origin_words=bad)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    export = importlib.import_module("export")
    used: set[int] = set()
    assert export.origin_words("a kept words pack: x", private.origin_words, used) == "a words pack: x"
    assert used == {0}
    untouched: set[int] = set()
    assert export.origin_words("nothing here", private.origin_words, untouched) == "nothing here" and not untouched
