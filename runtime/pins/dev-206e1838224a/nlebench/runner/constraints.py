"""
NLEBench Named Constraint System (v2)

15 named constraint functions + entity binding + global tolerance.
Coexists with legacy ConstraintValidator in validator.py.

Spec reference: scenario-design-spec.md §2, harness-spec.md §5
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, fields as dc_fields
from functools import wraps
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
    """Get tolerance for a specific field name.

    Match on the last path segment: the field-specific tables list bare names
    (`volume`, `duration`), but scenarios address nested fields by path, so
    `audio.volume` missed the volume entry and silently fell back to the default
    0.01 instead of 0.5 dB — a 50x tighter bar than intended, on the field that
    most volume constraints in the set actually use.
    """
    field_name = field_name.rsplit(".", 1)[-1]
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


def _entity_list_mapping(state: EditProject) -> dict[str, list]:
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
        # `timeline` was addressable nowhere, so `entity_count_changed
        # {type: timeline}` compared 0 against 0 and could never pass.
        "timeline": list(state.timelines),
    }
    return mapping


# The entity types a `type:` parameter may name. A scenario that names anything
# else is an authoring error, and the predicates below say so out loud —
# `_get_entity_list` returning `[]` for a typo made `entity_count_changed
# {type: marker}` a constraint no run could ever satisfy, silently.
KNOWN_ENTITY_TYPES: frozenset[str] = frozenset(_entity_list_mapping(EditProject()))


def _get_entity_list(state: EditProject, type_name: str) -> list:
    """Entities of `type_name`; empty for a type this schema does not model.

    Kept lenient because `bind_entity_refs` feeds it ref-derived names like
    `target_clip` that are not entity types at all. Predicates reading a
    scenario-authored `type:` go through `_require_entity_type` first.
    """
    return _entity_list_mapping(state).get(type_name, [])


def _require_entity_type(entity_type: Any) -> str:
    if entity_type not in KNOWN_ENTITY_TYPES:
        raise ValueError(
            f"unknown entity type: {entity_type!r} "
            f"(known: {', '.join(sorted(KNOWN_ENTITY_TYPES))})")
    return entity_type


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
    elif entity_type == "effect":
        # Effects are NativeBlocks on clips, never children of a track, so the
        # `parent_id` fallback below found none of them: `entity_not_exists
        # {type: effect, track: V1}` was true before the agent removed anything.
        return [nb for c in track.clips for nb in c.native
                if nb.type not in ("source", "metadata")]
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


def _resolve_field_present(entity: Any, field_name: str) -> tuple[bool, Any]:
    """`(present, value)` — tells "no such field" apart from "the field is None".

    `_resolve_field` collapses both to `None`, so `attribute_equals {field:
    link_group, value: null}` — "this clip must end up unlinked" — could not be
    satisfied by any run: the correct final state resolves to `None` and the
    predicate reads that as "unreadable field" and fails.
    """
    value = _resolve_field(entity, field_name)
    if value is not None:
        return True, value
    if entity is None:
        return False, None
    probe: Any = entity
    for part in field_name.split("."):
        if hasattr(probe, part):
            probe = getattr(probe, part)
        elif isinstance(probe, dict) and part in probe:
            probe = probe[part]
        else:
            return False, None
        if probe is None:
            # A None mid-path is only an answer at the last segment.
            return field_name.split(".")[-1] == part, None
    return True, probe


# ============================================================
# Constraint Function Registry
# ============================================================

CONSTRAINT_FUNCTIONS: dict[str, Any] = {}


@dataclass(frozen=True)
class PredicateContract:
    """The parameter contract of one named constraint, declared beside its code.

    ★ Why this exists. A predicate that reads `params.get("x")` and a scenario
    that writes some other key agree on nothing, and nothing said so. Across the
    v2 corpus 23 constraints named an entity by `name`, 7 bounded a count by
    `min`, 6 scoped an integrity check by `link_id`, 16 named the entity whose
    position moved — and every one of those keys went unread, turning a specific
    demand into a weaker one the initial fixture usually already satisfied.
    Declaring the contract here lets `register` reject an unread parameter at
    evaluation time, and lets the audit tool compare declaration against corpus
    without a second hand-maintained table that could drift from either.

    - `required` — must be present.
    - `one_of` — each group needs at least one member present (the modes a
      predicate supports, e.g. address an entity by id *or* describe it).
    - `optional` — read when present.
    - `documentation_only` — accepted and deliberately **not** read. Prose for
      the scenario author. Listing them keeps "we ignore this on purpose" apart
      from "we forgot to read this", which is the whole point.
    """
    required: tuple[str, ...] = ()
    one_of: tuple[tuple[str, ...], ...] = ()
    optional: tuple[str, ...] = ()
    documentation_only: tuple[str, ...] = ()

    @property
    def accepted(self) -> frozenset[str]:
        return frozenset(
            self.required + self.optional + self.documentation_only
            + tuple(k for group in self.one_of for k in group))


CONSTRAINT_CONTRACTS: dict[str, PredicateContract] = {}


def _validate_params(name: str, contract: PredicateContract, params: Any) -> None:
    """Reject a call the predicate cannot honour, loudly."""
    if not isinstance(params, dict):
        raise ValueError(f"{name}: parameters must be a mapping, got {type(params).__name__}")
    keys = set(params)
    unknown = sorted(keys - contract.accepted)
    if unknown:
        raise ValueError(
            f"{name}: unknown parameter(s) {unknown} "
            f"(accepted: {', '.join(sorted(contract.accepted)) or 'none'})")
    missing = [r for r in contract.required if r not in keys]
    if missing:
        raise ValueError(f"{name}: missing required parameter(s) {missing}")
    for group in contract.one_of:
        if not keys.intersection(group):
            raise ValueError(
                f"{name}: needs at least one of {list(group)}")


def register(name: str, contract: Optional[PredicateContract] = None):
    """Register a named constraint behind its declared parameter contract.

    The registry entry is the *checked* callable; the undecorated function stays
    the module-level name so predicates that delegate to one another (e.g.
    `entity_not_exists` → `entity_exists`) do not re-validate an already
    validated call under the wrong predicate name.
    """
    def decorator(func):
        c = contract if contract is not None else PredicateContract()
        CONSTRAINT_CONTRACTS[name] = c

        @wraps(func)
        def checked(state: EditProject, params: Any, tolerance: dict) -> bool:
            _validate_params(name, c, params)
            return func(state, params, tolerance)

        CONSTRAINT_FUNCTIONS[name] = checked
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


def _entity_name(entity: Any) -> Optional[str]:
    """The human-facing name a scenario means by `name:`.

    Media carry both a `name` and a `path`; scenarios name the file
    (`interview.mp4`), which may only appear as the tail of the path.
    """
    name = getattr(entity, "name", None)
    if name is not None:
        return str(name)
    path = getattr(entity, "path", None)
    if path:
        return str(path).rsplit("/", 1)[-1]
    return None


def _name_matches(entity: Any, wanted: str) -> bool:
    if _entity_name(entity) == wanted:
        return True
    path = getattr(entity, "path", None)
    return bool(path) and str(path).rsplit("/", 1)[-1] == wanted


def _transition_endpoints(transition: Any) -> set:
    """The two clip ids a transition joins, across both field spellings."""
    before = (getattr(transition, "clip_before_id", None)
              or getattr(transition, "clip_id_1", None))
    after = (getattr(transition, "clip_after_id", None)
             or getattr(transition, "clip_id_2", None))
    return {before, after}


def _transition_joins(transition: Any, between: Any) -> bool:
    return _transition_endpoints(transition) == set(between)


def _narrow_entity_type(entity_type: str, kind: Optional[str]) -> str:
    """`{type: track, kind: audio}` → `audio_track`."""
    if kind is None:
        return _require_entity_type(entity_type)
    return _require_entity_type(f"{kind}_{entity_type}")


_EXISTS_CONTRACT = PredicateContract(
    one_of=(("entity", "type"),),
    optional=("track", "index", "parent", "effect_type", "where", "name",
              "kind", "between"),
)


@register("entity_exists", _EXISTS_CONTRACT)
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
    entity_type = _narrow_entity_type(params["type"], params.get("kind"))
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
    # `name` picks out *which* entity the task asked for. Unread, "import a
    # media called interview.mp4" scored as "the project has some media", which
    # every fixture already satisfied — 20 v2 constraints passed on no work.
    name = params.get("name")
    if name is not None:
        if entity_type in ("track", "video_track", "audio_track"):
            # A scenario says `name: V2` for the slot the editor shows as
            # "V2 (B-Roll)". Literal string equality would answer "there is no
            # track called V2" about a project that plainly has one, so the
            # slot convention resolves the same way it does everywhere else.
            slot_id = _resolve_track(state, name)
            entities = [e for e in entities
                        if getattr(e, "id", None) == slot_id or _name_matches(e, name)]
        else:
            entities = [e for e in entities if _name_matches(e, name)]
    # `between` names the pair a transition must join.
    between = params.get("between")
    if between is not None:
        entities = [e for e in entities if _transition_joins(e, between)]
    where = params.get("where")
    if where:
        entities = [e for e in entities if _where_match(e, where, tolerance)]
    return index < len(entities)


@register("entity_not_exists", _EXISTS_CONTRACT)
def entity_not_exists(state: EditProject, params: dict, tolerance: dict) -> bool:
    # Support same two modes as entity_exists
    if "entity" in params:
        entity_id = params["entity"]
        if entity_id is None:
            # An unbindable `$ref`. Answering "true, it does not exist" turns a
            # binding failure into a pass; the scenario asked about a specific
            # entity and we could not find out which one.
            raise ValueError("entity_not_exists: `entity` did not bind to an id")
        return _get_entity(state, entity_id) is None
    return not entity_exists(state, params, tolerance)


@register("entity_count", PredicateContract(
    required=("type",), one_of=(("expected", "min", "max"),),
    optional=("track",)))
def entity_count(state: EditProject, params: dict, tolerance: dict) -> bool:
    """Exact count via `expected`, or a bound via `min` / `max`.

    `min` was never read and `expected` was indexed unguarded, so the 7 v2
    constraints written as `{type: caption, min: 4}` raised `KeyError` and were
    scored as failures no run could avoid.
    """
    entity_type = _require_entity_type(params["type"])
    track = params.get("track")
    n = len(_get_entities_in_track(state, entity_type, track))
    if "expected" in params:
        return n == params["expected"]
    if "min" in params and n < params["min"]:
        return False
    if "max" in params and n > params["max"]:
        return False
    return True


# --- 2.2 Attribute (3) ---

@register("attribute_equals", PredicateContract(required=("entity", "field", "value")))
def attribute_equals(state: EditProject, params: dict, tolerance: dict) -> bool:
    entity = _get_entity(state, params["entity"])
    if entity is None:
        return False
    field_name = params["field"]
    expected = params["value"]
    if expected is None:
        # "this field must end up empty" — e.g. `link_group: null` after an
        # unlink. Reading a `None` result as "unreadable field" made those two
        # v2 constraints unsatisfiable by construction.
        present, value = _resolve_field_present(entity, field_name)
        return present and value is None
    value = _resolve_field(entity, field_name)
    if value is None:
        return False

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


@register("attribute_in_range",
          PredicateContract(required=("entity", "field", "min", "max")))
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


@register("attribute_changed", PredicateContract(
    required=("entity", "field"), optional=("direction",)))
def attribute_changed(state: EditProject, params: dict, tolerance: dict) -> bool:
    initial = _require_baseline(state, "attribute_changed")
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

@register("position_equals", PredicateContract(
    required=("entity",), one_of=(("start", "end"),)))
def position_equals(state: EditProject, params: dict, tolerance: dict) -> bool:
    # A call that pins neither edge asserts nothing and would return True for
    # any state at all. No scenario in the corpus does it; the guard keeps it
    # that way, because both keys are individually optional.
    if params.get("start") is None and params.get("end") is None:
        raise ValueError("position_equals: needs a non-null `start` or `end`")
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


@register("duration_equals", PredicateContract(required=("entity", "value")))
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


@register("order", PredicateContract(
    one_of=(("entities", "expected"),), optional=("track",)))
def order_constraint(state: EditProject, params: dict, tolerance: dict) -> bool:
    """The listed entities must appear in the listed order along the timeline.

    Scenarios spell the list two ways — `entities:` and, when the list is
    scoped to a track, `expected:`. Only the first was read, so the one v2
    constraint written the second way raised `KeyError('entities')` and could
    never pass. `track`, when given, additionally requires that every listed
    entity actually sits on that track.
    """
    entity_ids = params.get("entities")
    if entity_ids is None:
        entity_ids = params["expected"]
    entities = [_get_entity(state, eid) for eid in entity_ids]
    if any(e is None for e in entities):
        return False
    track_ref = params.get("track")
    if track_ref is not None:
        in_track = {getattr(c, "id", None) for c in _get_clips_in_track(state, track_ref)}
        if not all(getattr(e, "id", None) in in_track for e in entities):
            return False
    for i in range(len(entities) - 1):
        if entities[i].start >= entities[i + 1].start:
            return False
    return True


# --- 2.4 Structural (3) ---

# Captions and titles ride *over* a video track by design — `add_caption`
# targets a video track, and the caption is meant to sit on top of the clip it
# annotates. Counting them as neighbours in the timeline lane makes
# `no_overlap` unsatisfiable for any captioned track: on the stock
# `simple_sequence`, `no_overlap {track: V1}` is False before an agent touches
# anything, because `caption_1` (2.0–5.0) overlays `video_clip_1` (0.0–5.0).
_OVERLAY_CLIP_TYPES = frozenset({"caption", "title"})


def _lane_clips(state: EditProject, track_ref: str) -> list:
    """Clips that share a timeline lane, in start order — overlays excluded."""
    return [c for c in _get_clips_in_track(state, track_ref)
            if getattr(c, "type", None) not in _OVERLAY_CLIP_TYPES]


def _require_track(state: EditProject, track_ref: Any) -> str:
    """Resolve a track reference or say so.

    `_get_clips_in_track` returns `[]` both for "that track is empty" and for
    "there is no such track", and every structural predicate reads an empty list
    as "no violation found". A track name that does not resolve therefore
    reported a clean timeline.
    """
    if not isinstance(track_ref, str):
        raise ValueError(f"track reference must be a string, got {track_ref!r}")
    track_id = _resolve_track(state, track_ref)
    if track_id is None:
        raise ValueError(f"unresolvable track reference: {track_ref!r}")
    return track_id


@register("no_overlap", PredicateContract(required=("track",)))
def no_overlap(state: EditProject, params: dict, tolerance: dict) -> bool:
    track_ref = params["track"]
    _require_track(state, track_ref)
    clips = _lane_clips(state, track_ref)
    time_tol = tolerance.get("time", DEFAULT_TOLERANCE["time"])
    for i in range(len(clips) - 1):
        if clips[i].end > clips[i + 1].start + time_tol:
            return False
    return True


@register("no_gap", PredicateContract(required=("track",)))
def no_gap(state: EditProject, params: dict, tolerance: dict) -> bool:
    track_ref = params["track"]
    _require_track(state, track_ref)
    clips = _lane_clips(state, track_ref)
    time_tol = tolerance.get("time", DEFAULT_TOLERANCE["time"])
    for i in range(len(clips) - 1):
        gap = clips[i + 1].start - clips[i].end
        if gap > time_tol:
            return False
    return True


def _link_by_id(state: EditProject, link_id: str) -> Optional[dict]:
    for link in state.links:
        if link.get("id") == link_id:
            return link
    return None


@register("reference_intact", PredicateContract(optional=("link_id",)))
def reference_intact(state: EditProject, params: dict, tolerance: dict) -> bool:
    # `link_id` scopes the check to one link group: "whatever else you change,
    # this video/audio pair must still be linked to each other". Unread, the
    # six v2 constraints that use it ran the project-wide integrity sweep
    # instead, which the untouched fixture passes — so a run that broke exactly
    # the link the task told it to preserve still scored as intact.
    link_id = params.get("link_id")
    if link_id is not None:
        link = _link_by_id(state, link_id)
        if link is None:
            return False
        clip_ids = link.get("clip_ids") or []
        clips = [state.get_clip_by_id(cid) for cid in clip_ids]
        if len(clips) < 2 or any(c is None for c in clips):
            return False
        groups = {c.link_group for c in clips}
        return len(groups) == 1 and None not in groups

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

@register("has_effect", PredicateContract(
    required=("entity",), optional=("effect_type",)))
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


@register("has_transition", PredicateContract(
    one_of=(("between", "track", "type"),)))
def has_transition(state: EditProject, params: dict, tolerance: dict) -> bool:
    """A transition exists, narrowed by any of `between` / `track` / `type`.

    `between` was indexed unguarded, so the 10 v2 constraints that scope by
    track or type alone ("add a cross dissolve somewhere on V1") raised
    `KeyError('between')` and were scored as failures no run could avoid.
    """
    ids = params.get("between")
    transition_type = params.get("type")
    track_ref = params.get("track")
    candidates = list(state.transitions)
    if track_ref is not None:
        track = _get_track_by_id(state, _require_track(state, track_ref))
        candidates = list(track.transitions) if track is not None else []
    for t in candidates:
        if ids is not None and not _transition_joins(t, ids):
            continue
        tr_type = getattr(t, "type", None) or getattr(t, "transition_type", None)
        if transition_type is None or tr_type == transition_type:
            return True
    return False


@register("has_link", PredicateContract(required=("clip_ids",)))
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


@register("not_linked", PredicateContract(required=("entity",)))
def not_linked(state: EditProject, params: dict, tolerance: dict) -> bool:
    """Check that a clip has no link_group (is unlinked)."""
    entity_id = params["entity"]
    clip = state.get_clip_by_id(entity_id)
    if clip is None:
        return False
    return clip.link_group is None


# --- 2.6 Global (1) ---

def _require_baseline(state: EditProject, name: str) -> EditProject:
    """The `_initial` snapshot a change-predicate needs, or a loud error.

    ⚠ Every one of these predicates used to answer **True** when the snapshot
    was missing — "did this change?" answered "yes" for a project nobody had
    compared against anything. That is a free pass, and it is exactly the shape
    of defect this audit exists to find, so it does not get to survive as a
    default just because the harness happens not to reach it.

    It genuinely is unreachable from scoring: `validate_named_constraints` —
    the sole production entry point, via `runner/validator.py` — assigns
    `final_state._initial` before evaluating, so the branch can only be reached
    by calling a predicate directly. That is why raising here changes no
    dataset verdict (pinned by `test_constraint_predicate_audit.py`), and why
    it must still raise: a future direct caller would otherwise inherit the
    free pass silently. Raising rather than returning False keeps the two
    failure modes apart — `evaluate_constraints` records it as an error with a
    message, not as an ordinary unmet constraint.
    """
    initial = state._initial
    if initial is None:
        raise ValueError(
            f"{name}: no initial-state snapshot to compare against "
            f"(set `state._initial`, or evaluate via validate_named_constraints)")
    return initial


@register("unchanged_except", PredicateContract(required=("changed",)))
def unchanged_except(state: EditProject, params: dict, tolerance: dict) -> bool:
    initial = _require_baseline(state, "unchanged_except")

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


@register("state_changed", PredicateContract(documentation_only=("description",)))
def state_changed(state: EditProject, params: dict, tolerance: dict) -> bool:
    """Check that the final state differs from initial state in any way.

    `description` is prose for the scenario author and is deliberately not read;
    it is declared so that "ignored on purpose" stays distinguishable from a
    parameter the code forgot.
    """
    initial = _require_baseline(state, "state_changed")
    return state.to_dict() != initial.to_dict()


@register("entity_count_changed", PredicateContract(
    optional=("type", "direction")))
def entity_count_changed_fn(state: EditProject, params: dict, tolerance: dict) -> bool:
    """Check that entity count changed in the expected direction."""
    initial = _require_baseline(state, "entity_count_changed")
    # A `type` this schema does not model used to count 0 against 0 and simply
    # fail — `{type: marker, direction: increase}` was a constraint no run could
    # satisfy and no output explained.
    entity_type = _require_entity_type(params.get("type", "clip"))
    direction = params.get("direction", "increase")

    current = len(_get_entity_list(state, entity_type))
    original = len(_get_entity_list(initial, entity_type))

    if direction == "increase":
        return current > original
    elif direction == "decrease":
        return current < original
    return current != original


def _position_of(entity: Any) -> Optional[tuple[float, float]]:
    """`(start, end)` of an entity, or None if it has no timeline extent."""
    start, end = _as_number(_resolve_field(entity, "start")), _as_number(
        _resolve_field(entity, "end"))
    if start is None or end is None:
        return None
    return start, end


@register("position_changed", PredicateContract(
    required=("entity",), optional=("max_duration",)))
def position_changed_fn(state: EditProject, params: dict, tolerance: dict) -> bool:
    """The named entity sits somewhere else than it did, and still exists.

    ⚠ This used to ignore `entity` entirely and return "did anything at all
    change in the project" — so any unrelated edit satisfied "move *this* clip",
    and deleting the clip satisfied it too. 16 v2 constraints named an entity
    here; none of them were checked. `max_duration`, where given, additionally
    caps the entity's length (the two appear together in a trim task).
    """
    initial = _require_baseline(state, "position_changed")
    entity_id = params["entity"]
    entity = _get_entity(state, entity_id)
    initial_entity = _get_entity(initial, entity_id)
    if entity is None:
        return False
    time_tol = tolerance.get("time", DEFAULT_TOLERANCE["time"])
    max_duration = params.get("max_duration")
    if max_duration is not None:
        span = _position_of(entity)
        if span is None or (span[1] - span[0]) > max_duration + time_tol:
            return False
    if initial_entity is None:
        return True  # newly created: it is not where it used to be
    now, before = _position_of(entity), _position_of(initial_entity)
    if now is None or before is None:
        return False
    return (abs(now[0] - before[0]) > time_tol
            or abs(now[1] - before[1]) > time_tol)


@register("effect_param_changed", PredicateContract(
    required=("entity",), optional=("effect_type", "param")))
def effect_param_changed_fn(state: EditProject, params: dict, tolerance: dict) -> bool:
    """A parameter of an effect on the named clip differs from the baseline.

    ⚠ **No scenario in any bundled dataset calls this** (v1/v2/v3/v3.1 alike) —
    it is reachable only from the test suite. It is kept registered because the
    name is part of the published registry surface, but its body no longer
    lies: it used to return "did anything at all change in the project", which
    is what `state_changed` means, under a name that promises an effect
    parameter moved.
    """
    initial = _require_baseline(state, "effect_param_changed")
    entity_id = params["entity"]
    effect_type = params.get("effect_type")
    param = params.get("param")

    def _effect_params(project: EditProject) -> dict:
        clip = project.get_clip_by_id(entity_id)
        if clip is None:
            return {}
        out: dict = {}
        for nb in clip.native:
            if nb.type in ("source", "metadata"):
                continue
            eff_type = getattr(nb, "effect_type", None) or nb.type
            if effect_type is not None and eff_type != effect_type:
                continue
            values = (nb.data or {}).get("params", {})
            if param is not None:
                if param not in values:
                    continue
                values = {param: values[param]}
            out[f"{eff_type}:{nb.data.get('id', id(nb))}"] = json.dumps(
                values, sort_keys=True, default=str)
        return out

    return _effect_params(state) != _effect_params(initial)


@register("effect_removed", PredicateContract(
    required=("effect_type",), optional=("entity",)))
def effect_removed(state: EditProject, params: dict, tolerance: dict) -> bool:
    """Effects of `effect_type` existed at the start and none remain.

    ⚠ This name was **called by 3 v2 scenarios but never registered**, so every
    one of them scored `Unknown constraint function: effect_removed` — a silent,
    permanent failure that looked like a model error.

    The contract is recovered from those call sites, not invented. Two of them
    say "remove all blur effects from all clips" / "…from the project", which
    fixes the scope as project-wide unless an `entity` narrows it. The removal
    reading — *existed, then gone* — rather than mere absence is forced by the
    fixture: `complex_sequence`, which all three use, ships **zero** effects, so
    a pure-absence predicate (what `entity_not_exists {type: effect,
    effect_type: …}` already offers) would be satisfied by an agent that does
    nothing at all. Migrating the three scenarios onto that predicate would have
    converted three permanent failures into three free passes.

    ⛔ **Registering this does not make those three scenarios solvable, and the
    audit does not count it as though it did.** Their fixture holds no effect of
    the named type and none of them patches one in, so the task "remove all blur
    effects" names nothing that exists: absence passes on zero work, removal
    fails on any work. No contract for this predicate can repair that — the
    contradiction is between the task text and the fixture, and it is recorded
    as `BLOCKED_NEEDS_OWNER` against the three scenarios, with a
    machine-verified fixture patch offered as the candidate repair. What this
    registration does fix is the predicate-level defect: three constraints that
    scored `Unknown constraint function`, a failure indistinguishable in the
    logs from a model that got the edit wrong.
    """
    initial = _require_baseline(state, "effect_removed")
    effect_type = params["effect_type"]
    entity_id = params.get("entity")

    def _matching(project: Optional[EditProject]) -> int:
        if project is None:
            return 0
        if entity_id is not None:
            clip = project.get_clip_by_id(entity_id)
            blocks = list(clip.native) if clip is not None else []
        else:
            blocks = list(project.effects)
        return sum(1 for nb in blocks
                   if nb.type not in ("source", "metadata")
                   and (getattr(nb, "effect_type", None) or nb.type) == effect_type)

    if _matching(state) > 0:
        return False
    return _matching(initial) > 0


@register("batch_operation", PredicateContract(documentation_only=("scope",)))
def batch_operation(state: EditProject, params: dict, tolerance: dict) -> bool:
    """Meta-constraint: batch operation was applied. Always passes if state changed.

    ⛔ This is `state_changed` under another name, and the audit records it as
    `BLOCKED_NEEDS_OWNER` — not as a checked predicate — for two reasons that
    are both about what it *fails to verify*:

    1. `scope: all_matching` — its only observed value, in all 73 v2 call sites
       — has no machine meaning here. Nothing tells the checker what "matching"
       selects, so nothing verifies that the operation reached every member of
       the set. The word "batch" is doing work no code does: an agent that
       edits **one** of the matching entities satisfies this exactly as well as
       one that edits all of them. Closing that needs a scope language
       (a selector the checker can evaluate), which is a dataset-design
       decision, not a code fix.
    2. Its missing-baseline branch used to return True, so it also passed when
       there was nothing to compare against. That half is fixed here; the
       unverified `scope` is not, and pretending otherwise would report a
       repair that did not happen.
    """
    initial = _require_baseline(state, "batch_operation")
    return state.to_dict() != initial.to_dict()


def _entities_equal(a: Any, b: Any, tolerance: dict) -> bool:
    """Compare two entities with field-specific tolerance.

    Skips nested list fields (clips, tracks, transitions, bins, …) because those
    are walked separately at their own entity level.

    ★ `native` is NOT one of them. Effects live there and are not walked
    anywhere else, so skipping it meant `unchanged_except` never saw an effect
    added to, changed on, or removed from a clip outside the allowed list — a
    silent channel for collateral damage.
    """
    if type(a) is not type(b):
        return False

    # Fields that contain child entities walked elsewhere - skip these
    nested_fields = {"clips", "tracks", "transitions", "bins", "media", "timelines"}

    # Effects are only reachable here; compare them by value.
    na, nb = getattr(a, "native", None), getattr(b, "native", None)
    if na is not None or nb is not None:
        def _blocks(blocks):
            return sorted(
                (nb_.type, json.dumps(nb_.data, sort_keys=True, default=str))
                for nb_ in (blocks or [])
                if nb_.type not in ("source", "metadata"))
        if _blocks(na) != _blocks(nb):
            return False

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
# Canonical Predicate Registry — the single dispatch source
# ============================================================

# Parameter value types, shared vocabulary for the registry rows below.
# `bool` is rejected wherever it is not listed even though Python counts it as
# an `int` — `min: true` is an authoring typo, not a count.
_NUM = (int, float)
_SEQ = (list, tuple)


@dataclass(frozen=True)
class PredicateSpec:
    """One row of the canonical predicate registry.

    The registry (`PREDICATE_REGISTRY`) is the single source the scorer
    dispatches through: `evaluate_constraints` — and therefore
    `validate_named_constraints`, the sole production entry point — resolves a
    constraint's predicate name here and nowhere else. A name this registry
    does not carry is a FAILED constraint, never a silent skip; parameters the
    row's contract or types do not accept fail the same way.

    Each row carries, for one predicate:
      - `contract`     — the parameter-key schema (`required` / `one_of` /
                         `optional` / `documentation_only`), declared beside
                         the implementation and enforced by `register`.
      - `param_types`  — allowed value types per key, enforced at dispatch.
                         `None` is always accepted where the key is accepted:
                         predicates handle it explicitly (`track: null` means
                         unscoped, `value: null` asserts emptiness) and the
                         corpus writes it.
      - `semantics`    — one line: what a True verdict means.
      - `tolerance`    — one line: how the global tolerance table applies.

    ⚠ Registered-faithfully discrepancies (documented, deliberately NOT fixed
    here — changing them is a dataset/owner decision, per the P1 audit):

      * `batch_operation` — the name promises "the operation reached every
        matching entity"; the body is `state_changed`. `scope: all_matching`
        (its only observed value) has no machine meaning — an agent that edits
        one matching entity scores the same as one that edits all. Recorded as
        BLOCKED_NEEDS_OWNER in the P1 audit; a scope/selector language is a
        dataset-design decision.
      * `effect_param_changed` — dead name: no scenario in any bundled dataset
        (v1/v2/v3/v3.1) calls it. Kept registered because the name is part of
        the published registry surface.
      * `effect_removed` — contract recovered from its three v2 call sites,
        which remain unmeasurable regardless (their fixture ships no effect of
        the named type): a task/fixture contradiction only the dataset owner
        can resolve.
      * `unchanged_except` — `Rational`-valued fields (`timeline_start`,
        `duration`, …) are compared exactly, not under the `time` tolerance:
        `_entities_equal` applies tolerance only to `float` instances. A
        sub-tolerance nudge on a non-allowlisted clip therefore still counts
        as a change. Current behavior, registered as-is.
      * `not_linked` / `entity_not_exists` — absence predicates: they pass on
        a state where the asked-for absence already held initially (no-op
        passes are a scenario-design concern, pinned dataset-side by
        `test_no_op_does_not_pass.py`).
    """
    name: str
    contract: PredicateContract
    func: Any  # the contract-checked callable registered in CONSTRAINT_FUNCTIONS
    param_types: tuple[tuple[str, tuple[type, ...]], ...]
    semantics: str
    tolerance: str

    def types_for(self, key: str) -> Optional[tuple[type, ...]]:
        for k, allowed in self.param_types:
            if k == key:
                return allowed
        return None

    def resolve(self) -> Any:
        """The callable to dispatch, resolved at call time.

        The registry decides *admission* (which names exist, with what
        schema); the body is read from `CONSTRAINT_FUNCTIONS` on every call
        because the published rescore harness
        (`rescore_attribute_changed_direction.py`, frozen in the release
        artifact) computes its counterfactual lenses by monkeypatching that
        mapping. Capturing the callable at import time silently disabled the
        patch and collapsed the "direction unenforced" lens onto the enforced
        one — resolving late keeps that seam working.
        """
        return CONSTRAINT_FUNCTIONS.get(self.name, self.func)


# Row data for every registered predicate. `_build_predicate_registry` refuses
# to import when this table and `CONSTRAINT_FUNCTIONS` disagree in either
# direction, so a predicate cannot be added without describing it here (and a
# meta-test in tests/test_predicate_fixtures.py additionally requires the
# three fixture kinds for every row).
_REGISTRY_ROWS: dict[str, dict[str, Any]] = {
    "entity_exists": {
        "types": {"entity": (str,), "type": (str,), "track": (str,),
                  "index": (int,), "parent": (str,), "effect_type": (str,),
                  "where": (dict,), "name": (str,), "kind": (str,),
                  "between": _SEQ},
        "semantics": ("An entity with the given id exists, or ≥ index+1 "
                      "entities survive the type/track/parent/effect_type/"
                      "name/kind/between/where filters."),
        "tolerance": ("Numeric `where` comparisons use the field's tolerance "
                      "category (time ±0.05s, volume ±0.5dB, speed ±0.01, "
                      "default ±0.01); everything else is exact."),
    },
    "entity_not_exists": {
        "types": {"entity": (str,), "type": (str,), "track": (str,),
                  "index": (int,), "parent": (str,), "effect_type": (str,),
                  "where": (dict,), "name": (str,), "kind": (str,),
                  "between": _SEQ},
        "semantics": ("Negation of entity_exists over the same filters; an "
                      "unbindable `entity` ref is an error, not a pass."),
        "tolerance": "Same as entity_exists (via delegation).",
    },
    "entity_count": {
        "types": {"type": (str,), "expected": (int,), "min": (int,),
                  "max": (int,), "track": (str,)},
        "semantics": ("The count of `type` entities (optionally within "
                      "`track`) equals `expected`, or satisfies `min`/`max` "
                      "bounds."),
        "tolerance": "None — counts are exact integers.",
    },
    "attribute_equals": {
        "types": {"entity": (str,), "field": (str,)},  # `value` is any type
        "semantics": ("The entity's (dotted) field equals `value`; "
                      "`value: null` asserts the field is present and empty."),
        "tolerance": ("Numeric comparison under the field's tolerance "
                      "category; dicts/strings exact."),
    },
    "attribute_in_range": {
        "types": {"entity": (str,), "field": (str,), "min": _NUM,
                  "max": _NUM},
        "semantics": "The entity's numeric field lies within [min, max].",
        "tolerance": ("Bounds widened by the field's tolerance category on "
                      "both ends."),
    },
    "attribute_changed": {
        "types": {"entity": (str,), "field": (str,), "direction": (str,)},
        "semantics": ("The entity's field differs from the initial snapshot, "
                      "optionally in the given direction; a deleted entity "
                      "does not count as changed."),
        "tolerance": ("Numeric change must exceed the field's tolerance "
                      "category to count."),
    },
    "position_equals": {
        "types": {"entity": (str,), "start": _NUM, "end": _NUM},
        "semantics": ("The entity's start and/or end sit at the given "
                      "timeline positions (at least one edge must be "
                      "pinned)."),
        "tolerance": "Each pinned edge compared within time ±0.05s.",
    },
    "duration_equals": {
        "types": {"entity": (str,), "value": _NUM},
        "semantics": "The entity's extent (end − start) equals `value`.",
        "tolerance": "Compared within time ±0.05s.",
    },
    "order": {
        "types": {"entities": _SEQ, "expected": _SEQ, "track": (str,)},
        "semantics": ("The listed entities all exist (and sit on `track`, if "
                      "given) with strictly increasing start times, in the "
                      "listed order."),
        "tolerance": "None — ordering is strict (ties fail).",
    },
    "no_overlap": {
        "types": {"track": (str,)},
        "semantics": ("No two lane clips on the track overlap; caption/title "
                      "overlays are not lane neighbours. An unresolvable "
                      "track is an error, not a clean report."),
        "tolerance": "Overlaps up to time ±0.05s are ignored.",
    },
    "no_gap": {
        "types": {"track": (str,)},
        "semantics": ("No gap between consecutive lane clips on the track; "
                      "overlays excluded; unresolvable track errors."),
        "tolerance": "Gaps up to time ±0.05s are ignored.",
    },
    "reference_intact": {
        "types": {"link_id": (str,)},
        "semantics": ("With `link_id`: that link group still joins ≥ 2 "
                      "existing clips. Without: every clip/transition media "
                      "reference in the project resolves."),
        "tolerance": "None — referential integrity is exact.",
    },
    "has_effect": {
        "types": {"entity": (str,), "effect_type": (str,)},
        "semantics": ("The clip carries at least one effect block, of "
                      "`effect_type` when given."),
        "tolerance": "None.",
    },
    "has_transition": {
        "types": {"between": _SEQ, "track": (str,), "type": (str,)},
        "semantics": ("A transition exists, narrowed by any of: the exact "
                      "clip pair (`between`, order-insensitive), the track, "
                      "the transition type."),
        "tolerance": "None.",
    },
    "has_link": {
        "types": {"clip_ids": _SEQ},
        "semantics": ("Exactly two clips are named and share the same "
                      "non-empty link_group."),
        "tolerance": "None.",
    },
    "not_linked": {
        "types": {"entity": (str,)},
        "semantics": ("The clip exists and has no link_group; a deleted clip "
                      "fails."),
        "tolerance": "None.",
    },
    "unchanged_except": {
        "types": {"changed": _SEQ},
        "semantics": ("No entity outside the `changed` allowlist was added, "
                      "removed, or modified (effect blocks included) relative "
                      "to the initial snapshot."),
        "tolerance": ("Float fields compared under their tolerance category; "
                      "⚠ Rational time fields compare exactly (see class "
                      "docstring)."),
    },
    "state_changed": {
        "types": {"description": (str,)},
        "semantics": ("The final project differs from the initial snapshot "
                      "in any way (`description` is authoring prose, "
                      "deliberately unread)."),
        "tolerance": "None — dict-level equality, reverted edits cancel out.",
    },
    "entity_count_changed": {
        "types": {"type": (str,), "direction": (str,)},
        "semantics": ("The count of `type` entities moved in `direction` "
                      "(default: increase) relative to the initial snapshot."),
        "tolerance": "None — counts are exact.",
    },
    "position_changed": {
        "types": {"entity": (str,), "max_duration": _NUM},
        "semantics": ("The named entity still exists and sits elsewhere than "
                      "in the initial snapshot; `max_duration` additionally "
                      "caps its length."),
        "tolerance": ("Displacement must exceed time ±0.05s; the "
                      "max_duration cap is widened by the same."),
    },
    "effect_param_changed": {
        "types": {"entity": (str,), "effect_type": (str,), "param": (str,)},
        "semantics": ("A parameter of an effect on the named clip differs "
                      "from the initial snapshot (⚠ dead name — no bundled "
                      "scenario calls it; see class docstring)."),
        "tolerance": "None — params serialized and compared exactly.",
    },
    "effect_removed": {
        "types": {"effect_type": (str,), "entity": (str,)},
        "semantics": ("Effects of `effect_type` (project-wide, or on "
                      "`entity`) existed initially and none remain — absence "
                      "alone is not removal."),
        "tolerance": "None.",
    },
    "batch_operation": {
        "types": {"scope": (str,)},
        "semantics": ("⚠ Verifies only that the state changed; `scope` has "
                      "no machine meaning (see class docstring — "
                      "BLOCKED_NEEDS_OWNER)."),
        "tolerance": "None — dict-level equality.",
    },
}


def _build_predicate_registry() -> dict[str, PredicateSpec]:
    """Assemble the canonical registry; refuse to import if it is not total.

    Failing at import rather than at lookup keeps the fail-closed property
    honest: there is no window in which a registered-but-undescribed predicate
    could be dispatched without its row.
    """
    registered = set(CONSTRAINT_FUNCTIONS)
    described = set(_REGISTRY_ROWS)
    if registered != described:
        raise RuntimeError(
            "predicate registry is not canonical — "
            f"registered but undescribed: {sorted(registered - described)}; "
            f"described but unregistered: {sorted(described - registered)}")
    registry: dict[str, PredicateSpec] = {}
    for name in sorted(registered):
        row = _REGISTRY_ROWS[name]
        contract = CONSTRAINT_CONTRACTS[name]
        stray = sorted(set(row["types"]) - set(contract.accepted))
        if stray:
            raise RuntimeError(
                f"{name}: registry declares types for parameter keys the "
                f"contract does not accept: {stray}")
        registry[name] = PredicateSpec(
            name=name,
            contract=contract,
            func=CONSTRAINT_FUNCTIONS[name],
            param_types=tuple(sorted(
                (key, tuple(allowed)) for key, allowed in row["types"].items())),
            semantics=row["semantics"],
            tolerance=row["tolerance"],
        )
    return registry


PREDICATE_REGISTRY: dict[str, PredicateSpec] = _build_predicate_registry()


# ============================================================
# Scorer version
# ============================================================

# Bump on any deliberate change to scoring semantics. The registry digest
# appended below changes by itself whenever the predicate surface (names,
# parameter schemas, types, documented semantics/tolerance) changes, so two
# results scored by different scorer contracts can never carry the same
# version string by accident.
SCORER_SEMVER = "1.0.0"


def _registry_digest(registry: dict[str, PredicateSpec]) -> str:
    """Content hash of the canonical registry (names, schemas, types, docs)."""
    # Local import: the module-level import block must not grow — tests pin
    # source line anchors in this file (see test_manuscript_claim_audit.py).
    import hashlib
    payload = json.dumps(
        [
            {
                "name": spec.name,
                "required": list(spec.contract.required),
                "one_of": [list(group) for group in spec.contract.one_of],
                "optional": list(spec.contract.optional),
                "documentation_only": list(spec.contract.documentation_only),
                "types": [[key, [t.__name__ for t in allowed]]
                          for key, allowed in spec.param_types],
                "semantics": spec.semantics,
                "tolerance": spec.tolerance,
            }
            for _, spec in sorted(registry.items())
        ],
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


SCORER_VERSION = f"{SCORER_SEMVER}+registry.{_registry_digest(PREDICATE_REGISTRY)[:12]}"


def _validate_param_types(spec: PredicateSpec, params: dict) -> None:
    """Reject a parameter value the predicate cannot read, loudly.

    Complements the key-schema check in `_validate_params` (which the checked
    callable already runs): keys may be right and values still unusable —
    `where: "duration > 5"` or `min: "four"` would otherwise degrade into a
    constraint that can never pass, indistinguishable from a model error.
    `None` is always allowed where the key is allowed; predicates handle it
    explicitly and the corpus writes it (`track: null`, `value: null`).
    """
    for key, allowed in spec.param_types:
        if key not in params:
            continue
        value = params[key]
        if value is None:
            continue
        if isinstance(value, bool) and bool not in allowed:
            raise ValueError(
                f"{spec.name}: parameter {key!r} expects "
                f"{'/'.join(t.__name__ for t in allowed)}, got bool")
        if not isinstance(value, allowed):
            raise ValueError(
                f"{spec.name}: parameter {key!r} expects "
                f"{'/'.join(t.__name__ for t in allowed)}, "
                f"got {type(value).__name__}")


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
    # Which scorer produced this verdict. Additive field (defaulted so every
    # existing construction site keeps working); see SCORER_VERSION.
    scorer_version: str = SCORER_VERSION

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
        # Each constraint is a dict like {"entity_exists": {"type": "clip", ...}}.
        # Any other shape fails closed: a two-key mapping would silently drop
        # its second predicate under the old `next(iter(...))` reading, and a
        # non-mapping asserts nothing. Neither may score as satisfied or be
        # skipped. (The corpus has zero such constraints — the loader rejects
        # them too; this guards direct callers and future formats.)
        if not isinstance(constraint, dict) or len(constraint) != 1:
            shape = (sorted(constraint) if isinstance(constraint, dict)
                     else type(constraint).__name__)
            results.append(ConstraintResult(
                constraint=(constraint if isinstance(constraint, dict)
                            else {"<malformed>": constraint}),
                passed=False,
                func_name="<malformed>",
                error=("Malformed constraint: expected a single-key mapping "
                       f"{{predicate: params}}, got {shape}"),
            ))
            continue

        func_name = next(iter(constraint))
        raw_params = constraint[func_name]

        # Resolve $ref → actual IDs
        params = _resolve_refs(raw_params or {}, bindings)

        # Dispatch through the canonical registry — the single source of the
        # predicate surface. An unregistered name is a failed constraint,
        # never a silent skip or pass.
        spec = PREDICATE_REGISTRY.get(func_name)
        if spec is None:
            results.append(ConstraintResult(
                constraint=constraint,
                passed=False,
                func_name=func_name,
                error=f"Unknown constraint function: {func_name}",
            ))
            continue

        try:
            _validate_param_types(spec, params)
            passed = spec.resolve()(state, params, tolerance)
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
        scorer_version=SCORER_VERSION,
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
