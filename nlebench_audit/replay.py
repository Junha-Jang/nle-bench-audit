"""Replay commands that need nothing outside this checkout.

Each command recomputes something and compares it with a recorded value. A
pass means: this copy of the files, run here, returns the recorded values. It
is not an independent verification of the benchmark, and it is not a run of
any agent or model.

  tables         RE-COUNTS the stored per-record verdicts and the candidate
                 site table, which hold the dev split. It judges no state.
  fixtures       runs the 69 predicate fixtures, the meta-test and the
                 fail-closed tests against the pinned modules of the
                 development pin.
  cases          re-runs the stored reference steps and the candidate steps of
                 the three probe cases, at every stage this repository holds,
                 each on the pinned modules of that stage's commit.
  stored-cases   judges the stored state pair of the two stored-run cases with
                 the snapshot harness modules under two scorer files.

The benchmark modules are the byte-identical exports under `runtime/pins/`.
They run in a subprocess whose import path is that one directory; the command
fails if `nlebench` was imported from anywhere else.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from . import integrity

PROBE_STAGES_EXPECTED = 10          # 4 + 4 + 2 stages are held; see runtime/README.md
STORED_JUDGMENTS_EXPECTED = 4       # 2 cases x 2 scorer files


class Report:
    def __init__(self, name: str) -> None:
        self.name, self.checks, self.failed = name, 0, 0

    def check(self, label: str, want, got) -> None:
        ok = integrity.same_json(want, got)
        self.checks += 1
        self.failed += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: expected {want!r}, got {got!r}")

    def fail(self, label: str) -> None:
        self.checks += 1
        self.failed += 1
        print(f"  FAIL {label}")

    def finish(self) -> int:
        if self.checks == 0:
            print(f"{self.name}: FAIL (nothing was checked)")
            return 1
        print(f"{self.name}: {'PASS' if not self.failed else f'FAIL ({self.failed})'} ({self.checks} checks)")
        return 1 if self.failed else 0


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pins(root: Path) -> dict:
    return load_json(root / "runtime" / "PINS.json")["pins"]


def pin_dir(root: Path, commit: str) -> Path:
    for name, pin in pins(root).items():
        if pin["commit"] == commit:
            return root / "runtime" / "pins" / name
    raise KeyError(f"no pinned modules for commit {commit}")


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------

def count_records(path: Path) -> Counter:
    got: Counter = Counter()
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            got["records"] += 1
            got[f"disposition_{row['d32_disposition']}"] += 1
            got["published_verdict_reproduced"] += row["sub_official"] == row["published_success"]
            if row["sub_official_unenforced"] != row["sub_official"]:
                got["A_prime_changed"] += 1
            if row["arm"] != "feasible":
                continue
            got["feasible"] += 1
            got[f"feasible_disposition_{row['d32_disposition']}"] += 1
            for run, column in (("sub", "sub_official"), ("new", "new_official"), ("dev_v31", "d31s_success"),
                                ("dev_v32", "d32_success")):
                got[f"success_{run}"] += row[column] == "True"

            def step(name: str, before: str, after: str) -> None:
                if row[before] != row[after]:
                    got[f"{name}_changed"] += 1
                    got[f"{name}_{'fail_to_pass' if row[after] == 'True' else 'pass_to_fail'}"] += 1

            step("A", "sub_official", "new_official")
            step("H", "new_official", "d31p_success")
            step("BC", "d31s_success", "d32_success")
            group = {"constraints_only": "B", "input_changed": "C", "unchanged": "U"}[row["membership_v31s_to_v32"]]
            got[f"{group}_feasible"] += 1
            got[f"{group}_rejudged"] += row["d31s_disposition"] == "S" and row["d32_disposition"] == "S"
            if row["d31s_success"] != row["d32_success"]:
                got[f"{group}_changed"] += 1
                got[f"{group}_{'fail_to_pass' if row['d32_success'] == 'True' else 'pass_to_fail'}"] += 1
    return got


def count_sites(path: Path) -> Counter:
    sites: Counter = Counter()
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            sites["sites"] += 1
            sites[f"cause_{row['cause']}"] += 1
            sites[f"{row['cause']}|{row['disposition']}"] += 1
    return sites


def tables(root: Path) -> int:
    report = Report("tables")
    want = load_json(root / "expected.json")["tables"]
    got = count_records(root / "rejudging" / "per_record.csv.gz")
    sites = count_sites(root / "sites" / "candidate_keys.csv")
    if got["records"] == 0 or sites["sites"] == 0:
        report.fail("the per-record file or the site table holds no rows")
        return report.finish()
    for key, value in want["stored_records"].items():
        report.check(key, value, got.get(key, 0))
    for key, value in want["sites"].items():
        report.check(f"sites.{key}", value, sites.get(key, 0))
    # How the processed records divide. These are identities of the recount, not new results.
    for label, parts in load_json(root / "expected_identities.json")["identities"].items():
        report.check(label, parts["total"], sum(got.get(key, 0) for key in parts["sum_of"]))
        if "equals" in parts:
            report.check(f"{label}: the count `{parts['equals']}`", parts["total"], got.get(parts["equals"], 0))
    return report.finish()


# ---------------------------------------------------------------------------
# subprocess on pinned modules
# ---------------------------------------------------------------------------

LOCATION = "import nlebench, os\n_where = os.path.dirname(os.path.realpath(nlebench.__file__))\n"

RUNNER = LOCATION + """
import copy, json, sys
import yaml
from nlebench.dataset.fixtures import build_fixture
from nlebench.runner.constraints import validate_named_constraints
from nlebench.runner.scenario_loader import load_scenario
from nlebench.tools.executor import execute_tool

