# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the editor utility helpers."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from terok.ui_utils.editor import _resolve_editor, open_in_editor


def which_for(*available: str) -> Callable[[str], str | None]:
    """Return a ``shutil.which`` side effect for the given available commands."""
    available_set = set(available)

    def _which(cmd: str) -> str | None:
        return cmd if cmd in available_set else None

    return _which


def config_path(tmp_path: Path) -> Path:
    """Create and return a temporary config file path."""
    path = tmp_path / "config.yml"
    path.write_text("x", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("editor", "which_side_effect", "expected"),
    [
        pytest.param(
            "/usr/bin/custom-editor",
            which_for("/usr/bin/custom-editor"),
            "/usr/bin/custom-editor",
            id="prefers-editor-env",
        ),
        pytest.param("", which_for("nano"), "nano", id="falls-back-to-nano"),
        pytest.param("", which_for("vi"), "vi", id="falls-back-to-vi"),
        pytest.param("   ", which_for("nano"), "nano", id="ignores-whitespace-editor"),
        pytest.param("nonexistent", which_for("nano"), "nano", id="invalid-editor-env-falls-back"),
    ],
)
def test_resolve_editor_prefers_env_then_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
    editor: str,
    which_side_effect: Callable[[str], str | None],
    expected: str,
) -> None:
    """Editor resolution prefers ``$EDITOR`` and otherwise falls back to common editors."""
    monkeypatch.setenv("EDITOR", editor)
    with patch("shutil.which", side_effect=which_side_effect):
        assert _resolve_editor() == expected


def test_resolve_editor_returns_none_when_no_editor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Editor resolution returns ``None`` when nothing usable is found."""
    monkeypatch.setenv("EDITOR", "")
    with patch("shutil.which", return_value=None):
        assert _resolve_editor() is None


@pytest.mark.parametrize(
    ("resolved_editor", "run_side_effect", "expected", "expect_error_output"),
    [
        pytest.param("nano", None, True, False, id="success"),
        pytest.param(None, None, False, True, id="no-editor"),
        pytest.param(
            "nano",
            subprocess.CalledProcessError(1, "nano"),
            False,
            False,
            id="called-process-error",
        ),
        pytest.param("nano", FileNotFoundError(), False, False, id="file-not-found"),
    ],
)
def test_open_in_editor_outcomes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    resolved_editor: str | None,
    run_side_effect: Exception | None,
    expected: bool,
    expect_error_output: bool,
) -> None:
    """Opening a file in the editor succeeds or fails cleanly for common outcomes."""
    path = config_path(tmp_path)
    with (
        patch("terok.ui_utils.editor._resolve_editor", return_value=resolved_editor),
        patch("subprocess.run", side_effect=run_side_effect) as mock_run,
    ):
        assert open_in_editor(path) is expected
        if resolved_editor is not None and run_side_effect is None:
            mock_run.assert_called_once_with([resolved_editor, str(path)], check=True)

    err = capsys.readouterr().err
    if expect_error_output:
        assert "EDITOR" in err
    else:
        assert err == ""
