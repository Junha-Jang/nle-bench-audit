"""Track A — scorer contract hardening: fail-closed dispatch + scorer version.

Three properties, each of which the published benchmark lacked at some point:

1. **Fail-closed dispatch.** A constraint whose predicate name the canonical
   registry does not carry, or whose parameters violate the registered schema
   (unknown key, missing required key, empty one-of group, wrong value type,
   malformed constraint shape), must score as a FAILED constraint — never as a
   silent pass and never as a silent skip. `effect_removed` spent a release
   unregistered and its failures were indistinguishable from model errors;
   these tests keep every such hole closed.

2. **Canonical registry totality.** `PREDICATE_REGISTRY` is the single
   dispatch source: it must cover exactly the registered predicate set, carry
   a non-empty semantics and tolerance line per row, and only declare types
   for keys the contract accepts.

3. **Scorer version.** Every scored result carries `SCORER_VERSION`, derived
   from the registry content, so a stored verdict can always be traced to the
   contract that produced it.
"""
from __future__ import annotations

import dataclasses
import re

import pytest

from nlebench.dataset.fixtures import build_fixture
from nlebench.models import Level, Scenario
from nlebench.runner.constraints import (
    CONSTRAINT_CONTRACTS,
    CONSTRAINT_FUNCTIONS,
    PREDICATE_REGISTRY,
    SCORER_SEMVER,
    SCORER_VERSION,
    _registry_digest,
    validate_named_constraints,
)
from nlebench.runner.validator import validate_scenario


def _scenario(*constraints: dict) -> Scenario:
    return Scenario(
        id="contract_test",
        name="contract test",
        level=Level.L1,
        category="test",
        description="scorer contract hardening",
        fixture="simple_sequence",
        user_messages=["contract test"],
        named_constraints_required=list(constraints),
    )


def _validate(*constraints: dict):
    initial = build_fixture("simple_sequence")
    final = build_fixture("simple_sequence")
    return validate_named_constraints(initial, final, _scenario(*constraints))


# ══════════════════════════════════════════════════════════════════
# 1. Fail-closed dispatch
# ══════════════════════════════════════════════════════════════════

def test_unregistered_predicate_fails_closed():
    """The gate case: a typo'd predicate name must FAIL the validation."""
    passed, result = _validate({"enity_exists": {"entity": "video_clip_1"}})
    assert not passed
    (cr,) = result.results
    assert not cr.passed
    assert cr.error == "Unknown constraint function: enity_exists"


def test_unregistered_predicate_is_not_skipped():
    """An unknown name among known ones still yields one result per constraint."""
    passed, result = _validate(
        {"no_overlap": {"track": "V1"}},
        {"totally_made_up": {}},
    )
    assert not passed
    assert result.total == 2
    assert [cr.passed for cr in result.results] == [True, False]


def test_unknown_parameter_key_fails_closed():
    """Typo defense: `enity_id` must fail, not be silently ignored."""
    passed, result = _validate(
        {"entity_exists": {"type": "clip", "enity_id": "video_clip_1"}})
    assert not passed
    (cr,) = result.results
    assert cr.error and "unknown parameter" in cr.error
    assert "enity_id" in cr.error


def test_missing_required_parameter_fails_closed():
    passed, result = _validate(
        {"attribute_equals": {"entity": "video_clip_1", "field": "volume"}})
    assert not passed
    (cr,) = result.results
    assert cr.error and "missing required parameter" in cr.error


def test_empty_one_of_group_fails_closed():
    passed, result = _validate({"entity_count": {"type": "clip"}})
    assert not passed
    (cr,) = result.results
    assert cr.error and "at least one of" in cr.error


def test_non_mapping_params_fail_closed():
    passed, result = _validate({"no_overlap": "V1"})
    assert not passed
    (cr,) = result.results
    assert cr.error and "must be a mapping" in cr.error


@pytest.mark.parametrize("constraint,expected_fragment", [
    ({"entity_exists": {"type": "clip", "where": "duration > 5"}},
     "'where' expects dict"),
    ({"entity_count": {"type": "caption", "min": "four"}},
     "'min' expects int"),
    ({"entity_count": {"type": "caption", "min": True}},
     "'min' expects int, got bool"),
    ({"attribute_in_range": {"entity": "video_clip_1", "field": "speed",
                             "min": "slow", "max": 2.0}},
     "'min' expects int/float"),
    ({"unchanged_except": {"changed": "video_clip_1"}},
     "'changed' expects list/tuple"),
    ({"has_link": {"clip_ids": "video_clip_1,audio_clip_1"}},
     "'clip_ids' expects list/tuple"),
    ({"entity_exists": {"type": "clip", "index": "2"}},
     "'index' expects int"),
], ids=["where-as-string", "min-as-word", "min-as-bool", "range-min-string",
        "changed-as-string", "clip_ids-as-string", "index-as-string"])
def test_type_violating_parameters_fail_closed(constraint, expected_fragment):
    passed, result = _validate(constraint)
    assert not passed
    (cr,) = result.results
    assert cr.error and expected_fragment in cr.error, cr.error


def test_none_stays_allowed_where_the_corpus_writes_it():
    """`track: null` (unscoped) and `value: null` (assert-empty) are corpus
    idioms; the type layer must not reject them — that would change verdicts."""
    passed, result = _validate(
        {"entity_count": {"type": "video_clip", "expected": 3, "track": None}},
        {"attribute_equals": {"entity": "caption_1", "field": "link_group",
                              "value": None}},
    )
    assert [cr.error for cr in result.results] == [None, None]
    assert passed


