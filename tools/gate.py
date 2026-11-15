"""Run every repository-only check on a fresh export of a commit, in a fresh environment.

    python tools/gate.py --work NEW_DIRECTORY [--ref HEAD] [--isolate]

What it does, in order:
  1. `git archive` of the commit into NEW_DIRECTORY/export. Untracked files of
     the working tree are not part of it; there is no `.git` in the export.
  2. a new virtual environment in NEW_DIRECTORY/venv, with the versions of
     requirements-observed.txt and nothing else.
  3. each command of `python -m nlebench_audit`, then `python -m pytest`, with
     the export as working directory and HOME set to NEW_DIRECTORY/home; then
     the project is built and installed from the export and its console script
     runs every command again.
  4. two controls on copies of the export: a pinned module removed, and the
     per-record table emptied. Each must make its command fail.

With --isolate every command of steps 3 and 4 runs under bubblewrap with the
home directories of the machine replaced by an empty directory, so that nothing
outside the export, the environment and the system Python can be read.

Every command is printed with its real exit code. The script exits non-zero if
a check fails or a control passes. It writes only inside NEW_DIRECTORY, which
must not exist.
"""
from __future__ import annotations

import argparse
import gzip
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMMANDS = ("verify", "tables", "fixtures", "cases", "stored-cases")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", required=True)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--isolate", action="store_true")
    args = parser.parse_args()
    work = Path(args.work).resolve()
    if work.exists():
        raise SystemExit(f"{work} exists; choose a new directory")
    export, venv, home = work / "export", work / "venv", work / "home"
    for folder in (export, home):
        folder.mkdir(parents=True)
    failures: list[str] = []

    def step(label: str, command: list[str], cwd: Path, expect_failure: bool = False, wrap: bool = True,
             show: bool = False) -> None:
        full = command
        if args.isolate and wrap:
            # Order matters: the empty /tmp and home directories are mounted first, and the work
            # directory is bound back afterwards in case it lies under one of them.
            full = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/home",
                    "--tmpfs", "/root", "--tmpfs", "/tmp", "--bind", str(work), str(work), "--chdir", str(cwd),
                    "--unshare-net", *command]
        done = subprocess.run(full, cwd=cwd, capture_output=True, text=True,
                              env={"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        tail = (done.stdout.strip().splitlines() or done.stderr.strip().splitlines() or [""])[-1]
        ok = (done.returncode != 0) if expect_failure else (done.returncode == 0)
        wanted = "non-zero" if expect_failure else "0"
        print(f"[{'ok' if ok else 'FAIL'}] {label}\n      $ {' '.join(command)}\n      exit={done.returncode} "
              f"(wanted {wanted}) | {tail}", flush=True)
        if show:
            print("      " + done.stdout.strip().replace("\n", "\n      "))
        if not ok:
            failures.append(label)
            print((done.stdout + done.stderr)[-3000:])

    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", args.ref], check=True, capture_output=True,
                            text=True).stdout.strip()
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", commit], check=True,
                             capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(export)], input=archive, check=True)
    files = sum(1 for path in export.rglob("*") if path.is_file())
    print(f"export of {commit}: {files} files in {export}; isolation: {'bubblewrap' if args.isolate else 'none'}")

    python = str(venv / "bin" / "python")
    step("create the environment", ["/usr/bin/python3", "-m", "venv", str(venv)], work, wrap=False)
    step("install the observed dependency versions",
         [python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-cache-dir", "-r",
          str(export / "requirements-observed.txt")], work, wrap=False)
    step("list the installed packages", [python, "-m", "pip", "list", "--format=freeze",
                                         "--disable-pip-version-check"], work, wrap=False, show=True)

    for command in COMMANDS:
        step(f"nlebench_audit {command}", [python, "-m", "nlebench_audit", command], export)
    step("tests of this repository", [python, "-m", "pytest", "-q"], export)

    # Packaging: build and install the project itself, then use its console script. The pinned
    # benchmark modules are not part of the installed package; they are read from the export.
    step("install this project from the export",
         [python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-cache-dir", "--no-deps",
          str(export)], work, wrap=False)
    step("the installed project and its version", [python, "-m", "pip", "show", "nle-bench-audit"], work,
         wrap=False, show=True)
    step("console script: nle-bench-audit all", [str(venv / "bin" / "nle-bench-audit"), "all"], export, show=True)

    removed = work / "control-missing-module"
    shutil.copytree(export, removed)
    (removed / "runtime" / "pins" / "dev-69f8d58a1382" / "nlebench" / "runner" / "scenario_loader.py").unlink()
    step("control: a pinned module removed, `cases` must fail", [python, "-m", "nlebench_audit", "cases"], removed,
         expect_failure=True)
    step("control: the same copy, `verify` must fail", [python, "-m", "nlebench_audit", "verify"], removed,
         expect_failure=True)

    emptied = work / "control-empty-table"
    shutil.copytree(export, emptied)
    table = emptied / "rejudging" / "per_record.csv.gz"
    header = gzip.decompress(table.read_bytes()).decode("utf-8").split("\n")[0]
    table.write_bytes(gzip.compress((header + "\n").encode("utf-8")))
    step("control: the per-record table emptied, `tables` must fail", [python, "-m", "nlebench_audit", "tables"],
         emptied, expect_failure=True)

    print(f"gate: {'PASS' if not failures else 'FAIL: ' + '; '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
