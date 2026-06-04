# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Smoke tests for the TUI entry module and configuration bridge."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.testmodule_utils import assert_module_callable
from tests.unit.tui.tui_test_helpers import import_app


def test_tui_main_is_callable() -> None:
    """The TUI module exports a callable ``main`` entrypoint."""
    import_app()
    assert_module_callable("terok.tui.app")


@pytest.mark.parametrize(
    ("config_text", "expected"),
    [
        pytest.param(None, False, id="missing-config"),
        pytest.param("tui:\n  default_tmux: true\n", True, id="tmux-enabled"),
    ],
)
def test_tmux_configuration_integration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config_text: str | None,
    expected: bool,
) -> None:
    """The TUI module can read the ``tui.default_tmux`` configuration value."""
    from terok.lib.core.config import get_tui_default_tmux

    monkeypatch.delenv("TEROK_CONFIG_FILE", raising=False)
    if config_text is not None:
        cfg_path = tmp_path / "config.yml"
        cfg_path.write_text(config_text, encoding="utf-8")
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(cfg_path))

    assert get_tui_default_tmux() is expected


@pytest.mark.parametrize(
    ("force_new", "expected_session_args"),
    [
        pytest.param(False, ["new-session", "-A", "-s", "terok"], id="attach-shared"),
        pytest.param(True, ["new-session"], id="force-new"),
    ],
)
def test_launch_in_tmux_attaches_or_forks(
    monkeypatch: pytest.MonkeyPatch,
    force_new: bool,
    expected_session_args: list[str],
) -> None:
    """Default launch attaches to the shared ``terok`` session; ``--new-session`` forks."""
    import shutil

    from terok.tui import app

    monkeypatch.delenv("TMUX", raising=False)
    # ``_launch_in_tmux`` does a local ``import shutil``; patch the real module.
    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/tmux")

    captured: list[str] = []
    monkeypatch.setattr(app.os, "execvp", lambda _file, argv: captured.extend(argv))

    app._launch_in_tmux(force_new=force_new)

    # argv is ["tmux", "-f", <conf>, *session_args, "terok-tui"]; the conf path
    # is materialised at runtime so we assert around it rather than on it.
    assert captured[:2] == ["tmux", "-f"]
    assert captured[3:] == [*expected_session_args, "terok-tui"]