def test_malformed_two_key_constraint_fails_closed():
    """A two-key mapping used to evaluate only its first predicate — a silent
    skip of the second. It must now fail as a whole."""
    passed, result = _validate(
        {"no_overlap": {"track": "V1"}, "no_gap": {"track": "V1"}})
    assert not passed
    (cr,) = result.results
    assert cr.func_name == "<malformed>"
    assert cr.error and "single-key mapping" in cr.error


def test_malformed_empty_constraint_fails_closed():
    passed, result = _validate({})
    assert not passed
    (cr,) = result.results
    assert cr.func_name == "<malformed>"


def test_well_formed_constraints_still_pass():
    """Hardening must not tax a correct scenario."""
    passed, result = _validate(
        {"no_overlap": {"track": "V1"}},
        {"entity_count": {"type": "video_clip", "expected": 3}},
        {"entity_exists": {"type": "media", "name": "intro.mp4"}},
    )
    assert passed and result.all_passed and result.passed_count == 3


# ══════════════════════════════════════════════════════════════════
# 2. Canonical registry totality
# ══════════════════════════════════════════════════════════════════

def test_registry_covers_exactly_the_registered_predicates():
    assert set(PREDICATE_REGISTRY) == set(CONSTRAINT_FUNCTIONS)
    assert set(PREDICATE_REGISTRY) == set(CONSTRAINT_CONTRACTS)


def test_registry_rows_reference_the_registered_callables_and_contracts():
    for name, spec in PREDICATE_REGISTRY.items():
        assert spec.name == name
        assert spec.func is CONSTRAINT_FUNCTIONS[name]
        assert spec.contract is CONSTRAINT_CONTRACTS[name]


def test_dispatch_reads_constraint_functions_at_call_time():
    """The published rescore harness (frozen release artifact) computes its
    counterfactual lenses by monkeypatching CONSTRAINT_FUNCTIONS. Dispatch
    must resolve the callable at call time, or those lenses silently collapse
    onto the enforced path (this actually happened when the registry first
    captured callables at import)."""
    calls: list[str] = []
    original = CONSTRAINT_FUNCTIONS["no_overlap"]

    def stub(state, params, tolerance):
        calls.append("stub")
        return False

    CONSTRAINT_FUNCTIONS["no_overlap"] = stub
    try:
        passed, result = _validate({"no_overlap": {"track": "V1"}})
    finally:
        CONSTRAINT_FUNCTIONS["no_overlap"] = original

    assert calls == ["stub"], "monkeypatched callable was not dispatched"
    assert not passed and not result.results[0].passed

    passed_after, _ = _validate({"no_overlap": {"track": "V1"}})
    assert passed_after, "original callable was not restored to dispatch"


def test_every_row_documents_semantics_and_tolerance():
    for name, spec in PREDICATE_REGISTRY.items():
        assert spec.semantics.strip(), f"{name}: empty semantics line"
        assert spec.tolerance.strip(), f"{name}: empty tolerance line"


def test_every_typed_key_is_an_accepted_contract_key():
    for name, spec in PREDICATE_REGISTRY.items():
        typed = {key for key, _ in spec.param_types}
        stray = typed - set(spec.contract.accepted)
        assert not stray, f"{name}: types declared for unaccepted keys {stray}"


def test_every_accepted_key_is_typed_or_deliberately_any():
    """Only `attribute_equals.value` is deliberately untyped (it may be str,
    number, bool, dict, list, or null). Everything else declares types, so a
    new parameter cannot slip in untyped unnoticed."""
    deliberately_any = {("attribute_equals", "value")}
    for name, spec in PREDICATE_REGISTRY.items():
        typed = {key for key, _ in spec.param_types}
        untyped = {(name, key) for key in spec.contract.accepted} - {
            (name, key) for key in typed}
        assert untyped <= deliberately_any, f"untyped parameters: {untyped}"


# ══════════════════════════════════════════════════════════════════
# 3. Scorer version
# ══════════════════════════════════════════════════════════════════

def test_scorer_version_has_the_declared_shape():
    assert re.fullmatch(r"\d+\.\d+\.\d+\+registry\.[0-9a-f]{12}", SCORER_VERSION)
    assert SCORER_VERSION.startswith(SCORER_SEMVER + "+registry.")


def test_eval_result_carries_the_scorer_version():
    _, result = _validate({"no_overlap": {"track": "V1"}})
    assert result.scorer_version == SCORER_VERSION


def test_validation_result_carries_the_scorer_version():
    initial = build_fixture("simple_sequence")
    final = build_fixture("simple_sequence")
    vr = validate_scenario(initial, final,
                           _scenario({"no_overlap": {"track": "V1"}}))
    assert vr.scorer_version == SCORER_VERSION


def test_registry_digest_moves_when_the_contract_moves():
    """The version is content-derived: touching one row's documented contract
    must change the digest; recomputing untouched must not."""
    assert _registry_digest(PREDICATE_REGISTRY) == _registry_digest(
        PREDICATE_REGISTRY)

    modified = dict(PREDICATE_REGISTRY)
    name = "no_overlap"
    modified[name] = dataclasses.replace(
        modified[name], semantics=modified[name].semantics + " (edited)")
    assert _registry_digest(modified) != _registry_digest(PREDICATE_REGISTRY)

    removed = {k: v for k, v in PREDICATE_REGISTRY.items() if k != name}
    assert _registry_digest(removed) != _registry_digest(PREDICATE_REGISTRY)