scenario_path, steps_path = sys.argv[1], sys.argv[2]
doc = yaml.safe_load(open(scenario_path, encoding="utf-8"))
scenario = load_scenario(scenario_path)
out = {"nlebench_dir": _where}
for name, steps in json.load(open(steps_path, encoding="utf-8")).items():
    initial = build_fixture(copy.deepcopy(doc["fixture"]))
    final = build_fixture(copy.deepcopy(doc["fixture"]))
    ok = True
    for step in steps:
        try:
            ok = bool(execute_tool(final, step["tool"], copy.deepcopy(step["params"])).get("success"))
        except Exception:
            ok = False
        if not ok:
            break
    passed = False
    if ok:
        try:
            passed = bool(validate_named_constraints(initial, final, scenario)[0])
        except Exception:
            passed = False
    out[name] = {"all_calls_succeed": ok, "passed": passed}
print(json.dumps(out))
"""

JUDGE = LOCATION + """
import json, sys
import yaml
from nlebench.models import EditProject, Scenario
from nlebench.runner.constraints import validate_named_constraints
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
scenario = Scenario.from_dict(doc)
initial = EditProject.from_json(open(sys.argv[2], encoding="utf-8").read())
final = EditProject.from_json(open(sys.argv[3], encoding="utf-8").read())
print(json.dumps({"nlebench_dir": _where,
                  "passed": bool(validate_named_constraints(initial, final, scenario)[0])}))
"""

PYTEST = LOCATION + """
import json, sys, pytest
out = {}
class Collect:
    def pytest_runtest_logreport(self, report):
        if report.when == 'call' or (report.outcome != 'passed' and report.nodeid not in out):
            out[report.nodeid] = report.outcome
rc = pytest.main(['-q', '-p', 'no:cacheprovider', '--rootdir', sys.argv[1], '-o', 'addopts=',
                  sys.argv[1] + '/tests/test_predicate_fixtures.py',
                  sys.argv[1] + '/tests/test_scorer_contract.py'], plugins=[Collect()])
from nlebench.runner.constraints import PREDICATE_REGISTRY
json.dump({'rc': int(rc), 'outcomes': out, 'predicates': len(PREDICATE_REGISTRY), 'nlebench_dir': _where},
          open(sys.argv[2], 'w'))
"""


def run_on(tree: Path, code: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run code with the given module tree as the only extra import path."""
    return subprocess.run([sys.executable, "-c", code, *args], capture_output=True, cwd=cwd,
                          env={"PYTHONPATH": str(Path(tree).resolve()), "PYTHONDONTWRITEBYTECODE": "1",
                               "PATH": "/usr/bin:/bin"})


