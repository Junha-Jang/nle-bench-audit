"""
NLEBench Named Constraint System (v2)

15 named constraint functions + entity binding + global tolerance.
Coexists with legacy ConstraintValidator in validator.py.

Spec reference: scenario-design-spec.md §2, harness-spec.md §5
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, fields as dc_fields
from typing import Any, Optional

from nlebench.models import EditProject, Scenario


# ============================================================
# Global Tolerance Defaults
# ============================================================

DEFAULT_TOLERANCE = {
    "time": 0.05,      # ±0.05s (~1 frame @ 24fps)
    "volume": 0.5,     # ±0.5 dB
    "speed": 0.01,     # ±0.01x
    "position": 2,     # ±2 px
    "default": 0.01,   # other floats
}

# Field name → tolerance category mapping
_TIME_FIELDS = {"start", "end", "in_point", "out_point", "duration"}
_VOLUME_FIELDS = {"volume"}
_SPEED_FIELDS = {"speed"}


def _field_tolerance(field_name: str, tolerance: dict) -> float:
    """Get tolerance for a specific field name."""
    if field_name in _TIME_FIELDS:
        return tolerance.get("time", DEFAULT_TOLERANCE["time"])
    if field_name in _VOLUME_FIELDS:
        return tolerance.get("volume", DEFAULT_TOLERANCE["volume"])
    if field_name in _SPEED_FIELDS:
        return tolerance.get("speed", DEFAULT_TOLERANCE["speed"])
    return tolerance.get("default", DEFAULT_TOLERANCE["default"])


def _merge_tolerance(override: Optional[dict]) -> dict:
    """Merge scenario-level tolerance override with defaults."""
    tol = dict(DEFAULT_TOLERANCE)
    if override:
        tol.update(override)
    return tol


# ============================================================
# Entity Binding
# ============================================================

def _parse_ref(ref: str) -> tuple[str, int]:
    """Parse '$clip_1' → ('clip', 1), '$new_caption_2' → ('caption', 2)."""
    # Strip leading $
    s = ref.lstrip("$")
    # Strip 'new_' / 'existing_' prefix. `new_` was handled but `existing_` was
    # not, so `$existing_effect_1` parsed as type "existing_effect" — a type no
    # mapping knows — and bound to None forever.
    if s.startswith("new_"):
        s = s[4:]
    elif s.startswith("existing_"):
        s = s[9:]
    # Split off trailing number
    m = re.match(r"^(.+?)_(\d+)$", s)
    if m:
        return m.group(1), int(m.group(2))
    return s, 1


def _get_entity_list(state: EditProject, type_name: str) -> list:
    """Map type name to entity list(s) from EditProject."""
    mapping = {
        "clip": state.video_clips + state.audio_clips,
        "video_clip": list(state.video_clips),
        "audio_clip": list(state.audio_clips),
        "caption": list(state.captions),
        "effect": list(state.effects),
        "transition": list(state.transitions),
        "track": state.video_tracks + state.audio_tracks,
        "video_track": list(state.video_tracks),
        "audio_track": list(state.audio_tracks),
        # `av_medias` and `video_medias` are the same predicate (type == "video"),
        # so summing the three legacy lists counted every video media twice and
        # dropped images entirely — an imported .png could never satisfy a
        # `media` constraint. Read the backing collection once instead.
        "media": list(state.media),
        "av_media": list(state.av_medias),
        "video_media": list(state.video_medias),
        "audio_media": list(state.audio_medias),
        "sequence": list(state.sequences),
        "bin": list(state.bins),
        "link": list(state.links),
    }
    return mapping.get(type_name, [])


def _extract_all_refs(scenario: Scenario) -> set[str]:
    """Extract all $ref strings from named constraints."""
    refs: set[str] = set()
    for constraint_list in [scenario.named_constraints_required,
                            scenario.named_constraints_specified]:
        for constraint in constraint_list:
            _collect_refs_from_dict(constraint, refs)
    return refs


def _collect_refs_from_dict(d: Any, refs: set[str]) -> None:
    """Recursively collect $ref strings from a dict/list structure."""
    if isinstance(d, str) and d.startswith("$"):
        refs.add(d)
    elif isinstance(d, dict):
        for v in d.values():
            _collect_refs_from_dict(v, refs)
    elif isinstance(d, list):
        for item in d:
            _collect_refs_from_dict(item, refs)


def bind_entity_refs(state: EditProject, scenario: Scenario) -> dict[str, Optional[str]]:
    """
    Bind $ref expressions to actual entity IDs.

    $clip_1 → first video clip ID (1-based indexing in ref)
    $audio_clip_2 → second audio clip ID
    $new_caption_1 → None (will be bound after agent execution)
    """
    bindings: dict[str, Optional[str]] = {}
    refs = _extract_all_refs(scenario)

    for ref in refs:
        if ref.startswith("$new_"):
            bindings[ref] = None
            continue

        type_name, index = _parse_ref(ref)
        entities = _get_entity_list(state, type_name)
        if index <= len(entities):
            entity = entities[index - 1]
            if hasattr(entity, 'id'):
                bindings[ref] = entity.id
            elif isinstance(entity, dict) and 'id' in entity:
                bindings[ref] = entity['id']
            elif hasattr(entity, 'data') and isinstance(entity.data, dict) and 'id' in entity.data:
                # NativeBlock (effects): id is inside data dict
                bindings[ref] = entity.data['id']
            else:
                bindings[ref] = str(entity)
        else:
            bindings[ref] = None

    return bindings


def rebind_entity_refs(
    state: EditProject,
    scenario: Scenario,
    existing_bindings: dict[str, Optional[str]],
    initial_ids: set[str],
) -> dict[str, Optional[str]]:
    """Re-bind $new_* refs after agent execution to newly created entities."""
    new_bindings = dict(existing_bindings)
    refs = _extract_all_refs(scenario)

    for ref in refs:
        if not ref.startswith("$new_"):
            continue
        type_name, index = _parse_ref(ref)
        entities = _get_entity_list(state, type_name)
        def _entity_id(e):
            if hasattr(e, 'id'): return e.id
            if isinstance(e, dict) and 'id' in e: return e['id']
            if hasattr(e, 'data') and isinstance(e.data, dict) and 'id' in e.data: return e.data['id']
            return str(e)

        new_entities = [e for e in entities if _entity_id(e) not in initial_ids]
        if index <= len(new_entities):
            new_bindings[ref] = _entity_id(new_entities[index - 1])

    return new_bindings


def _resolve_refs(params: Any, bindings: dict[str, Optional[str]]) -> Any:
    """Recursively replace $ref strings with bound IDs."""
    if isinstance(params, str) and params.startswith("$"):
        return bindings.get(params, params)
    if isinstance(params, dict):
        return {k: _resolve_refs(v, bindings) for k, v in params.items()}
    if isinstance(params, list):
        return [_resolve_refs(item, bindings) for item in params]
    return params


# ============================================================
# Helper: resolve track name to entities
# ============================================================

def _resolve_track(state: EditProject, track_ref: str) -> Optional[str]:
    """Resolve track reference like 'V1', 'A2' to actual track ID."""
    for t in state.video_tracks:
        if t.name == track_ref or t.id == track_ref:
            return t.id
    for t in state.audio_tracks:
        if t.name == track_ref or t.id == track_ref:
            return t.id
    # Try matching V{n} pattern → n-th video track, A{n} → n-th audio track
    m = re.match(r"^V(\d+)$", track_ref)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(state.video_tracks):
            return state.video_tracks[idx].id
    m = re.match(r"^A(\d+)$", track_ref)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(state.audio_tracks):
            return state.audio_tracks[idx].id
    return None


def _get_track_by_id(state: EditProject, track_id: str):
    """Get track by ID from any timeline."""
    for timeline in state.timelines:
        for track in timeline.tracks:
            if track.id == track_id:
                return track
    return None


def _get_entities_in_track(
    state: EditProject, entity_type: str, track_ref: Optional[str]
) -> list:
    """Get entities of given type within a track (or all if track is None)."""
    if track_ref is None:
        return _get_entity_list(state, entity_type)

    track_id = _resolve_track(state, track_ref)
    if track_id is None:
        return []

    track = _get_track_by_id(state, track_id)
    if track is None:
        return []

    # For v4.3 schema: clips are inside tracks directly
    if entity_type in ("clip", "video_clip", "audio_clip", "caption"):
        if entity_type == "clip":
            return list(track.clips)
        else:
            clip_type = entity_type.replace("_clip", "") if "_clip" in entity_type else entity_type
            return [c for c in track.clips if c.type == clip_type]
    elif entity_type == "transition":
        return list(track.transitions)
    else:
        # For other entity types, use legacy filtering
        entities = _get_entity_list(state, entity_type)
        return [e for e in entities if getattr(e, "parent_id", None) == track_id]


def _get_clips_in_track(state: EditProject, track_ref: str) -> list:
    """Get all clips (video or audio) in a track, sorted by start time."""
    track_id = _resolve_track(state, track_ref)
    if track_id is None:
        return []

    track = _get_track_by_id(state, track_id)
    if track is None:
        return []

    return sorted(track.clips, key=lambda c: c.start)


def _get_entity(state: EditProject, entity_ref: str) -> Any:
    """Get entity by ID (already resolved from $ref)."""
    return state.get_entity_by_id(entity_ref)


def _resolve_field(entity: Any, field_name: str) -> Any:
    """Resolve a dotted field path on an entity (e.g., 'transform.opacity').

    Traverses attributes or dict keys. Returns None if any segment fails.
    Special handling:
      - `speed` on a Clip: return the scalar rate (float), not the Speed object
      - `audio.X` on a Clip whose `audio` is None: fall back to legacy flat fields
        (`volume`, `muted`) when available.
    """
    if entity is None:
        return None

    # Special: top-level `speed` on a Clip → effective rate as float
    if field_name == "speed" and hasattr(entity, "effective_speed"):
        return float(entity.effective_speed)

    # Special: `audio.X` when clip.audio is None → fall back to legacy flat field
    if field_name.startswith("audio.") and getattr(entity, "audio", None) is None:
        legacy_key = field_name.split(".", 1)[1]
        if hasattr(entity, legacy_key):
            return getattr(entity, legacy_key)

    # Effects are NativeBlocks: `effect_type` is the block type and everything
    # else (`params.*`, `enabled`, `id`) lives inside `data`.
    data = getattr(entity, "data", None)
    if isinstance(data, dict) and hasattr(entity, "type"):
        if field_name == "effect_type":
            return getattr(entity, "effect_type", None) or entity.type
        probe: Any = data
        for part in field_name.split("."):
            if isinstance(probe, dict) and part in probe:
                probe = probe[part]
            else:
                probe = None
                break
        if probe is not None:
            return probe

    current = entity
    for part in field_name.split("."):
        if current is None:
            return None
        if hasattr(current, part):
            current = getattr(current, part)
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


# ============================================================
# Constraint Function Registry
# ============================================================

CONSTRAINT_FUNCTIONS: dict[str, Any] = {}


def register(name: str):
    def decorator(func):
        CONSTRAINT_FUNCTIONS[name] = func
        return func
    return decorator


# --- 2.1 Existence / Count (3) ---

def _entities_under_parent(state: EditProject, entity_type: str, parent_id: str) -> list:
    """Entities of `entity_type` belonging to one parent entity.

    31 constraints scope `entity_not_exists` to a single clip via `parent`, and
    the parameter was never read — so the check ran project-wide and failed
    whenever any effect existed anywhere, not just on the named clip.
    """
    if entity_type == "effect":
        clip = state.get_clip_by_id(parent_id)
        if clip is None:
            return []
        return [nb for nb in clip.native if nb.type not in ("source", "metadata")]
    if entity_type in ("clip", "video_clip", "audio_clip", "caption", "transition"):
        return _get_entities_in_track(state, entity_type, parent_id)
    return [e for e in _get_entity_list(state, entity_type)
            if getattr(e, "parent_id", None) == parent_id]


def _where_field_value(entity: Any, field: str) -> Any:
    """Resolve `where.field` on an entity.

    `_resolve_field` cannot read caption text off a Clip (the text lives on the
    nested `caption`/`title` object), so fall back to those children for
    single-segment field names.
    """
    value = _resolve_field(entity, field)
    if value is not None:
        return value
    if "." not in field:
        for parent in ("caption", "title"):
            child = getattr(entity, parent, None)
            if child is not None:
                value = _resolve_field(child, field)
                if value is not None:
                    return value
    return None


def _as_number(value: Any) -> float | None:
    """Coerce a field value to a float for comparison.

    Time-valued fields (`duration`, `timeline_start`, …) hold `Rational`, which
    is not a `float` and whose dataclass `__eq__` returns False against one.
    Without this coercion a numeric `where` silently degraded to a string
    comparison of `Rational(n=5, d=2)` against `2.5`.
    """
    to_float = getattr(value, "to_float", None)
    if callable(to_float):
        try:
            return float(to_float())
        except (TypeError, ValueError):
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _where_match(entity: Any, where: dict, tolerance: dict) -> bool:
    """Evaluate one `where` clause against an entity.

    Fails closed: an unresolvable field never matches. An unknown operator
    raises rather than silently making the constraint unsatisfiable — a typo in
    an authored scenario must surface as an error, not as a scenario nobody can
    pass.
    """
    field = where["field"]
    op = where.get("op", "==")
    expected = where["value"]
    actual = _where_field_value(entity, field)
    if actual is None:
        return False

    actual_n, expected_n = _as_number(actual), _as_number(expected)
    if actual_n is not None and expected_n is not None:
        tol = _field_tolerance(field, tolerance)
        if op in ("==", "eq"):
            return abs(actual_n - expected_n) <= tol
        if op in ("!=", "ne"):
            return abs(actual_n - expected_n) > tol
        if op == "<=":
            return actual_n <= expected_n + tol
        if op == "<":
            return actual_n < expected_n + tol
        if op == ">=":
            return actual_n >= expected_n - tol
        if op == ">":
            return actual_n > expected_n - tol
        raise ValueError(f"unknown where operator for numeric compare: {op!r}")

    if op in ("==", "eq"):
        return str(actual) == str(expected)
    if op in ("!=", "ne"):
        return str(actual) != str(expected)
    raise ValueError(f"unknown where operator for string compare: {op!r}")


@register("entity_exists")
def entity_exists(state: EditProject, params: dict, tolerance: dict) -> bool:
    # Support two modes:
    #   1. {"entity": "some_id"} — check if entity with this ID exists
    #   2. {"type": "clip", "index": 0} — check if Nth entity of type exists
    #
    # Mode 2 additionally accepts a `where` clause narrowing the candidate list
    # before `index` is applied. Ignoring it made 23 constraints across 16
    # scenarios trivially true — they held in the initial fixture, so the
    # scenario passed without the requested entity ever being created.
    if "entity" in params:
        entity_id = params["entity"]
        return _get_entity(state, entity_id) is not None
    entity_type = params["type"]
    track = params.get("track")
    index = params.get("index", 0)
    parent = params.get("parent")
    if parent is not None:
        entities = _entities_under_parent(state, entity_type, parent)
    else:
        entities = _get_entities_in_track(state, entity_type, track)
    effect_type = params.get("effect_type")
    if effect_type is not None:
        entities = [e for e in entities
                    if (getattr(e, "effect_type", None) or getattr(e, "type", None))
                    == effect_type]
    where = params.get("where")
    if where:
        entities = [e for e in entities if _where_match(e, where, tolerance)]
    return index < len(entities)


@register("entity_not_exists")
def entity_not_exists(state: EditProject, params: dict, tolerance: dict) -> bool:
    # Support same two modes as entity_exists
    if "entity" in params:
        entity_id = params["entity"]
        return _get_entity(state, entity_id) is None
    return not entity_exists(state, params, tolerance)


@register("entity_count")
def entity_count(state: EditProject, params: dict, tolerance: dict) -> bool:
    entity_type = params["type"]
    track = params.get("track")
    entities = _get_entities_in_track(state, entity_type, track)
    return len(entities) == params["expected"]


# --- 2.2 Attribute (3) ---

@register("attribute_equals")
def attribute_equals(state: EditProject, params: dict, tolerance: dict) -> bool:
    entity = _get_entity(state, params["entity"])
    if entity is None:
        return False
    field_name = params["field"]
    value = _resolve_field(entity, field_name)
    if value is None:
        return False
    expected = params["value"]

    # Time-valued fields hold `Rational`, which is neither `int` nor `float` and
    # whose dataclass `__eq__` is False against one. Without coercion a model
    # that set `duration` to exactly 1.5 was scored wrong, because
    # `Rational(n=3, d=2) == 1.5` is False and the comparison fell through to
    # the identity branch below.
    value_n, expected_n = _as_number(value), _as_number(expected)
    if value_n is not None and expected_n is not None:
        tol = _field_tolerance(field_name, tolerance)
        return abs(value_n - expected_n) <= tol
    # Handle nested dict comparison (e.g., style)
    if isinstance(expected, dict) and hasattr(value, "to_dict"):
        return value.to_dict() == expected
    return value == expected


@register("attribute_in_range")
def attribute_in_range(state: EditProject, params: dict, tolerance: dict) -> bool:
    entity = _get_entity(state, params["entity"])
    if entity is None:
        return False
    field_name = params["field"]
    value = _as_number(_resolve_field(entity, field_name))
    if value is None:
        return False
    tol = _field_tolerance(field_name, tolerance)
    return (params["min"] - tol) <= value <= (params["max"] + tol)


@register("attribute_changed")
def attribute_changed(state: EditProject, params: dict, tolerance: dict) -> bool:
    initial = state._initial
    if initial is None:
        return False
    entity = _get_entity(state, params["entity"])
    initial_entity = _get_entity(initial, params["entity"])
    if entity is None:
        # The entity was deleted. This used to return True (`None is not <obj>`),
        # so "raise the BGM" was satisfied by removing the BGM and "move this
        # clip to the new track" by deleting the clip — the constraint passed
        # while the project ended up the opposite of what was asked.
        return False
    if initial_entity is None:
        # The entity was created during the run. There is no baseline value, so
        # the field genuinely went from nothing to something.
        # ⚠ `direction` cannot be enforced here for want of a baseline; a task
        # that needs a bound on a new entity's value should use
        # `attribute_equals` or `position_equals` instead.
        return True
    field_name = params["field"]
    current_val = _resolve_field(entity, field_name)
    initial_val = _resolve_field(initial_entity, field_name)
    # `direction` says which way the value must move. It was never read, so
    # "raise the volume" and "lower the volume" scored identically and a model
    # that moved the value the wrong way passed — a false success, across the
    # 115 constraints that declare a direction. `entity_count_changed` has
    # honoured its own `direction` all along; only this predicate did not.
    direction = params.get("direction")
    cur_n, ini_n = _as_number(current_val), _as_number(initial_val)
    if cur_n is not None and ini_n is not None:
        tol = _field_tolerance(field_name, tolerance)
        if direction == "increase":
            return cur_n - ini_n > tol
        if direction == "decrease":
            return ini_n - cur_n > tol
        return abs(cur_n - ini_n) > tol
    if direction in ("increase", "decrease"):
        return False   # a direction is meaningless on a non-numeric field
    return current_val != initial_val


# --- 2.3 Time/Position (3) ---

@register("position_equals")
def position_equals(state: EditProject, params: dict, tolerance: dict) -> bool:
    entity = _get_entity(state, params["entity"])
    if entity is None:
        return False
    time_tol = tolerance.get("time", DEFAULT_TOLERANCE["time"])
    ok = True
    for key in ("start", "end"):
        want = params.get(key)
        if want is None:
            continue
        got, want_n = _as_number(_resolve_field(entity, key)), _as_number(want)
        if got is None or want_n is None:
            return False
        ok = ok and abs(got - want_n) <= time_tol
    return ok


@register("duration_equals")
def duration_equals(state: EditProject, params: dict, tolerance: dict) -> bool:
    entity = _get_entity(state, params["entity"])
    if entity is None:
        return False
    time_tol = tolerance.get("time", DEFAULT_TOLERANCE["time"])
    end, start = _as_number(_resolve_field(entity, "end")), _as_number(_resolve_field(entity, "start"))
    want = _as_number(params["value"])
    if end is None or start is None or want is None:
        return False
    return abs((end - start) - want) <= time_tol


@register("order")
def order_constraint(state: EditProject, params: dict, tolerance: dict) -> bool:
    entity_ids = params["entities"]
    entities = [_get_entity(state, eid) for eid in entity_ids]
    if any(e is None for e in entities):
        return False
    for i in range(len(entities) - 1):
        if entities[i].start >= entities[i + 1].start:
            return False
    return True


# --- 2.4 Structural (3) ---

@register("no_overlap")
def no_overlap(state: EditProject, params: dict, tolerance: dict) -> bool:
    track_ref = params["track"]
    clips = _get_clips_in_track(state, track_ref)
    time_tol = tolerance.get("time", DEFAULT_TOLERANCE["time"])
    for i in range(len(clips) - 1):
        if clips[i].end > clips[i + 1].start + time_tol:
            return False
    return True


@register("no_gap")
def no_gap(state: EditProject, params: dict, tolerance: dict) -> bool:
    track_ref = params["track"]
    clips = _get_clips_in_track(state, track_ref)
    time_tol = tolerance.get("time", DEFAULT_TOLERANCE["time"])
    for i in range(len(clips) - 1):
        gap = clips[i + 1].start - clips[i].end
        if gap > time_tol:
            return False
    return True


@register("reference_intact")
def reference_intact(state: EditProject, params: dict, tolerance: dict) -> bool:
    media_ids = {m.id for m in state.media}

    # Check all clips have valid media references (v4.3: ref_id instead of media_id)
    for clip in state.video_clips + state.audio_clips:
        ref_id = getattr(clip, "ref_id", None) or getattr(clip, "media_id", None)
        if ref_id and ref_id not in media_ids:
            # Check if ref_id might be a timeline reference (nested timelines)
            timeline_ids = {t.id for t in state.timelines}
            if ref_id not in timeline_ids:
                return False

    clip_ids = {c.id for c in state.video_clips + state.audio_clips + state.captions}

    # Check transitions reference valid clips
    for transition in state.transitions:
        before_id = getattr(transition, "clip_before_id", None) or getattr(transition, "clip_id_1", None)
        after_id = getattr(transition, "clip_after_id", None) or getattr(transition, "clip_id_2", None)
        if before_id and before_id not in clip_ids:
            return False
        if after_id and after_id not in clip_ids:
            return False

    return True


# --- 2.5 Relation (2) ---

@register("has_effect")
def has_effect(state: EditProject, params: dict, tolerance: dict) -> bool:
    entity_id = params["entity"]
    effect_type = params.get("effect_type")

    # In v4.3, effects are NativeBlocks on clips - find the clip first
    clip = state.get_clip_by_id(entity_id)
    if clip is None:
        return False

    for nb in clip.native:
        if nb.type not in ("source", "metadata"):
            # Check effect type if specified
            eff_type = getattr(nb, "effect_type", None) or nb.type
            if effect_type is None or eff_type == effect_type:
                return True
    return False


@register("has_transition")
def has_transition(state: EditProject, params: dict, tolerance: dict) -> bool:
    ids = params["between"]
    transition_type = params.get("type")
    for t in state.transitions:
        before_id = getattr(t, "clip_before_id", None) or getattr(t, "clip_id_1", None)
        after_id = getattr(t, "clip_after_id", None) or getattr(t, "clip_id_2", None)
        if {before_id, after_id} == set(ids):
            tr_type = getattr(t, "type", None) or getattr(t, "transition_type", None)
            if transition_type is None or tr_type == transition_type:
                return True
    return False


@register("has_link")
def has_link(state: EditProject, params: dict, tolerance: dict) -> bool:
    """Check that two clips share the same link_group."""
    clip_ids = params["clip_ids"]
    if len(clip_ids) != 2:
        return False
    clip1 = state.get_clip_by_id(clip_ids[0])
    clip2 = state.get_clip_by_id(clip_ids[1])
    if clip1 is None or clip2 is None:
        return False
    if not clip1.link_group or not clip2.link_group:
        return False
    return clip1.link_group == clip2.link_group


@register("not_linked")
def not_linked(state: EditProject, params: dict, tolerance: dict) -> bool:
    """Check that a clip has no link_group (is unlinked)."""
    entity_id = params["entity"]
    clip = state.get_clip_by_id(entity_id)
    if clip is None:
        return False
    return clip.link_group is None


# --- 2.6 Global (1) ---

@register("unchanged_except")
def unchanged_except(state: EditProject, params: dict, tolerance: dict) -> bool:
    initial = state._initial
    if initial is None:
        return True  # No initial state to compare

    changed_ids = set(params.get("changed", []))

    # Entity types that have .id attribute
    entity_list_names = [
        "video_clips", "audio_clips", "captions",
        "transitions", "video_tracks", "audio_tracks",
        "sequences", "bins", "av_medias", "video_medias",
        "audio_medias", "media",
    ]

    for list_name in entity_list_names:
        current_list = getattr(state, list_name, [])
        original_list = getattr(initial, list_name, [])

        # Only process if entities have .id attribute
        if not current_list and not original_list:
            continue

        current = {}
        for e in current_list:
            eid = getattr(e, "id", None)
            if eid:
                current[eid] = e

        original = {}
        for e in original_list:
            eid = getattr(e, "id", None)
            if eid:
                original[eid] = e

        # Deleted entities not in changed set → violation
        for eid in original:
            if eid not in current and eid not in changed_ids:
                return False
        # Added entities not in changed set → violation
        for eid in current:
            if eid not in original and eid not in changed_ids:
                return False
        # Modified entities not in changed set → violation
        for eid in current:
            if eid in original and eid not in changed_ids:
                if not _entities_equal(current[eid], original[eid], tolerance):
                    return False

    return True


@register("state_changed")
def state_changed(state: EditProject, params: dict, tolerance: dict) -> bool:
    """Check that the final state differs from initial state in any way."""
    initial = state._initial
    if initial is None:
        return True
    return state.to_dict() != initial.to_dict()


@register("entity_count_changed")
def entity_count_changed_fn(state: EditProject, params: dict, tolerance: dict) -> bool:
    """Check that entity count changed in the expected direction."""
    initial = state._initial
    if initial is None:
        return True
    entity_type = params.get("type", "clip")
    direction = params.get("direction", "increase")

    current = len(_get_entity_list(state, entity_type))
    original = len(_get_entity_list(initial, entity_type))

    if direction == "increase":
        return current > original
    elif direction == "decrease":
        return current < original
    return current != original


@register("position_changed")
def position_changed_fn(state: EditProject, params: dict, tolerance: dict) -> bool:
    """Check that an entity's position changed."""
    initial = state._initial
    if initial is None:
        return True
    # Just check that state differs - generic fallback
    return state.to_dict() != initial.to_dict()


