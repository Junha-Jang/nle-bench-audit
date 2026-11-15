"""Track A — per-predicate regression fixtures, three kinds per predicate.

For EVERY predicate in the canonical registry (`PREDICATE_REGISTRY`):

- **normal**      — the predicate passes on a correct final state.
- **boundary**    — a tolerance edge, off-by-one, or empty-set edge, with the
                    verdict pinned on whichever side the contract puts it.
- **adversarial** — a state crafted to fool the predicate: wrong entity of the
                    right type, value moved on the wrong axis, deletion passed
                    off as modification, id reuse, overlay clips masquerading
                    as lane clips. The predicate must not be fooled.

Every case goes through `validate_named_constraints` — the production scoring
entry point — on states built with the shipping fixture mechanism
(`build_fixture` with `{base, patch}` specs). A meta-test enumerates the
registry, so a predicate cannot be added without all three kinds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

import pytest

from nlebench.dataset.fixtures import build_fixture
from nlebench.models import Level, Scenario
from nlebench.runner.constraints import PREDICATE_REGISTRY, SCORER_VERSION, \
    validate_named_constraints

KINDS = ("normal", "boundary", "adversarial")

FixtureSpec = Union[str, dict]


def _patch(base: str, *ops: dict) -> dict:
    return {"base": base, "patch": list(ops)}


def _set(entity: str, field_name: str, value) -> dict:
    return {"op": "set_attribute",
            "params": {"entity": entity, "field": field_name, "value": value}}


def _tool(op: str, **params) -> dict:
    return {"op": op, "params": params}


@dataclass(frozen=True)
class Case:
    constraint: dict
    expect: bool
    why: str
    initial: FixtureSpec = "simple_sequence"
    final: FixtureSpec = "simple_sequence"


# ══════════════════════════════════════════════════════════════════
# The case table — 23 predicates × {normal, boundary, adversarial}
# ══════════════════════════════════════════════════════════════════

CASES: dict[str, dict[str, Case]] = {
    "entity_exists": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _tool("add_caption", track_id="video_track_1",
                               text="Subscribe now", start=6.0, end=8.0)),
            constraint={"entity_exists": {
                "type": "caption",
                "where": {"field": "text", "value": "Subscribe now"}}},
            expect=True,
            why="the requested caption was created with the requested text"),
        "boundary": Case(
            constraint={"entity_exists": {
                "type": "video_clip", "track": "V1", "index": 3}},
            expect=False,
            why="off-by-one edge: index 3 demands a 4th clip; V1 has exactly 3"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _tool("import_media", file_path="/media/broll.mp4")),
            constraint={"entity_exists": {
                "type": "media", "name": "interview.mp4"}},
            expect=False,
            why="a media was imported, but not the one the task named"),
    },
    "entity_not_exists": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _tool("remove_caption", caption_id="caption_1")),
            constraint={"entity_not_exists": {"type": "caption"}},
            expect=True,
            why="the caption was genuinely removed"),
        "boundary": Case(
            initial="with_transitions",
            final="with_transitions",
            constraint={"entity_not_exists": {
                "type": "transition", "between": ["vc_2", "vc_1"]}},
            expect=False,
            why="`between` is a pair-set: reversed order still finds the "
                "existing vc_1/vc_2 transition"),
        "adversarial": Case(
            initial=_patch("simple_sequence",
                           _tool("add_effect", clip_id="video_clip_1",
                                 effect_type="blur")),
            final=_patch("simple_sequence",
                         _tool("add_effect", clip_id="video_clip_2",
                               effect_type="blur")),
            constraint={"entity_not_exists": {
                "type": "effect", "effect_type": "blur"}},
            expect=False,
            why="the blur moved to another clip; it was not removed"),
    },
    "entity_count": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _tool("add_caption", track_id="video_track_1",
                               text="Second caption", start=6.0, end=8.0)),
            constraint={"entity_count": {"type": "caption", "expected": 2}},
            expect=True,
            why="1 fixture caption + 1 added = exactly 2"),
        "boundary": Case(
            final=_patch("simple_sequence",
                         _tool("add_caption", track_id="video_track_1",
                               text="Second caption", start=6.0, end=8.0)),
            constraint={"entity_count": {"type": "caption", "min": 3}},
            expect=False,
            why="off-by-one edge: count 2 is one short of min 3"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _tool("add_clip", track_id="video_track_1",
                               media_id="av_media_1", start=20.0, end=23.0,
                               type="video")),
            constraint={"entity_count": {"type": "caption", "min": 2}},
            expect=False,
            why="an entity of the wrong type was added; captions still 1"),
    },
    "attribute_equals": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_1", "transform.scale_x", 1.1)),
            constraint={"attribute_equals": {
                "entity": "video_clip_1", "field": "transform.scale_x",
                "value": 1.1}},
            expect=True,
            why="the field was set to exactly the requested value"),
        "boundary": Case(
            final=_patch("simple_sequence",
                         _set("audio_clip_4", "volume", -11.6)),
            constraint={"attribute_equals": {
                "entity": "audio_clip_4", "field": "volume", "value": -12.0}},
            expect=True,
            why="tolerance edge: -11.6 is within the ±0.5 dB volume tolerance "
                "of -12.0 (the 50x-too-tight default would have failed this)"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _tool("remove_clip", clip_id="audio_clip_1")),
            constraint={"attribute_equals": {
                "entity": "audio_clip_1", "field": "link_group",
                "value": None}},
            expect=False,
            why="deleting the clip is not setting its link_group to null"),
    },
    "attribute_in_range": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_2", "speed", 1.5)),
            constraint={"attribute_in_range": {
                "entity": "video_clip_2", "field": "speed",
                "min": 1.25, "max": 2.0}},
            expect=True,
            why="1.5 sits inside [1.25, 2.0]"),
        "boundary": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_2", "speed", 1.20)),
            constraint={"attribute_in_range": {
                "entity": "video_clip_2", "field": "speed",
                "min": 1.25, "max": 2.0}},
            expect=False,
            why="off-by-epsilon edge: 1.20 is below min 1.25 by more than the "
                "±0.01 speed tolerance"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_1", "transform.scale_y", 1.3)),
            constraint={"attribute_in_range": {
                "entity": "video_clip_1", "field": "transform.scale_x",
                "min": 1.2, "max": 1.5}},
            expect=False,
            why="value moved on the wrong axis: scale_y changed, scale_x "
                "still 1.0"),
    },
    "attribute_changed": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _set("audio_clip_4", "volume", -14.0)),
            constraint={"attribute_changed": {
                "entity": "audio_clip_4", "field": "volume",
                "direction": "decrease"}},
            expect=True,
            why="volume moved -6.0 → -14.0, the requested direction"),
        "boundary": Case(
            final=_patch("simple_sequence",
                         _set("audio_clip_4", "volume", -6.4)),
            constraint={"attribute_changed": {
                "entity": "audio_clip_4", "field": "volume"}},
            expect=False,
            why="tolerance edge: a 0.4 dB drift is inside the ±0.5 dB volume "
                "tolerance — not a change"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _set("audio_clip_4", "volume", -14.0)),
            constraint={"attribute_changed": {
                "entity": "audio_clip_4", "field": "volume",
                "direction": "increase"}},
            expect=False,
            why="the value moved the wrong way; 'changed' must not absorb "
                "'raised'"),
    },
    "position_equals": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_3", "start", 16.0)),
            constraint={"position_equals": {
                "entity": "video_clip_3", "start": 16.0}},
            expect=True,
            why="the clip starts exactly where asked"),
        "boundary": Case(
            constraint={"position_equals": {
                "entity": "video_clip_3", "start": 15.04}},
            expect=True,
            why="tolerance edge: |15.00 − 15.04| = 0.04 is inside the ±0.05s "
                "time tolerance"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_3", "start", 16.0),
                         _set("video_clip_3", "end", 18.0)),
            constraint={"position_equals": {
                "entity": "video_clip_3", "start": 16.0, "end": 21.0}},
            expect=False,
            why="right start, wrong end: the clip was trimmed, not moved "
                "intact — both pinned edges must hold"),
    },
    "duration_equals": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_1", "end", 3.0)),
            constraint={"duration_equals": {
                "entity": "video_clip_1", "value": 3.0}},
            expect=True,
            why="the clip was trimmed to the requested 3s"),
        "boundary": Case(
            constraint={"duration_equals": {
                "entity": "video_clip_1", "value": 5.04}},
            expect=True,
            why="tolerance edge: |5.00 − 5.04| = 0.04 is inside ±0.05s"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_2", "end", 8.0)),
            constraint={"duration_equals": {
                "entity": "video_clip_1", "value": 3.0}},
            expect=False,
            why="the wrong clip was trimmed; the named clip is still 5s"),
    },
    "order": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_1", "start", 10.0),
                         _set("video_clip_2", "start", 0.0)),
            constraint={"order": {"entities": [
                "video_clip_2", "video_clip_1", "video_clip_3"]}},
            expect=True,
            why="after the swap the starts run 0 < 10 < 15 in the listed order"),
        "boundary": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_2", "start", 0.0)),
            constraint={"order": {"entities": [
                "video_clip_1", "video_clip_2"]}},
            expect=False,
            why="tie edge: equal start times are not an order — strictly "
                "increasing is required"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _tool("remove_clip", clip_id="video_clip_2")),
            constraint={"order": {"expected": [
                "video_clip_1", "video_clip_2", "video_clip_3"],
                "track": "V1"}},
            expect=False,
            why="a listed entity was deleted; an order over a missing member "
                "holds for nothing (also exercises the `expected:` spelling)"),
    },
    "no_overlap": {
        "normal": Case(
            constraint={"no_overlap": {"track": "V1"}},
            expect=True,
            why="V1's lane clips are back-to-back; the caption overlay is not "
                "a lane neighbour"),
        "boundary": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_2", "start", 4.96)),
            constraint={"no_overlap": {"track": "V1"}},
            expect=True,
            why="tolerance edge: a 0.04s overlap is inside the ±0.05s time "
                "tolerance"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_3", "start", 1.0)),
            constraint={"no_overlap": {"track": "V1"}},
            expect=False,
            why="the overlap is between clips that were not neighbours before "
                "the move — the check must re-sort, not trust original order"),
    },
    "no_gap": {
        "normal": Case(
            constraint={"no_gap": {"track": "V1"}},
            expect=True,
            why="V1's lane clips run 0-5-15-20 with no gap"),
        "boundary": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_3", "start", 15.04)),
            constraint={"no_gap": {"track": "V1"}},
            expect=True,
            why="tolerance edge: a 0.04s gap is inside ±0.05s"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_3", "start", 17.0),
                         _tool("add_caption", track_id="video_track_1",
                               text="bridge", start=15.0, end=17.0)),
            constraint={"no_gap": {"track": "V1"}},
            expect=False,
            why="the 15-17s hole was papered over with a caption overlay; "
                "overlays do not fill a lane gap"),
    },
    "reference_intact": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _set("audio_clip_4", "volume", -9.0)),
            constraint={"reference_intact": {"link_id": "link_1"}},
            expect=True,
            why="an unrelated edit left the named link group joined"),
        "boundary": Case(
            constraint={"reference_intact": {"link_id": "link_99"}},
            expect=False,
            why="missing-scope edge: a link that does not exist cannot be "
                "intact — an unresolvable link_id fails closed"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _set("audio_clip_1", "link_group", None)),
            constraint={"reference_intact": {"link_id": "link_1"}},
            expect=False,
            why="id reuse: 'link_1' survives on one clip only — a "
                "half-unlinked group is not intact"),
    },
    "has_effect": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _tool("add_effect", clip_id="video_clip_1",
                               effect_type="blur", params={"amount": 5.0})),
            constraint={"has_effect": {
                "entity": "video_clip_1", "effect_type": "blur"}},
            expect=True,
            why="the requested effect is on the requested clip"),
        "boundary": Case(
            final=_patch("simple_sequence",
                         _tool("add_effect", clip_id="video_clip_1",
                               effect_type="brightness")),
            constraint={"has_effect": {
                "entity": "video_clip_1", "effect_type": "blur"}},
            expect=False,
            why="empty-after-filter edge: an effect exists, but not of the "
                "requested type"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _tool("add_effect", clip_id="video_clip_2",
                               effect_type="blur")),
            constraint={"has_effect": {
                "entity": "video_clip_1", "effect_type": "blur"}},
            expect=False,
            why="right effect, wrong clip of the right type"),
    },
    "has_transition": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _tool("add_transition", clip_id_1="video_clip_1",
                               clip_id_2="video_clip_2",
                               track_id="video_track_1",
                               transition_type="cross_dissolve",
                               duration=1.0)),
            constraint={"has_transition": {
                "between": ["video_clip_1", "video_clip_2"],
                "type": "cross_dissolve"}},
            expect=True,
            why="the requested transition joins the requested pair"),
        "boundary": Case(
            initial="with_transitions",
            final="with_transitions",
            constraint={"has_transition": {
                "track": "V1", "type": "constant_power"}},
            expect=False,
            why="track-scoping edge: constant_power exists on A1 only — the "
                "V1 scope must not leak (and no `between` must not KeyError)"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _tool("add_transition", clip_id_1="video_clip_1",
                               clip_id_2="video_clip_2",
                               track_id="video_track_1",
                               transition_type="cross_dissolve",
                               duration=1.0)),
            constraint={"has_transition": {
                "between": ["video_clip_2", "video_clip_3"]}},
            expect=False,
            why="a transition was added at the wrong cut"),
    },
    "has_link": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _tool("link_clips", video_clip_id="video_clip_3",
                               audio_clip_id="audio_clip_4")),
            constraint={"has_link": {
                "clip_ids": ["video_clip_3", "audio_clip_4"]}},
            expect=True,
            why="the two clips were linked into one group"),
        "boundary": Case(
            constraint={"has_link": {"clip_ids": ["video_clip_1"]}},
            expect=False,
            why="arity edge: a link is a pair — one id asserts nothing and "
                "must not pass vacuously"),
        "adversarial": Case(
            constraint={"has_link": {
                "clip_ids": ["video_clip_1", "audio_clip_2"]}},
            expect=False,
            why="both clips are linked — but to different partners "
                "(link_1 vs link_2), not to each other"),
    },
    "not_linked": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _tool("unlink_clips", link_id="link_1")),
            constraint={"not_linked": {"entity": "video_clip_1"}},
            expect=True,
            why="the clip was genuinely unlinked"),
        "boundary": Case(
            constraint={"not_linked": {"entity": "audio_clip_4"}},
            expect=True,
            why="vacuous edge: a clip that never had a link is unlinked with "
                "zero work — documented absence-predicate behavior"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _tool("remove_clip", clip_id="video_clip_1")),
            constraint={"not_linked": {"entity": "video_clip_1"}},
            expect=False,
            why="deleting the clip is not unlinking it"),
    },
    "unchanged_except": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _set("audio_clip_4", "volume", -14.0)),
            constraint={"unchanged_except": {"changed": ["audio_clip_4"]}},
            expect=True,
            why="the only modified entity is the allowlisted one"),
        "boundary": Case(
            constraint={"unchanged_except": {"changed": []}},
            expect=True,
            why="empty-allowlist edge: an untouched project satisfies the "
                "tightest possible form"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _set("caption_1", "text", "New caption text"),
                         _tool("add_effect", clip_id="video_clip_2",
                               effect_type="blur")),
            constraint={"unchanged_except": {"changed": ["caption_1"]}},
            expect=False,
            why="collateral damage hidden in a clip's effect blocks — the "
                "`native` channel must be compared, not skipped"),
    },
    "state_changed": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _tool("add_caption", track_id="video_track_1",
                               text="Anything", start=6.0, end=7.0)),
            constraint={"state_changed": {}},
            expect=True,
            why="an edit happened"),
        "boundary": Case(
            constraint={"state_changed": {}},
            expect=False,
            why="zero-diff edge: an untouched project has not changed"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _set("audio_clip_4", "audio.volume", -14.0),
                         _set("audio_clip_4", "audio.volume", -6.0)),
            constraint={"state_changed": {}},
            expect=False,
            why="an edit that was reverted nets to no change"),
    },
    "entity_count_changed": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _tool("add_caption", track_id="video_track_1",
                               text="Another", start=6.0, end=7.0)),
            constraint={"entity_count_changed": {
                "type": "caption", "direction": "increase"}},
            expect=True,
            why="caption count went 1 → 2"),
        "boundary": Case(
            final=_patch("simple_sequence",
                         _tool("remove_clip", clip_id="video_clip_3"),
                         _tool("add_clip", track_id="video_track_1",
                               media_id="av_media_3", start=15.0, end=20.0,
                               type="video")),
            constraint={"entity_count_changed": {
                "type": "video_clip", "direction": "increase"}},
            expect=False,
            why="net-zero edge: one removed, one added — churn is not an "
                "increase"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _tool("add_clip", track_id="video_track_1",
                               media_id="av_media_1", start=20.0, end=23.0,
                               type="video")),
            constraint={"entity_count_changed": {
                "type": "caption", "direction": "increase"}},
            expect=False,
            why="an entity of the wrong type was added"),
    },
    "position_changed": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_3", "start", 16.0)),
            constraint={"position_changed": {"entity": "video_clip_3"}},
            expect=True,
            why="the named clip moved by a full second"),
        "boundary": Case(
            final=_patch("simple_sequence",
                         _set("video_clip_3", "start", 15.04)),
            constraint={"position_changed": {"entity": "video_clip_3"}},
            expect=False,
            why="tolerance edge: a 0.04s nudge is inside ±0.05s — not a move"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _tool("remove_clip", clip_id="video_clip_3")),
            constraint={"position_changed": {"entity": "video_clip_3"}},
            expect=False,
            why="deleting the clip is not moving it"),
    },
    "effect_param_changed": {
        "normal": Case(
            initial=_patch("simple_sequence",
                           _tool("add_effect", clip_id="video_clip_1",
                                 effect_type="blur",
                                 params={"amount": 5.0})),
            final=_patch("simple_sequence",
                         _tool("add_effect", clip_id="video_clip_1",
                               effect_type="blur",
                               params={"amount": 12.0})),
            constraint={"effect_param_changed": {
                "entity": "video_clip_1", "effect_type": "blur",
                "param": "amount"}},
            expect=True,
            why="the blur amount moved 5 → 12"),
        "boundary": Case(
            initial=_patch("simple_sequence",
                           _tool("add_effect", clip_id="video_clip_1",
                                 effect_type="blur",
                                 params={"amount": 5.0})),
            final=_patch("simple_sequence",
                         _tool("add_effect", clip_id="video_clip_1",
                               effect_type="blur",
                               params={"amount": 5.0})),
            constraint={"effect_param_changed": {
                "entity": "video_clip_1", "effect_type": "blur",
                "param": "amount"}},
            expect=False,
            why="empty-diff edge: identical params on both sides"),
        "adversarial": Case(
            initial=_patch("simple_sequence",
                           _tool("add_effect", clip_id="video_clip_1",
                                 effect_type="blur", params={"amount": 5.0}),
                           _tool("add_effect", clip_id="video_clip_1",
                                 effect_type="brightness",
                                 params={"amount": 2.0})),
            final=_patch("simple_sequence",
                         _tool("add_effect", clip_id="video_clip_1",
                               effect_type="blur", params={"amount": 5.0}),
                         _tool("add_effect", clip_id="video_clip_1",
                               effect_type="brightness",
                               params={"amount": 9.0})),
            constraint={"effect_param_changed": {
                "entity": "video_clip_1", "effect_type": "blur"}},
            expect=False,
            why="a different effect's parameter moved; the named effect_type "
                "is untouched"),
    },
    "effect_removed": {
        "normal": Case(
            initial=_patch("simple_sequence",
                           _tool("add_effect", clip_id="video_clip_1",
                                 effect_type="blur")),
            final="simple_sequence",
            constraint={"effect_removed": {"effect_type": "blur"}},
            expect=True,
            why="the blur existed initially and none remains"),
        "boundary": Case(
            constraint={"effect_removed": {"effect_type": "blur"}},
            expect=False,
            why="never-existed edge: absence without prior existence is not "
                "removal — a no-op must not pass"),
        "adversarial": Case(
            initial=_patch("simple_sequence",
                           _tool("add_effect", clip_id="video_clip_1",
                                 effect_type="blur"),
                           _tool("add_effect", clip_id="video_clip_2",
                                 effect_type="blur")),
            final=_patch("simple_sequence",
                         _tool("add_effect", clip_id="video_clip_2",
                               effect_type="blur")),
            constraint={"effect_removed": {"effect_type": "blur"}},
            expect=False,
            why="'remove all blur' half-done: one instance survives on "
                "another clip"),
    },
    "batch_operation": {
        "normal": Case(
            final=_patch("simple_sequence",
                         _set("audio_clip_4", "audio.volume", -14.0)),
            constraint={"batch_operation": {"scope": "all_matching"}},
            expect=True,
            why="the state changed (all this predicate verifies — see the "
                "registry discrepancy note)"),
        "boundary": Case(
            constraint={"batch_operation": {"scope": "all_matching"}},
            expect=False,
            why="zero-diff edge: nothing changed"),
        "adversarial": Case(
            final=_patch("simple_sequence",
                         _set("audio_clip_4", "audio.volume", -14.0),
                         _set("audio_clip_4", "audio.volume", -6.0)),
            constraint={"batch_operation": {"scope": "all_matching"}},
            expect=False,
            why="a reverted edit nets to no change"),
    },
}


# ══════════════════════════════════════════════════════════════════
# Meta-test: the registry cannot grow without fixtures
# ══════════════════════════════════════════════════════════════════

def test_every_registered_predicate_has_all_three_fixture_kinds():
    """A predicate added to the registry without a normal, boundary, and
    adversarial fixture case fails here before it ships."""
    missing: list[str] = []
    for name in sorted(PREDICATE_REGISTRY):
        kinds = CASES.get(name, {})
        for kind in KINDS:
            if kind not in kinds:
                missing.append(f"{name}:{kind}")
    assert not missing, f"registered predicates without fixture cases: {missing}"


def test_no_fixture_case_names_an_unregistered_predicate():
    stray = sorted(set(CASES) - set(PREDICATE_REGISTRY))
    assert not stray, f"fixture cases for unregistered predicates: {stray}"


def test_every_case_constrains_the_predicate_it_is_filed_under():
    for name, kinds in CASES.items():
        for kind, case in kinds.items():
            assert list(case.constraint) == [name], f"{name}:{kind}"


# ══════════════════════════════════════════════════════════════════
# The cases themselves
# ══════════════════════════════════════════════════════════════════

ALL_CASES = [(name, kind, CASES[name][kind])
             for name in sorted(CASES) for kind in KINDS if kind in CASES[name]]


def _scenario(constraint: dict, fixture: FixtureSpec) -> Scenario:
    return Scenario(
        id="predicate_fixture",
        name="predicate fixture",
        level=Level.L1,
        category="test",
        description="per-predicate regression fixture",
        fixture=fixture if isinstance(fixture, str) else "simple_sequence",
        user_messages=["predicate fixture"],
        named_constraints_required=[constraint],
    )


@pytest.mark.parametrize(
    "name,kind,case", ALL_CASES, ids=[f"{n}-{k}" for n, k, _ in ALL_CASES])
def test_predicate_fixture(name: str, kind: str, case: Case):
    initial = build_fixture(case.initial)
    final = build_fixture(case.final)
    scenario = _scenario(case.constraint, case.initial)

    passed, result = validate_named_constraints(initial, final, scenario)

    (cr,) = result.results
    assert result.scorer_version == SCORER_VERSION
    # A fixture case must exercise the predicate's verdict, not an evaluation
    # error — except where the contract *is* the error (none here: all cases
    # are schema-valid calls).
    assert cr.error is None, f"{name}:{kind} errored: {cr.error}"
    assert passed is case.expect, (
        f"{name}:{kind} expected {'PASS' if case.expect else 'FAIL'} "
        f"({case.why}); got {'PASS' if passed else 'FAIL'}")
