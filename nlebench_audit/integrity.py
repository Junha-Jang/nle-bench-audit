"""Typed JSON comparison, file manifests and the scan of the checkout.

Comparison rules (used wherever two JSON documents are compared):

  - a boolean equals only the same boolean: `True` is not `1`;
  - `null` equals only `null`;
  - a missing key or list element is different from a present `null`;
  - numbers compare by value (`0 == 0.0`); strings by exact characters;
  - lists compare element by element, objects key by key.

A position is the complete path of a leaf from the root of its document. A
pattern for positions is written with `/` between segments, `*` for exactly one
object key and `#` for exactly one list index. Patterns are matched against the
whole position, never against its end.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


class _Missing:
    def __repr__(self) -> str:
        return "<missing>"


MISSING = _Missing()

IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "build", "dist"}
IGNORED_SUFFIXES = (".pyc", ".pyo", ".egg-info")
MANIFEST_NAME = "MANIFEST.json"

SCAN_PATTERNS = {
    "home_path": re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+"),
    "tmp_path": re.compile(r"/tmp/[A-Za-z0-9_./-]+"),
    "email_address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "secret_like": re.compile(r"(?i)(api[_-]?key|secret[_-]?key|password|bearer\s+[a-z0-9]|sk-[A-Za-z0-9]{16,})"),
    "hangul_text": re.compile(r"[가-힣]"),
    "dataset_canary": re.compile(r"NLE-BENCH-V3-[A-Z]+-[0-9a-f]{6,}"),
    "scenario_id": re.compile(r"NLB-v3-[A-Z]{2}-[A-Z]{2}-\d{3}"),
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


# ---------------------------------------------------------------------------
# typed JSON comparison
# ---------------------------------------------------------------------------

def _kind(value) -> str:
    if value is MISSING:
        return "missing"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    raise TypeError(f"not a JSON value: {type(value).__name__}")


def same_json(a, b) -> bool:
    ka, kb = _kind(a), _kind(b)
    if ka != kb:
        return False
    if ka == "list":
        return len(a) == len(b) and all(same_json(x, y) for x, y in zip(a, b))
    if ka == "object":
        return a.keys() == b.keys() and all(same_json(a[key], b[key]) for key in a)
    if ka == "missing":
        return True
    return a == b


def differing_leaves(a, b, position: tuple = ()) -> dict[tuple, tuple]:
    """Every position where the two documents differ, with both sides.

    A side that has nothing at the position is reported as MISSING.
    """
    if _kind(a) == "object" and _kind(b) == "object":
        out = {}
        for key in sorted(set(a) | set(b)):
            out.update(differing_leaves(a.get(key, MISSING), b.get(key, MISSING), position + (key,)))
        return out
    if _kind(a) == "list" and _kind(b) == "list":
        out = {}
        for index in range(max(len(a), len(b))):
            out.update(differing_leaves(a[index] if index < len(a) else MISSING,
                                        b[index] if index < len(b) else MISSING, position + (index,)))
        return out
    return {} if same_json(a, b) else {position: (a, b)}


def string_leaves(value, position: tuple = ()):
    if isinstance(value, dict):
        for key in value:
            yield from string_leaves(value[key], position + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from string_leaves(item, position + (index,))
    elif isinstance(value, str):
        yield position, value


def position_text(position: tuple) -> str:
    return "/".join(str(segment) for segment in position)


def matches(position: tuple, pattern: str) -> bool:
    """Whether the complete position matches the pattern, segment by segment."""
    segments = pattern.split("/")
    if len(segments) != len(position):
        return False
    for want, got in zip(segments, position):
        if want == "#":
            if isinstance(got, bool) or not isinstance(got, int):
                return False
        elif want == "*":
            if not isinstance(got, str):
                return False
        elif not isinstance(got, str) or want != got:
            return False
    return True


def get_at(document, position: tuple):
    value = document
    for segment in position:
        if isinstance(value, dict) and isinstance(segment, str) and segment in value:
            value = value[segment]
        elif isinstance(value, list) and isinstance(segment, int) and not isinstance(segment, bool) \
                and 0 <= segment < len(value):
            value = value[segment]
        else:
            return MISSING
    return value


def dump_json(document, indent: int = 1) -> bytes:
    """The serialisation used for JSON files this repository writes: sorted keys, UTF-8, one
    final newline. A transformed copy keeps the indentation of its source (1 or 2)."""
    return (json.dumps(document, ensure_ascii=False, indent=indent, sort_keys=True) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# files of the checkout
# ---------------------------------------------------------------------------

def checkout_files(root: Path) -> list[str]:
    """Every file of the checkout, as sorted POSIX paths, without build and cache litter."""
    root = Path(root)
    found = []
    for path in root.rglob("*"):
        if not path.is_file() and not path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRS or part.endswith(IGNORED_SUFFIXES) for part in relative.parts):
            continue
        found.append(relative.as_posix())
    return sorted(found)


def measure(root: Path) -> dict[str, dict]:
    root = Path(root)
    out = {}
    for name in checkout_files(root):
        if name == MANIFEST_NAME:
            continue
        data = (root / name).read_bytes()
        out[name] = {"bytes": len(data), "sha256": sha256_bytes(data)}
    return out


def compare_manifest(root: Path) -> list[str]:
    """Problems between MANIFEST.json and the files actually present. Empty means equal."""
    root = Path(root)
    path = root / MANIFEST_NAME
    if not path.is_file():
        return [f"{MANIFEST_NAME} is missing"]
    recorded = json.loads(path.read_text(encoding="utf-8")).get("files")
    if not isinstance(recorded, dict) or not recorded:
        return [f"{MANIFEST_NAME} lists no files"]
    actual = measure(root)
    problems = [f"listed but not present: {name}" for name in sorted(set(recorded) - set(actual))]
    problems += [f"present but not listed: {name}" for name in sorted(set(actual) - set(recorded))]
    for name in sorted(set(recorded) & set(actual)):
        for field in ("bytes", "sha256"):
            if not same_json(recorded[name].get(field, MISSING), actual[name][field]):
                problems.append(f"{field} differs: {name}")
    return problems


def scan_file(data: bytes) -> dict[str, int]:
    """Counts of the generic patterns in one file. Binary content is decoded leniently."""
    text = data.decode("utf-8", "replace")
    return {name: len(pattern.findall(text)) for name, pattern in SCAN_PATTERNS.items()
            if pattern.search(text)}