@register("effect_param_changed")
def effect_param_changed_fn(state: EditProject, params: dict, tolerance: dict) -> bool:
    """Check that an effect parameter changed."""
    # Simplified: just check state changed
    initial = state._initial
    if initial is None:
        return True
    return state.to_dict() != initial.to_dict()


@register("batch_operation")
def batch_operation(state: EditProject, params: dict, tolerance: dict) -> bool:
    """Meta-constraint: batch operation was applied. Always passes if state changed."""
    initial = state._initial
    if initial is None:
        return True
    return state.to_dict() != initial.to_dict()


def _entities_equal(a: Any, b: Any, tolerance: dict) -> bool:
    """Compare two entities with field-specific tolerance.

    Skips nested list fields (clips, tracks, transitions, native, bins)
    since those are compared separately at their own entity level.
    """
    if type(a) is not type(b):
        return False

    # Fields that contain child entities - skip these
    nested_fields = {"clips", "tracks", "transitions", "native", "bins", "media", "timelines"}

    for f in dc_fields(a):
        if f.name.startswith("_"):
            continue
        if f.name in nested_fields:
            continue  # Skip nested entity lists
        v1 = getattr(a, f.name, None)
        v2 = getattr(b, f.name, None)
        if isinstance(v1, float) and isinstance(v2, float):
            tol = _field_tolerance(f.name, tolerance)
            if abs(v1 - v2) > tol:
                return False
        elif isinstance(v1, list) and isinstance(v2, list):
            # Skip other lists (like media_ids, timeline_ids)
            continue
        elif v1 != v2:
            return False
    return True


