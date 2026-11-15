"""The clean-history candidate check, on small made-up repositories.

Each control builds a candidate that looks clean at its head and is not: an earlier commit, a
stray object, a remote, a file that is not committed, a link, a different author. The check must
see each of them, because it reads the object database and not only the files of the head.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nlebench_audit import candidate, integrity

# Addresses and paths are assembled from pieces so that this file holds no string the scan of the
# checkout, or the candidate check itself, would count as an e-mail address or a local path.
NAME, EMAIL = "A Person", "1+person" "@" "users.noreply.example.org"
OTHER_EMAIL = "personal" "@" "example.org"
CONTACT = "contact" "@" "example.org"
TOKENS = ["someone"]
LOCAL = "/home" "/someone/project"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", f"user.name={NAME}", "-c", f"user.email={EMAIL}", *args],
                          check=True, capture_output=True, text=True).stdout


def write_manifest(folder: Path) -> None:
    (folder / "MANIFEST.json").write_bytes(integrity.dump_json({"files": integrity.measure(folder)}))


@pytest.fixture()
def source(tmp_path) -> Path:
    """A small working repository whose FIRST commit holds a local trace and whose head does not."""
    repo = tmp_path / "working"
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=main")
    (repo / "data.txt").write_text(f"read from {LOCAL}\n")
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "--message", "first, with a local trace")
    (repo / "data.txt").write_text("read from <CHECKOUT>\n")
    write_manifest(repo)
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "--message", "trace removed")
    return repo


@pytest.fixture()
def clean(source, tmp_path) -> Path:
    out = tmp_path / "candidate"
    candidate.build(source, "HEAD", out, NAME, EMAIL, "candidate")
    return out


def problems(folder: Path, **kwargs) -> list[str]:
    return candidate.check(folder, NAME, EMAIL, TOKENS, **kwargs)


def test_a_candidate_built_from_the_head_is_clean_and_has_the_same_files(source, clean):
    tree = git(source, "rev-parse", "HEAD^{tree}").strip()
    assert problems(clean, expected_tree=tree) == []
    assert git(clean, "rev-list", "--count", "HEAD").strip() == "1"
    assert git(clean, "remote").strip() == ""


def test_build_refuses_an_existing_directory(source, clean):
    with pytest.raises(SystemExit):
        candidate.build(source, "HEAD", clean, NAME, EMAIL, "candidate")


def test_control_the_working_history_itself_is_not_a_candidate(source):
    found = problems(source)
    assert any("2 commits" in problem for problem in found)
    assert any("a local path in blob" in problem for problem in found)
    assert any("a local account name in blob" in problem for problem in found)


def test_control_a_copied_git_directory_leaks_the_old_commit(source, tmp_path):
    """The head is clean, the files are right, and the history came along."""
    import shutil
    copied = tmp_path / "copied"
    shutil.copytree(source, copied)
    found = problems(copied)
    assert any("commits" in problem for problem in found) and any("a local path in blob" in problem for problem in found)


def test_control_a_stray_object_outside_the_commit(clean):
    subprocess.run(["git", "-C", str(clean), "hash-object", "-w", "--stdin"], input=f"{LOCAL}\n".encode(), check=True,
                   capture_output=True)
    found = problems(clean)
    assert any("not part of the commit" in problem for problem in found)
    assert any("a local path in blob" in problem for problem in found)


def test_control_a_second_commit_on_top(clean):
    (clean / "data.txt").write_text("changed\n")
    write_manifest(clean)
    git(clean, "add", "--all")
    git(clean, "commit", "--quiet", "--message", "amended later")
    assert any("2 commits" in problem for problem in problems(clean))


def test_control_a_remote_a_tag_and_another_branch(clean):
    git(clean, "remote", "add", "origin", "https://example.org/x.git")
    git(clean, "tag", "v1")
    git(clean, "branch", "other")
    found = problems(clean)
    assert any("a remote is configured" in problem for problem in found)
    assert any("references other than the one branch" in problem for problem in found)


def test_control_a_file_on_disk_that_is_not_committed(clean):
    (clean / "backup.tar").write_bytes(b"x")
    found = problems(clean)
    assert any("not in the commit" in problem for problem in found)
    assert any("present but not listed: backup.tar" in problem for problem in found)


def test_control_a_link_instead_of_a_file(source, tmp_path):
    (source / "link").symlink_to("data.txt")
    write_manifest(source)
    git(source, "add", "--all")
    git(source, "commit", "--quiet", "--message", "a link")
    out = tmp_path / "with-link"
    candidate.build(source, "HEAD", out, NAME, EMAIL, "candidate")
    assert any("not a regular file: link" in problem for problem in problems(out))


def test_control_another_author_or_address(source, tmp_path):
    out = tmp_path / "other-author"
    candidate.build(source, "HEAD", out, NAME, OTHER_EMAIL, "candidate")
    found = problems(out)
    assert any("author or committer" in problem for problem in found)
    assert any("an e-mail address in commit" in problem for problem in found)


def test_a_file_may_give_the_declared_contact_address_and_no_other(source, tmp_path):
    (source / "contact.md").write_text(f"Write to {CONTACT}.\n", encoding="utf-8")
    write_manifest(source)
    git(source, "add", "--all")
    git(source, "commit", "--quiet", "--message", "a contact")
    out = tmp_path / "with-contact"
    candidate.build(source, "HEAD", out, NAME, EMAIL, "candidate")
    assert any("an e-mail address in blob" in problem for problem in problems(out))
    assert not any("e-mail address" in problem for problem in problems(out, contact=CONTACT))
    (source / "other.md").write_text(f"Or to {OTHER_EMAIL}.\n", encoding="utf-8")
    write_manifest(source)
    git(source, "add", "--all")
    git(source, "commit", "--quiet", "--message", "another address")
    other = tmp_path / "with-other"
    candidate.build(source, "HEAD", other, NAME, EMAIL, "candidate")
    assert any("an e-mail address in blob" in problem for problem in problems(other, contact=CONTACT))


def test_control_files_that_are_not_the_reviewed_files(source, clean):
    other_tree = git(source, "rev-parse", "HEAD~1^{tree}").strip()
    assert any("tree differs" in problem for problem in problems(clean, expected_tree=other_tree))
