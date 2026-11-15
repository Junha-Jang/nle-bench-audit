from __future__ import annotations

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture()
def copy(tmp_path) -> Path:
    """A disposable copy of the checkout, for tests that damage it."""
    target = tmp_path / "checkout"
    shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns(
        ".git", ".venv", "venv", "__pycache__", ".pytest_cache", "*.egg-info", "build", "dist"))
    return target
