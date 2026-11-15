"""A single-commit candidate: build it from one commit's files, and check that it holds nothing else.

A candidate is a separate directory with a history of exactly one commit, made from the files of one
reviewed commit and from nothing else: no copied `.git`, no parent commit, no other branch, tag,
remote, stash or stray object.

`check` looks at every object in the candidate's object database, not only at the files of HEAD.
A file may give one declared public contact address (`contact`); no other address may appear.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import integrity

FILE_MODES = {"100644", "100755"}


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, input=data).stdout


def build(source: Path, ref: str, out: Path, name: str, email: str, message: str) -> str:
    """Write the files of `ref` into the new directory `out` and commit them once. Returns the commit."""
    out = Path(out)
    if out.exists():
        raise SystemExit(f"{out} exists; choose a new directory (nothing is overwritten)")
    archive = git(source, "archive", "--format=tar", ref)
    out.mkdir(parents=True)
    subprocess.run(["tar", "-x", "-C", str(out)], input=archive, check=True)
    git(out, "init", "--quiet", "--initial-branch=main")
    git(out, "config", "user.name", name)
    git(out, "config", "user.email", email)
    git(out, "add", "--all")
    git(out, "commit", "--quiet", "--message", message)
    return git(out, "rev-parse", "HEAD").decode().strip()


def check(candidate: Path, name: str, email: str, private_tokens: list[str], expected_tree: str | None = None,
          private_literals: list[str] = (), contact: str | None = None) -> list[str]:
    """Problems of a candidate directory. Empty means it is one clean commit of the listed files."""
    candidate = Path(candidate)
    problems: list[str] = []
    dot_git = candidate / ".git"
    if not dot_git.is_dir():
        return ["the candidate has no .git directory of its own"]
    for inside in ("objects/info/alternates", "worktrees", "modules", "shallow"):
        if (dot_git / inside).exists():
            problems.append(f".git/{inside} exists: objects or checkouts outside this history")

    refs = git(candidate, "for-each-ref", "--format=%(refname)").decode().split()
    if refs != ["refs/heads/main"]:
        problems.append(f"references other than the one branch: {refs}")
    if git(candidate, "remote").decode().split():
        problems.append("a remote is configured")
    commits = git(candidate, "rev-list", "--all").decode().split()
    if len(commits) != 1:
        problems.append(f"{len(commits)} commits; a candidate has exactly one")
    head = git(candidate, "rev-list", "--parents", "-n", "1", "HEAD").decode().split()
    if len(head) != 1:
        problems.append("the commit has a parent")

    every = {line.split()[0] for line in
             git(candidate, "cat-file", "--batch-all-objects", "--batch-check").decode().splitlines()}
    reachable = {line.split()[0] for line in git(candidate, "rev-list", "--objects", "HEAD").decode().splitlines()}
    stray = sorted(every - reachable)
    if stray:
        problems.append(f"{len(stray)} objects in the database are not part of the commit")

    for line in git(candidate, "ls-tree", "-r", "HEAD").decode().splitlines():
        mode, kind = line.split()[:2]
        if kind != "blob" or mode not in FILE_MODES:
            problems.append(f"not a regular file: {line.split(chr(9))[-1]}")
    if git(candidate, "status", "--porcelain", "--ignored").strip():
        problems.append("the directory holds files that are not in the commit, or changed files")
    tracked = set(git(candidate, "ls-files", "-z").decode().split("\0")) - {""}
    on_disk = {path.relative_to(candidate).as_posix() for path in candidate.rglob("*")
               if (path.is_file() or path.is_symlink()) and ".git" not in path.relative_to(candidate).parts}
    if tracked != on_disk:
        problems.append("files on disk differ from the files of the commit")
    problems += [f"manifest: {problem}" for problem in integrity.compare_manifest(candidate)]

    author = git(candidate, "log", "-1", "--format=%an|%ae|%cn|%ce").decode().strip()
    if author != f"{name}|{email}|{name}|{email}":
        problems.append("author or committer is not the expected name and address")
    if expected_tree and git(candidate, "rev-parse", "HEAD^{tree}").decode().strip() != expected_tree:
        problems.append("the files are not exactly the files of the reviewed commit (tree differs)")

    # every object, including any that is not reachable
    address = re.compile(integrity.SCAN_PATTERNS["email_address"].pattern.encode())
    for object_id in sorted(every):
        kind = git(candidate, "cat-file", "-t", object_id).decode().strip()
        data = git(candidate, "cat-file", kind, object_id)
        text = data.decode("utf-8", "replace")
        if kind != "tree":
            if integrity.SCAN_PATTERNS["home_path"].search(text) or integrity.SCAN_PATTERNS["tmp_path"].search(text):
                problems.append(f"a local path in {kind} {object_id[:12]}")
            if any(token in text for token in private_tokens):
                problems.append(f"a local account name in {kind} {object_id[:12]}")
            if any(literal in text for literal in private_literals):
                problems.append(f"a literal of the private map in {kind} {object_id[:12]}")
        found = set(address.findall(data)) if kind != "tree" else set()
        if kind == "commit":
            found -= {email.encode()}
        elif kind == "blob" and contact:
            found -= {contact.encode()}
        if found:
            problems.append(f"an e-mail address in {kind} {object_id[:12]}")
    return problems