def last_json(done: subprocess.CompletedProcess) -> dict:
    if done.returncode != 0:
        raise RuntimeError(done.stderr.decode("utf-8", "replace").strip()[-1200:] or "the subprocess failed")
    return json.loads(done.stdout.decode("utf-8").strip().splitlines()[-1])


def imported_from(tree: Path, got: dict) -> bool:
    return Path(got.get("nlebench_dir", "")) == (Path(tree).resolve() / "nlebench")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def fixtures(root: Path) -> int:
    report = Report("fixtures")
    want = load_json(root / "expected.json")["fixtures"]
    commit = load_json(root / "scorers" / "IDENTITY.json")["scorers"]["current"]["commit"]
    tree = pin_dir(root, commit)
    with tempfile.TemporaryDirectory() as tmp:
        result = Path(tmp) / "result.json"
        done = run_on(tree, PYTEST, str(root / "fixtures"), str(result), cwd=Path(tmp))
        if not result.is_file():
            print(done.stderr.decode("utf-8", "replace").strip()[-1200:])
            report.fail("the fixture run produced no result")
            return report.finish()
        got = load_json(result)
    cases = {node: outcome for node, outcome in got["outcomes"].items() if "::test_predicate_fixture[" in node}
    report.check("modules imported from the pinned export", True, imported_from(tree, got))
    report.check("registered predicates", want["registry_predicates"], got["predicates"])
    report.check("fixture cases run", want["fixture_cases"], len(cases))
    report.check("fixture cases passed", want["fixture_cases"], sum(1 for v in cases.values() if v == "passed"))
    report.check("tests not passed", 0, sum(1 for v in got["outcomes"].values() if v != "passed"))
    report.check("more tests than fixture cases were run (meta-test, fail-closed tests)", True,
                 len(got["outcomes"]) > len(cases))
    report.check("pytest exit code", 0, got["rc"])
    return report.finish()


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------

def cases(root: Path) -> int:
    import yaml
    report = Report("cases")
    replayed_stages = 0
    identities_file = root / "cases" / "SCENARIO_IDENTITIES.json"
    identities = load_json(identities_file)["files"] if identities_file.is_file() else {}
    for case in load_json(root / "expected.json")["cases"]["probe"]:
        folder = root / "cases" / case["scenario"]
        replayed = {}
        for stage in case["stages"]:
            label = f"{case['scenario']}:{stage['stage']}"
            try:
                tree = pin_dir(root, stage["code_commit"])
            except KeyError as error:
                report.fail(f"{label}: {error}")
                continue
            scenario = folder / stage["scenario_file"]
            # The stage records the hash of the source file. The file held here may differ from it in
            # one metadata value; cases/SCENARIO_IDENTITIES.json gives both hashes.
            identity = identities.get(f"cases/{case['scenario']}/{stage['scenario_file']}", {})
            if identity.get("source_sha256") != stage["scenario_sha256"]:
                report.fail(f"{label}: the identity table does not name the recorded source hash")
                continue
            if not scenario.is_file() \
                    or integrity.sha256_file(scenario) != identity.get("sha256_in_this_repository"):
                report.fail(f"{label}: scenario file is missing or does not have the recorded hash")
                continue
            with tempfile.TemporaryDirectory() as tmp:
                steps = Path(tmp) / "steps.json"
                steps.write_text(json.dumps({"reference": case["reference_steps"],
                                             "candidate": case["candidate_steps"]}), encoding="utf-8")
                try:
                    got = last_json(run_on(tree, RUNNER, str(scenario), str(steps), cwd=Path(tmp)))
                except RuntimeError as error:
                    report.fail(f"{label}: could not run on {tree.name}: {str(error).splitlines()[-1]}")
                    continue
            report.check(f"{label} modules imported from {tree.name}", True, imported_from(tree, got))
            for who in ("reference", "candidate"):
                report.check(f"{label} {who}", {"all_calls_succeed": True, "passed": stage[f"{who}_passed"]},
                             got[who])
                replayed[(stage["stage"], who)] = got[who]["passed"]
            replayed_stages += 1
        for item in case.get("stages_not_replayable_from_pinned_files", []):
            print(f"  not held: {case['scenario']}:{item['stage']} ({item['why']})")
        isolation = case.get("constraint_isolation_in_package") or {}
        if isolation.get("available"):
            first, second = isolation["stages"]
            if all((stage, who) in replayed for stage in (first, second) for who in ("candidate", "reference")):
                docs = [yaml.safe_load((folder / name).read_text(encoding="utf-8"))
                        for name in isolation["scenario_files"]]
                differing = sorted(key for key in set(docs[0]) | set(docs[1]) if docs[0].get(key) != docs[1].get(key))
                report.check(f"{case['scenario']}: {first} and {second} scenario files differ only in",
                             ["constraints"], differing)
                for who in ("candidate", "reference"):
                    report.check(f"{case['scenario']}: constraint change alone, {who} [before, after]",
                                 isolation["recorded_verdicts"][who], [replayed[(first, who)], replayed[(second, who)]])
            else:
                report.fail(f"{case['scenario']}: the constraint-isolation pair was not replayed")
    report.check("stages replayed", PROBE_STAGES_EXPECTED, replayed_stages)
    return report.finish()


