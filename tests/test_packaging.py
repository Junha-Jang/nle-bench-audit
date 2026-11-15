"""Packaging and citation metadata: what is declared, and what is deliberately not."""
from __future__ import annotations

import json
import subprocess
import sys
import tomllib

import yaml

import nlebench_audit
from nlebench_audit import integrity

TITLE ="Red-Teaming the Grader: Mutation-Validated Constraint Checking for Tool-Agent Benchmarks"
# sha256 of https://creativecommons.org/licenses/by-nc/4.0/legalcode.txt
LEGAL_CODE_SHA256 = "41003d4a74749c0220e33dd415042164b5a1093ed401f36277234f772d22d3d0"


def test_project_metadata(root):
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == "nle-bench-audit"
    assert project["version"] == nlebench_audit.__version__
    assert project["authors"] == [{"name": "Junha Jang"}]
    assert project["scripts"] == {"nle-bench-audit": "nlebench_audit.__main__:main"}
    assert any(item.startswith("pydantic") for item in project["dependencies"])
    assert any(item.lower().startswith("pyyaml") for item in project["dependencies"])
    # the licence and its terms are stated in LICENSE and LICENSE.md; the package metadata sets no licence key
    assert "license" not in project and "license-files" not in project
    assert not any("License ::" in item for item in project.get("classifiers", []))


def test_citation_names_the_paper_and_invents_nothing(root):
    cff = yaml.safe_load((root / "CITATION.cff").read_text(encoding="utf-8"))
    preferred = cff["preferred-citation"]
    assert preferred["title"] == TITLE
    for block in (cff, preferred):
        assert [(a["given-names"], a["family-names"], a["affiliation"]) for a in block["authors"]] == \
            [("Junha", "Jang", "Someple Inc.")]
        for absent in ("doi", "date-released", "date-published", "url", "license-url", "version",
                       "identifiers", "pages", "volume"):
            assert absent not in block, absent
    assert cff["license"] == "CC-BY-NC-4.0" and "license" not in preferred
    assert cff["repository-code"] == "https://github.com/Junha-Jang/nle-bench-audit"
    assert "repository-code" not in preferred


def test_the_chosen_licence_and_what_it_does_not_cover(root):
    top = {path.name for path in root.iterdir() if path.is_file()}
    assert {name for name in top if name.upper().startswith(("LICENSE", "LICENCE", "COPYING"))} == \
        {"LICENSE", "LICENSE.md"}
    # the standard legal code, unmodified
    assert integrity.sha256_bytes((root / "LICENSE").read_bytes()) == LEGAL_CODE_SHA256
    scope = " ".join((root / "LICENSE.md").read_text(encoding="utf-8").split())
    for needed in ("Copyright 2026 Someple Inc.", "(CC BY-NC 4.0)", "## Additional verification permission",
                   "attribute it to Junha Jang (Someple Inc.)", "junha.jang" "@" "someple.com",
                   "cases/NLB-v3-EX-CO-004/states/agent_response.txt",
                   "cases/NLB-v3-EX-CO-009/states/agent_response.txt"):
        assert needed in scope, needed
    # the source trees' own licence files are not part of this repository
    assert not (root / "upstream-licenses").exists()


def test_module_entry_point_lists_the_commands(root):
    done = subprocess.run([sys.executable, "-m", "nlebench_audit", "--help"], capture_output=True, text=True, cwd=root)
    assert done.returncode == 0
    for command in ("verify", "tables", "fixtures", "cases", "stored-cases", "all", "fingerprint"):
        assert command in done.stdout


def test_the_scope_and_its_limits_are_stated_where_a_reader_looks(root):
    readme = " ".join((root / "README.md").read_text(encoding="utf-8").split())
    assert "Scope: the dev split only." in readme
    assert "This repository holds no item of a test-split scenario" in readme
    assert "only their dev-split components can be recounted here" in readme
    # who made the semantic judgments, and what the evidence does not include
    assert "were generated in a coding-agent workflow" in readme
    assert "they are the names the records were written with" in readme
    assert "does not include human validation or an inter-rater agreement measurement" in readme
    # what a reader cannot do, and that a pass is not an independent verification
    assert "It is not an independent verification." in readme
    assert "Judging the stored records again." in readme
    licence = " ".join((root / "LICENSE.md").read_text(encoding="utf-8").split())
    assert "On 14 November 2026, Junha Jang, answering for Someple Inc., chose this licence." in licence
    assert "The two stored model responses" in licence


def test_projections_are_logged_by_rule_and_count_without_the_items(root):
    from nlebench_audit import projection
    transformations = json.loads((root / "provenance" / "TRANSFORMATIONS.json").read_text(encoding="utf-8"))["files"]
    projected = {name: item for name, item in transformations.items()
                 if projection.TRANSFORMATION in item["transformation"].split("+")}
    assert set(projected) == set(projection.PROJECTION)
    for name, item in projected.items():
        logged = [change for change in item["changes"] if change.get("rule") == projection.TRANSFORMATION]
        assert [(change["kind"], change["pattern"]) for change in logged] == list(projection.PROJECTION[name])
        assert sum(change["removed"] for change in logged) > 0, name
        for change in logged:
            assert set(change) == {"rule", "kind", "pattern", "removed"}, name
    text = (root / "provenance" / "TRANSFORMATIONS.json").read_text(encoding="utf-8")
    assert "WITHHELD" not in text


def test_the_quoted_text_facts_in_provenance_are_the_measured_ones(root):
    review = json.loads((root / "provenance" / "CONTENT_REVIEW.json").read_text(encoding="utf-8"))
    facts = " ".join((root / "provenance" / "README.md").read_text(encoding="utf-8").split())
    totals = review["totals"]
    assert review["method"]["scenarios_known"] == 800 and review["method"]["split_counts"] == {"dev": 200, "test": 600}
    assert totals["files_holding_a_local_account_name"] == 0
    quoted, named = totals["scenarios_quoted"], totals["scenarios_named"]
    assert named == {"dev": 200, "test": 0}
    assert f"{totals['files_quoting_an_instruction']} files quote the instructions of {quoted['dev']} dev-split" in facts
    assert f"{totals['files_naming_scenarios']} files name scenarios by identifier, 200 of the dev split and 0 of" in facts
    assert f"also counts {quoted['test']} test-split scenarios" in facts
    assert "not a statement that no text of a test-split scenario occurs here" in facts
    assert set(review["model_responses_kept"]) == {
        "cases/NLB-v3-EX-CO-004/states/agent_response.txt", "cases/NLB-v3-EX-CO-009/states/agent_response.txt"}
    values = {tuple(value) for value in review["annotator_values_in_scenario_files"].values()}
    assert values == {("<ANNOTATOR_ACCOUNT>",), ("author_pi",)}


def test_promise_inventory_is_not_all_present(root):
    promises = json.loads((root / "inventory" / "promises" / "promises.json").read_text(encoding="utf-8"))["promises"]
    statuses = {item["status"] for item in promises}
    assert statuses <= {"present", "partial", "missing"}
    assert {"partial", "missing"} & statuses, "an inventory that marks everything present needs a second look"
    for item in promises:
        for name in item["files"]:
            assert (root / name).exists(), name


def test_issue_map_uses_only_the_four_statuses(root):
    issues = json.loads((root / "inventory" / "issues" / "issues.json").read_text(encoding="utf-8"))["issues"]
    assert len(issues) >= 6
    assert {item["status"] for item in issues} <= {"addressed", "partial", "open", "not checked"}
