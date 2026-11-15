"""Maintainer tool: make and check a single-commit copy of this repository for publication.

    python tools/candidate.py build --out NEW_DIRECTORY --name NAME --email ADDRESS [--ref HEAD]
    python tools/candidate.py check --dir DIRECTORY --name NAME --email ADDRESS --private-map FILE [--ref HEAD]

A published version is a candidate: a separate directory whose history is one commit made from the
files of one reviewed commit and from nothing else.

build   writes the files of the commit into NEW_DIRECTORY, which must not exist, and commits them
        once on a branch `main`, with the given name and address. It adds no remote and pushes
        nothing.
check   fails unless the directory is exactly that: one commit without a parent, one branch, no
        tag, remote or stash, no object in its database outside the commit, regular files only,
        nothing on disk that is not in the commit, files equal to MANIFEST.json and to the files
        of the reviewed commit, the expected author, and no local path, local account name, other
        literal of the private map or other e-mail address in any object.

Neither command writes to a remote. Publishing is a separate act.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nlebench_audit import candidate, sanitize  # noqa: E402

MESSAGE = ("Audit material for \"Red-Teaming the Grader\"\n\n"
           "This commit identifies the packaging. The results stay pinned to the\n"
           "benchmark commits named in runtime/PINS.json and scorers/IDENTITY.json,\n"
           "and the provenance of every file is recorded in provenance/. The\n"
           "licence and its terms are stated in LICENSE and LICENSE.md.\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("build")
    one.add_argument("--out", required=True)
    two = sub.add_parser("check")
    two.add_argument("--dir", required=True)
    two.add_argument("--private-map", required=True)
    two.add_argument("--contact", help="the one public contact address that LICENSE.md and README.md give")
    for item in (one, two):
        item.add_argument("--name", required=True)
        item.add_argument("--email", required=True)
        item.add_argument("--ref", default="HEAD")
    args = parser.parse_args()
    if args.command == "build":
        commit = candidate.build(ROOT, args.ref, Path(args.out), args.name, args.email, MESSAGE)
        print(f"candidate: {args.out} commit {commit}")
        return 0
    private = sanitize.PrivateMap.load(args.private_map)
    tree = candidate.git(ROOT, "rev-parse", f"{args.ref}^{{tree}}").decode().strip()
    problems = candidate.check(Path(args.dir), args.name, args.email, private.private_tokens, expected_tree=tree,
                               private_literals=private.literals(), contact=args.contact)
    for problem in problems:
        print("  FAIL", problem)
    objects = len(candidate.git(Path(args.dir), "cat-file", "--batch-all-objects", "--batch-check").splitlines())
    print(f"candidate check: {objects} objects examined, tree {tree}; {'PASS' if not problems else 'FAIL'}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