def stored_cases(root: Path) -> int:
    report = Report("stored-cases")
    identity = load_json(root / "scorers" / "IDENTITY.json")["scorers"]
    harness = pin_dir(root, identity["published"]["commit"])
    judged = 0
    for case in load_json(root / "expected.json")["cases"]["stored_run"]:
        folder = root / "cases" / case["scenario"]
        states = folder / "states"
        for name, recorded in sorted(case["scorers"].items()):
            label = f"{case['scenario']} under the {name} scorer"
            scorer_file = root / identity[name]["file"]
            if not scorer_file.is_file() or integrity.sha256_file(scorer_file) != recorded["sha256"]:
                report.fail(f"{label}: scorer file is missing or does not have the recorded hash")
                continue
            with tempfile.TemporaryDirectory() as tmp:
                tree = Path(tmp) / "tree"
                shutil.copytree(harness, tree)
                (tree / "nlebench" / "runner" / "constraints.py").write_bytes(scorer_file.read_bytes())
                try:
                    got = last_json(run_on(tree, JUDGE, str(folder / "scenario_published.yaml"),
                                           str(states / "initial_state.json"), str(states / "final_state.json"),
                                           cwd=Path(tmp)))
                except RuntimeError as error:
                    report.fail(f"{label}: could not run: {str(error).splitlines()[-1]}")
                    continue
                report.check(f"{label}: modules imported from the assembled tree", True, imported_from(tree, got))
            report.check(label, recorded["passed"], got["passed"])
            judged += 1
    report.check("judgments made", STORED_JUDGMENTS_EXPECTED, judged)
    return report.finish()


# ---------------------------------------------------------------------------
# fingerprint (needs a corpus directory that this repository does not hold)
# ---------------------------------------------------------------------------

IDENTITY_FIELDS = ("fixture", "constraints", "turns")


def fingerprints(corpus: Path, id_key: str) -> dict:
    import yaml
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    per_file, entries = {}, {}
    for path in sorted(corpus.rglob("*.yaml")):
        data = path.read_bytes()
        per_file[str(path.relative_to(corpus))] = hashlib.sha256(data).hexdigest()
        doc = yaml.load(data.decode("utf-8"), Loader=loader)
        key = str(doc.get("legacy_id") or doc["id"]) if id_key == "legacy" else str(doc["id"])
        entries[key] = json.dumps({field: doc.get(field) for field in IDENTITY_FIELDS}, sort_keys=True,
                                  ensure_ascii=False, separators=(",", ":"))
    folded = hashlib.sha256()
    for name in sorted(per_file):
        folded.update(name.encode("utf-8"))
        folded.update(per_file[name].encode("utf-8"))
    canon = hashlib.sha256()
    for key in sorted(entries):
        canon.update(json.dumps([key, entries[key]], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return {"scenario_files": len(per_file), "F_file": folded.hexdigest(), "F_canon": canon.hexdigest()}


def fingerprint(root: Path, corpus: str, name: str) -> int:
    report = Report(f"fingerprint {name}")
    want = load_json(root / "expected.json")["fingerprints"][name]
    got = fingerprints(Path(corpus), want["id_key"])
    for key in ("scenario_files", "F_file", "F_canon"):
        report.check(key, want[key], got[key])
    return report.finish()
