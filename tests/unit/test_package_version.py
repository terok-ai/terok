# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The package version resolves with and without installed metadata."""

import importlib
import importlib.metadata
import tomllib
from pathlib import Path

import pytest

import terok

REPO_ROOT = Path(__file__).parents[2]


def test_version_is_a_nonempty_string() -> None:
    """The installed-metadata happy path."""
    assert isinstance(terok.__version__, str)
    assert terok.__version__


def test_version_falls_back_to_hatch_fallback_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No installed metadata -> the pyproject fallback-version is reported.

    This is the source-checkout dev-mode path; it must track the field
    hatch-vcs itself falls back to, not a table that no longer exists.
    """
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    expected = pyproject["tool"]["hatch"]["version"]["fallback-version"]

    def _missing(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", _missing)
    try:
        assert importlib.reload(terok).__version__ == expected
    finally:
        monkeypatch.undo()
        importlib.reload(terok)