# ============================================================
# Constraint Evaluator
# ============================================================

@dataclass
class ConstraintResult:
    """Result of a single constraint evaluation."""
    constraint: dict
    passed: bool
    func_name: str
    error: Optional[str] = None


@dataclass
class EvalResult:
    """Result of evaluating all constraints for a scenario."""
    all_passed: bool
    results: list[ConstraintResult]
    total: int
    passed_count: int

    @property
    def failed(self) -> list[ConstraintResult]:
        return [r for r in self.results if not r.passed]


def evaluate_constraints(
    state: EditProject,
    scenario: Scenario,
    bindings: dict[str, Optional[str]],
) -> EvalResult:
    """
    Evaluate all named constraints for a scenario.

    Uses state._initial for attribute_changed / unchanged_except.
    """
    tolerance = _merge_tolerance(scenario.tolerance_override)
    results: list[ConstraintResult] = []

    all_constraints = (
        scenario.named_constraints_required
        + scenario.named_constraints_specified
    )

    for constraint in all_constraints:
        # Each constraint is a dict like {"entity_exists": {"type": "clip", ...}}
        func_name = next(iter(constraint))
        raw_params = constraint[func_name]

        # Resolve $ref → actual IDs
        params = _resolve_refs(raw_params or {}, bindings)

        func = CONSTRAINT_FUNCTIONS.get(func_name)
        if func is None:
            results.append(ConstraintResult(
                constraint=constraint,
                passed=False,
                func_name=func_name,
                error=f"Unknown constraint function: {func_name}",
            ))
            continue

        try:
            passed = func(state, params, tolerance)
            results.append(ConstraintResult(
                constraint=constraint,
                passed=passed,
                func_name=func_name,
            ))
        except Exception as e:
            results.append(ConstraintResult(
                constraint=constraint,
                passed=False,
                func_name=func_name,
                error=str(e),
            ))

    return EvalResult(
        all_passed=all(r.passed for r in results),
        results=results,
        total=len(results),
        passed_count=sum(1 for r in results if r.passed),
    )


def evaluate_with_snapshot(
    state: EditProject,
    scenario: Scenario,
    bindings: dict[str, Optional[str]],
    initial_state: EditProject,
) -> EvalResult:
    """Evaluate constraints with an explicit initial state snapshot."""
    state._initial = initial_state
    return evaluate_constraints(state, scenario, bindings)


# ============================================================
# Convenience: validate a scenario end-to-end (named constraints)
# ============================================================

def validate_named_constraints(
    initial_state: EditProject,
    final_state: EditProject,
    scenario: Scenario,
) -> tuple[bool, EvalResult]:
    """
    High-level validation for scenarios using named constraints.

    Returns:
        (tsr, eval_result) — TSR is True if all constraints pass.
    """
    # Snapshot initial state
    final_state._initial = initial_state

    # Bind entity refs from INITIAL state (so deleted entities still resolve)
    bindings = bind_entity_refs(initial_state, scenario)

    # Also try to rebind $new_* refs
    initial_ids = set(initial_state.collect_all_ids())
    bindings = rebind_entity_refs(final_state, scenario, bindings, initial_ids)

    # Evaluate
    result = evaluate_constraints(final_state, scenario, bindings)
    return result.all_passed, result
