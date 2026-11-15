"""The two rules of `nlebench_audit.internal`, on made-up documents: commit messages, and listed spans."""
import copy
import hashlib

import pytest

from nlebench_audit import integrity, internal


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def span_entry(value: str, part: str) -> dict:
    start = value.index(part)
    return {"original_sha256": sha(value), "span_sha256": sha(part), "start": start, "end": start + len(part)}


# --------------------------------------------------------------------------- commit messages
NAME = "checks/constraint_history.json"


def history():
    return {"commits": [{"commit": "a" * 40, "date": "2026-08-01", "added": 3, "subject": "fix: first note"},
                        {"commit": "b" * 40, "date": "2026-08-02", "added": 0, "subject": "docs: second note"}],
            "checks_against_the_submission_text": {"other_commits_with_a_removed_entry": [
                {"commit": "c" * 7, "removed": 1, "subject": "fix: third note"}]},
            "totals": {"added": 3}}


def test_registered_commit_messages_are_dropped_and_everything_else_is_kept():
    original = history()
    derived, log = internal.drop_messages(NAME, original)
    assert all("subject" not in commit for commit in derived["commits"])
    assert [commit["commit"] for commit in derived["commits"]] == ["a" * 40, "b" * 40]
    assert derived["commits"][0]["date"] == "2026-08-01" and derived["totals"] == {"added": 3}
    assert {entry["pattern"]: entry["removed"] for entry in log} == {
        "commits/#/subject": 2, "checks_against_the_submission_text/other_commits_with_a_removed_entry/#/subject": 1}
    assert internal.check_messages(NAME, original, derived, log) == []
    assert internal.message_texts(NAME, original) == ["fix: first note", "docs: second note", "fix: third note"]


def test_the_message_check_finds_other_changes_and_a_kept_message():
    original = history()
    derived, log = internal.drop_messages(NAME, original)
    changed = copy.deepcopy(derived)
    changed["commits"][0]["added"] = 4
    assert any("outside a commit-message pattern" in p for p in internal.check_messages(NAME, original, changed, log))
    kept = copy.deepcopy(derived)
    kept["commits"][1]["subject"] = "docs: second note"
    assert any("remains" in p or "count" in p for p in internal.check_messages(NAME, original, kept, log))
    wrong_log = [dict(entry, removed=entry["removed"] + 1) for entry in log]
    assert any("does not count" in p for p in internal.check_messages(NAME, original, derived, wrong_log))


def test_a_registered_pattern_that_matches_nothing_stops_the_export():
    with pytest.raises(ValueError, match="matches no value"):
        internal.drop_messages(NAME, {"commits": [], "checks_against_the_submission_text": {}})


# --------------------------------------------------------------------------- listed spans in JSON values
def document():
    return {"meta": {"why": "A reason that stands. A reference to some meeting.", "what": "kept"},
            "decision": "A sentence that refers only to elsewhere.", "items": [{"role": "used (see note 4): yes"}]}


def listed(doc):
    return {"meta/why": span_entry(doc["meta"]["why"], " A reference to some meeting."),
            "decision": span_entry(doc["decision"], doc["decision"]),
            "items/0/role": span_entry(doc["items"][0]["role"], " (see note 4)")}


def test_a_listed_span_is_removed_and_a_whole_value_drops_its_key():
    original = document()
    entries = listed(original)
    derived, log = internal.apply_references("x.json", original, entries)
    assert derived["meta"] == {"why": "A reason that stands.", "what": "kept"}
    assert "decision" not in derived
    assert derived["items"][0]["role"] == "used: yes"
    assert log == [{"rule": internal.REFERENCE, "removed": 3, "values_dropped": 1, "values_shortened": 2}]
    assert internal.check_references("x.json", original, derived, entries, log) == []
    assert sorted(internal.span_texts("x.json", original, entries)) == sorted(
        [" A reference to some meeting.", "A sentence that refers only to elsewhere.", " (see note 4)"])


def test_a_listed_span_with_other_text_or_at_no_value_stops_the_export():
    original = document()
    entries = listed(original)
    bad = dict(entries, **{"meta/why": dict(entries["meta/why"], original_sha256=sha("other"))})
    with pytest.raises(ValueError, match="differs from the source"):
        internal.apply_references("x.json", original, bad)
    bad = dict(entries, **{"meta/why": dict(entries["meta/why"], span_sha256=sha("other"))})
    with pytest.raises(ValueError, match="span is not in the value"):
        internal.apply_references("x.json", original, bad)
    with pytest.raises(ValueError, match="at no string value"):
        internal.apply_references("x.json", original, {"meta/absent": entries["meta/why"]})


