"""Command line of the audit repository.

    python -m nlebench_audit verify
    python -m nlebench_audit tables
    python -m nlebench_audit fixtures
    python -m nlebench_audit cases
    python -m nlebench_audit stored-cases
    python -m nlebench_audit all
    python -m nlebench_audit fingerprint --corpus DIR --name published|v31|v32

Every command but `fingerprint` reads only this checkout. `fingerprint` needs a
scenario corpus directory at the named pin, which this repository does not hold.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import replay, verify

REPO_ONLY = ("verify", "tables", "fixtures", "cases", "stored-cases")


def find_root(explicit: str | None) -> Path:
    candidates = [explicit, os.environ.get("NLE_BENCH_AUDIT_ROOT"), os.getcwd(),
                  str(Path(__file__).resolve().parent.parent)]
    for candidate in candidates:
        if candidate and (Path(candidate) / "MANIFEST.json").is_file() \
                and (Path(candidate) / "expected.json").is_file():
            return Path(candidate).resolve()
    raise SystemExit("cannot find the audit checkout (MANIFEST.json and expected.json); pass --root DIR")


def run(command: str, root: Path) -> int:
    if command == "verify":
        return verify.verify(root)
    if command == "tables":
        return replay.tables(root)
    if command == "fixtures":
        return replay.fixtures(root)
    if command == "cases":
        return replay.cases(root)
    return replay.stored_cases(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nle-bench-audit", description=__doc__.splitlines()[0])
    parser.add_argument("--root", help="the audit checkout (default: found from the working directory)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in REPO_ONLY + ("all",):
        sub.add_parser(name)
    one = sub.add_parser("fingerprint")
    one.add_argument("--corpus", required=True)
    one.add_argument("--name", required=True, choices=("published", "v31", "v32"))
    args = parser.parse_args(argv)
    root = find_root(args.root)
    if args.command == "fingerprint":
        return replay.fingerprint(root, args.corpus, args.name)
    if args.command != "all":
        return run(args.command, root)
    results = {name: run(name, root) for name in REPO_ONLY}
    for name, code in results.items():
        print(f"{name}: exit {code}")
    return 1 if any(results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
