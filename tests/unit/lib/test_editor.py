# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the editor utility helpers."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from terok.ui_utils.editor import _resolve_editor, open_in_editor
from tests.testfs import MOCK_BASE


def which_for(*available: str) -> Callable[[str], str | None]:
    """Return a ``find_host_tool`` side effect for the given available commands."""
    available_set = set(available)

    def _which(cmd: str) -> str | None:
        return str(MOCK_BASE / "bin" / cmd) if cmd in available_set else None

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
        pytest.param(
            "", which_for("nano"), str(MOCK_BASE / "bin" / "nano"), id="falls-back-to-nano"
        ),
        pytest.param("", which_for("vi"), str(MOCK_BASE / "bin" / "vi"), id="falls-back-to-vi"),
        pytest.param(
            "   ",
            which_for("nano"),
            str(MOCK_BASE / "bin" / "nano"),
            id="ignores-whitespace-editor",
        ),
        pytest.param(
            "nonexistent",
            which_for("nano"),
            str(MOCK_BASE / "bin" / "nano"),
            id="invalid-editor-env-falls-back",
        ),
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
    with patch("terok.ui_utils.editor.find_host_tool", side_effect=which_side_effect):
        assert _resolve_editor() == expected


def test_resolve_editor_returns_none_when_no_editor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Editor resolution returns ``None`` when nothing usable is found."""
    monkeypatch.setenv("EDITOR", "")
    with patch("terok.ui_utils.editor.find_host_tool", return_value=None):
        assert _resolve_editor() is None


def test_editor_uses_current_absolute_path_entries(tmp_path, monkeypatch) -> None:
    """Shared lookup rejects relative search entries and retains the selected executable."""
    editor = tmp_path / "fixture-editor"
    editor.write_text("fixture")
    editor.chmod(0o700)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EDITOR", "fixture-editor -w")
    monkeypatch.setenv("PATH", ".")
    assert _resolve_editor() is None
    monkeypatch.setenv("PATH", str(tmp_path))
    assert _resolve_editor() == shlex.join([str(editor), "-w"])


@pytest.mark.parametrize("configured", [True, False], ids=["editor-env", "fallback"])
def test_editor_executes_absolute_match_not_relative_shadow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: bool
) -> None:
    """Execution keeps the safe lookup result and the editor's quoted arguments."""
    name = "fixture-editor" if configured else "nano"
    for directory in (tmp_path / "relative", tmp_path / "absolute tools"):
        directory.mkdir()
        editor = directory / name
        editor.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            "Path(sys.argv[-1]).write_text(json.dumps(["
            f"{directory.name!r}, *sys.argv[1:-1]]))\n"
        )
        editor.chmod(0o700)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", f"relative:{tmp_path / 'absolute tools'}")
    monkeypatch.setenv("EDITOR", f"{name} -w 'argument with spaces'" if configured else "")
    target = config_path(tmp_path)

    assert open_in_editor(target)
    expected_args = ["-w", "argument with spaces"] if configured else []
    assert json.loads(target.read_text()) == ["absolute tools", *expected_args]


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
