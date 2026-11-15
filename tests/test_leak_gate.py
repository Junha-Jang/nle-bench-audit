"""The leak gate's scanning, on made-up scenarios and wordings (the gate itself needs the corpora, which are not
included here)."""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
import types
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import leak_gate  # noqa: E402
from nlebench_audit import projection  # noqa: E402

TEST, DEV = "NLB-v3-" + "ZZ-" + "AA-001", "NLB-v3-" + "ZZ-" + "AA-002"
WORDING = "A later instruction of a made-up test item, long enough."
TARGET = leak_gate.Target(types.SimpleNamespace(splits=projection.Splits({TEST: "test", DEV: "dev"}),
                                                wordings={TEST: {WORDING}, DEV: {"An unrelated dev instruction."}}),
                          {})


def escaped(text: str) -> str:
    return "".join("\\u%04x" % ord(c) for c in text)


def test_escaped_json_is_parsed_also_inside_gzip():
    data = ('{"instruction": "' + escaped(WORDING) + '", "scenario": "' + escaped(TEST) + '"}').encode()
    for name, blob in (("hidden.json", data), ("hidden.json.gz", gzip.compress(data, mtime=0))):
        hits, _ = leak_gate.scan_file(name, blob, TARGET)
        assert len(hits) == 2, name


def test_a_bare_code_and_a_file_name_are_hits():
    assert leak_gate.scan_file("notes.md", b"see " + TEST[-9:].encode(), TARGET)[0]
    assert leak_gate.scan_file(f"cases/{TEST}.txt", b"x", TARGET)[0]


def test_a_json_file_that_does_not_parse_is_a_hit():
    hits, _ = leak_gate.scan_file("broken.json.gz", gzip.compress(b"{not json", mtime=0), TARGET)
    assert any("does not parse" in hit for hit in hits)


def test_a_dev_only_file_has_no_hit():
    assert leak_gate.scan_file("ok.json", ('{"scenario": "' + DEV + '"}').encode(), TARGET)[0] == []


def test_an_old_corpus_id_tied_to_a_pinned_scenario_is_a_hit():
    old = "NLB-" + "043"
    tied = ('{"v2": "' + old + '", "v3": "' + DEV + '"}').encode()
    assert leak_gate.scan_file("map.json", tied, TARGET)[0]
    assert leak_gate.scan_file("map.csv", f"{old},{DEV}\n".encode(), TARGET)[0]
    alone = ('{"scenarios_v2": ["' + old + '"], "scenarios_v3": ["' + DEV + '"]}').encode()
    assert leak_gate.scan_file("lists.json", alone, TARGET)[0] == []


OWN = "Before that. " + WORDING + " After that."
ONLY_TEST = "A test-only instruction that no dev item records."
OWN_TARGET = leak_gate.Target(types.SimpleNamespace(
    splits=projection.Splits({TEST: "test", DEV: "dev"}),
    wordings={TEST: {WORDING + " || " + ONLY_TEST}, DEV: {OWN}}), {})


def yaml_file(*lines: str) -> bytes:
    return ("\n".join(("id: " + DEV, "split: dev") + lines) + "\n").encode()


def jsonl_line(record: dict) -> bytes:
    return (json.dumps(record) + "\n").encode()


def test_a_dev_scenario_file_may_hold_its_recorded_instruction():
    hits, allowed = leak_gate.scan_file("dev.yaml", yaml_file("turns:", "- instruction: " + OWN), OWN_TARGET)
    assert (hits, allowed) == ([], 1)
    sample = {"scenario_id": DEV, "scenario": {"turns": [{"instruction": OWN}]}}
    assert leak_gate.scan_file("samples.jsonl", jsonl_line(sample), OWN_TARGET) == ([], 1)


def test_the_same_wording_in_another_field_is_still_a_hit():
    # the own turn does not cover a second occurrence elsewhere in the file or record
    assert leak_gate.scan_file("dev.yaml", yaml_file("turns:", "- instruction: " + OWN, "note: " + WORDING),
                               OWN_TARGET)[0]
    assert leak_gate.scan_file("dev.yaml", yaml_file("turns:", "- instruction: " + OWN, "# " + WORDING),
                               OWN_TARGET)[0]
    record = {"id": DEV, "turns": [{"instruction": OWN}], "note": WORDING}
    assert leak_gate.scan_file("dev.jsonl", jsonl_line(record), OWN_TARGET)[0]


def test_an_escaped_own_instruction_does_not_cover_a_comment():
    # the own instruction in escapes and the wording once more, plain, in a comment
    plain = yaml_file("turns:", "- instruction: " + OWN)
    escaped_own = yaml_file("turns:", '- instruction: "' + escaped(OWN) + '"')
    assert leak_gate.scan_file("dev.yaml", escaped_own, OWN_TARGET) == ([], 1)
    for data in (plain, escaped_own):
        assert leak_gate.scan_file("dev.yaml", data + ("# " + WORDING + "\n").encode(), OWN_TARGET)[0]
    folded = yaml_file("turns:", "- instruction: >-", "    " + OWN.replace(" ", "\n    ", 3))
    assert leak_gate.scan_file("dev.yaml", folded, OWN_TARGET) == ([], 1)
    assert leak_gate.scan_file("dev.yaml", folded + ("# " + WORDING + "\n").encode(), OWN_TARGET)[0]


def test_an_instruction_that_is_not_recorded_for_that_dev_scenario_is_a_hit():
    # a test wording placed in a dev id's turns, not the pinned instruction of that scenario
    for turn in (WORDING, ONLY_TEST, OWN + " More."):
        assert leak_gate.scan_file("dev.yaml", yaml_file("turns:", "- instruction: " + turn), OWN_TARGET)[0], turn
        assert leak_gate.scan_file("dev.jsonl", jsonl_line({"id": DEV, "turns": [{"instruction": turn}]}),
                                   OWN_TARGET)[0], turn
    other = yaml_file("turns:", "- instruction: " + OWN).replace(DEV.encode(), TEST.encode())
    assert leak_gate.scan_file("test.yaml", other, OWN_TARGET)[0]


def test_escaped_yaml_is_read_decoded_also_inside_gzip():
    data = ('id: "' + escaped(TEST) + '"\nnote: "' + escaped(ONLY_TEST) + '"\n').encode()
    for name, blob in (("hidden.yaml", data), ("hidden.yml", data), ("hidden.yaml.gz", gzip.compress(data, mtime=0))):
        hits, _ = leak_gate.scan_file(name, blob, OWN_TARGET)
        assert len(hits) == 2, (name, hits)


def test_a_declared_synthetic_id_is_allowed_only_in_the_reviewed_bytes_of_its_file():
    old = "NLB-" + "042"
    target = leak_gate.Target(types.SimpleNamespace(
        splits=projection.Splits({TEST: "test", DEV: "dev", old: "test"}),
        wordings={TEST: {WORDING}, DEV: {"An unrelated dev instruction."}}), {})
    name = "code/tests/test_scenario_loader.py"
    data = ('{"id": "' + old + '"}').encode()
    reviewed = {name: (hashlib.sha256(data).hexdigest(), {old})}
    with mock.patch.dict(leak_gate.SYNTHETIC_IDS, reviewed):
        assert leak_gate.scan_file(name, data, target)[0] == []
        assert leak_gate.scan_file(name, data + b"\n# " + old.encode(), target)[0]
        assert leak_gate.scan_file(name, data + b"\n" + WORDING.encode(), target)[0]
        assert leak_gate.scan_file("elsewhere.py", data, target)[0]
    assert leak_gate.scan_file(name, data, target)[0]