def test_the_reference_check_finds_an_unlisted_change_a_wrong_cut_and_a_kept_span():
    original = document()
    entries = listed(original)
    derived, log = internal.apply_references("x.json", original, entries)
    changed = copy.deepcopy(derived)
    changed["meta"]["what"] = "changed"
    assert any("no listed reference names" in p for p in internal.check_references("x.json", original, changed, entries, log))
    cut = copy.deepcopy(derived)
    cut["meta"]["why"] = "A reason that"
    assert any("without the listed span" in p for p in internal.check_references("x.json", original, cut, entries, log))
    kept = copy.deepcopy(derived)
    kept["items"][0]["role"] = original["items"][0]["role"]
    assert any("was not removed" in p for p in internal.check_references("x.json", original, kept, entries, log))


# --------------------------------------------------------------------------- listed lines and spans in a text file
TEXT = "\n".join(['"""Title.', "", "A quoted passage starts here.", "  quoted line", "", "Kept line, and a remark.",
                  '"""', 'WHY = "first part. second part."', ""])


def line_entries():
    rows = TEXT.split("\n")
    out = {number: {"drop": True, "original_sha256": sha(rows[number - 1])} for number in (3, 4, 5)}
    out[6] = span_entry(rows[5], ", and a remark")
    out[8] = span_entry(rows[7], " second part.")
    return out


def test_listed_lines_are_dropped_and_listed_spans_removed_and_nothing_else_changes():
    entries = line_entries()
    derived, log = internal.apply_reference_lines("probe.py", TEXT.encode(), entries)
    assert derived.decode().split("\n") == ['"""Title.', "", "Kept line.", '"""', 'WHY = "first part."', ""]
    assert log == [{"rule": internal.REFERENCE, "removed": 5, "spans_removed": 2, "lines_dropped": 3}]
    assert internal.check_reference_lines("probe.py", TEXT.encode(), derived, entries, log) == []
    assert internal.line_texts("probe.py", TEXT.encode(), entries)[:2] == ["A quoted passage starts here.", "  quoted line"]


def test_the_line_check_finds_a_changed_line_and_a_wrong_drop():
    entries = line_entries()
    derived, log = internal.apply_reference_lines("probe.py", TEXT.encode(), entries)
    rows = derived.decode().split("\n")
    rows[0] = '"""Title changed.'
    assert any("no listed reference names it" in p
               for p in internal.check_reference_lines("probe.py", TEXT.encode(), "\n".join(rows).encode(), entries, log))
    shorter = "\n".join(derived.decode().split("\n")[1:]).encode()
    assert any("line count differs" in p for p in internal.check_reference_lines("probe.py", TEXT.encode(), shorter, entries, log))
    wrong = dict(entries)
    wrong[4] = {"drop": True, "original_sha256": sha("another line")}
    with pytest.raises(ValueError, match="listed to be dropped but differs"):
        internal.apply_reference_lines("probe.py", TEXT.encode(), wrong)


# --------------------------------------------------------------------------- the private map's list
def test_the_list_of_references_is_validated():
    good = {"file": "x.json", "position": "a", "original_sha256": sha("a b"), "span_sha256": sha("b"), "start": 2, "end": 3}
    assert internal.validate([good]) == {"x.json": {"a": {k: good[k] for k in ("original_sha256", "span_sha256", "start", "end")}}}
    for bad in (dict(good, original_sha256="nope"), dict(good, start=3, end=3), dict(good, line=4),
                {**{k: v for k, v in good.items() if k != "position"}, "drop": True},
                {"file": "x.json", "position": "a", "drop": True, "original_sha256": sha("x")}):
        with pytest.raises(ValueError):
            internal.validate([bad])
    with pytest.raises(ValueError, match="listed twice"):
        internal.validate([good, good])
    dropped = {"file": "p.py", "line": 4, "drop": True, "original_sha256": sha("x")}
    assert internal.validate([dropped]) == {"p.py": {4: {"drop": True, "original_sha256": sha("x")}}}
    assert integrity.sha256_bytes(b"x") == sha("x")
