# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the completions CLI subcommand."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from terok.cli.commands import completions


@pytest.fixture()
def patch_completion_locations(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., None]:
    """Return a helper that replaces completion search locations for one test."""

    def _apply(
        *,
        bash: tuple[Path, ...] = (),
        zsh: tuple[Path, ...] = (),
        fish: tuple[Path, ...] = (),
        rc: tuple[Path, ...] = (),
    ) -> None:
        monkeypatch.setattr(completions, "_BASH_COMPLETION_DIRS", bash)
        monkeypatch.setattr(completions, "_ZSH_COMPLETION_DIRS", zsh)
        monkeypatch.setattr(completions, "_FISH_COMPLETION_DIRS", fish)
        monkeypatch.setattr(completions, "_SHELL_RC_FILES", rc)

    return _apply


def install_targets(tmp_path: Path) -> dict[str, Path]:
    """Build completion-install paths rooted in *tmp_path*."""
    return {
        "bash": tmp_path / "bash" / "terokctl",
        "zsh": tmp_path / "zsh" / "_terokctl",
        "fish": tmp_path / "fish" / "terokctl.fish",
    }


@pytest.mark.parametrize(
    ("shell", "expected"),
    [
        pytest.param("/bin/bash", "bash", id="bash"),
        pytest.param("/usr/bin/zsh", "zsh", id="zsh"),
        pytest.param("/usr/bin/fish", "fish", id="fish"),
    ],
)
def test_detect_shell_returns_supported_shell(
    monkeypatch,
    shell: str,
    expected: str,
) -> None:
    """Shell detection accepts the supported interactive shells."""
    monkeypatch.setenv("SHELL", shell)
    assert completions._detect_shell() == expected


@pytest.mark.parametrize(
    "shell",
    [pytest.param("/bin/tcsh", id="unsupported"), pytest.param(None, id="missing")],
)
def test_detect_shell_rejects_unknown_shell(monkeypatch, shell: str | None) -> None:
    """Unsupported or missing ``$SHELL`` values cause a clean CLI exit."""
    if shell is None:
        monkeypatch.delenv("SHELL", raising=False)
    else:
        monkeypatch.setenv("SHELL", shell)

    with pytest.raises(SystemExit):
        completions._detect_shell()


@pytest.mark.parametrize(
    ("requested_shell", "detected_shell", "expected_shell"),
    [
        pytest.param("bash", None, "bash", id="explicit-shell"),
        pytest.param(None, "fish", "fish", id="auto-detected-shell"),
    ],
)
@patch("terok.cli.commands.completions.shellcode", return_value="# completion")
def test_install_completions_writes_to_selected_target(
    _mock_shellcode,
    monkeypatch,
    tmp_path: Path,
    requested_shell: str | None,
    detected_shell: str | None,
    expected_shell: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Completion install writes the generated script to the resolved target path."""
    targets = install_targets(tmp_path)
    monkeypatch.setattr(completions, "_INSTALL_TARGETS", targets)
    if detected_shell is not None:
        monkeypatch.setattr(completions, "_detect_shell", lambda: detected_shell)

    completions._install_completions(requested_shell)

    target = targets[expected_shell]
    assert target.is_file()
    assert "# completion" in target.read_text(encoding="utf-8")
    assert str(target) in capsys.readouterr().out


def test_is_completion_installed_returns_false_when_nothing_found(
    patch_completion_locations,
) -> None:
    """Completion detection is false when no autoload file or RC marker exists."""
    patch_completion_locations()
    assert not completions.is_completion_installed()


@pytest.mark.parametrize(
    ("attr", "filename"),
    [
        pytest.param("bash", "terokctl", id="bash-autoload"),
        pytest.param("zsh", "_terokctl", id="zsh-autoload"),
        pytest.param("fish", "terokctl.fish", id="fish-autoload"),
    ],
)
def test_is_completion_installed_detects_autoload_files(
    patch_completion_locations,
    tmp_path: Path,
    attr: str,
    filename: str,
) -> None:
    """Completion detection succeeds when an autoload target exists."""
    (tmp_path / filename).write_text("# comp", encoding="utf-8")
    patch_completion_locations(**{attr: (tmp_path,)})
    assert completions.is_completion_installed()


def test_is_completion_installed_detects_rc_marker(
    patch_completion_locations,
    tmp_path: Path,
) -> None:
    """Completion detection succeeds when a shell RC file has the registration marker."""
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("# register-python-argcomplete terokctl\n", encoding="utf-8")
    patch_completion_locations(rc=(rc_file,))
    assert completions.is_completion_installed()
