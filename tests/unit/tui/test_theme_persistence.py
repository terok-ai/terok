# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for TerokTUI's theme persistence wiring.

``_apply_saved_theme`` / ``_on_theme_picked`` are exercised on a minimal
real-Textual host that borrows the methods from ``TerokTUI`` — running
the full app (project loading, vault probing, pollers) buys nothing for
this contract:

* the saved ``tui.theme`` is applied on mount; unknown names fall back
  to the default;
* startup never writes the config file — only a genuine palette pick
  does, and a failed write degrades to a session-only theme with a
  warning instead of crashing.

Persistence is asserted against the real config file (routed to a tmp
path via ``TEROK_CONFIG_FILE``) rather than by monkeypatching the app
module — fresh-import tests elsewhere in the suite swap out
``terok.tui.app``, so a module-attribute patch is order-fragile while
the file is not.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.app import App

from terok.tui.app import TerokTUI


class _ThemeHost(App):
    """Real-Textual host borrowing TerokTUI's theme-persistence methods."""

    _apply_saved_theme = TerokTUI._apply_saved_theme
    _on_theme_picked = TerokTUI._on_theme_picked

    def __init__(self, saved: str | None) -> None:
        """Stand in a config snapshot carrying only the persisted theme."""
        super().__init__()
        self._config = SimpleNamespace(tui_theme=saved)

    def on_mount(self) -> None:
        """Mirror TerokTUI.on_mount's theme wiring."""
        self._apply_saved_theme()


@pytest.fixture
def config_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Route the global config file to a fresh tmp path and return it."""
    path = tmp_path / "config.yml"
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(path))
    return path


@pytest.mark.asyncio
async def test_saved_theme_is_applied_on_mount(config_file: Path) -> None:
    """A persisted theme name becomes the active theme at startup — without a re-save."""
    app = _ThemeHost("textual-light")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == "textual-light"
    assert not config_file.exists(), "applying the saved theme must not write the config"


@pytest.mark.asyncio
async def test_unknown_saved_theme_falls_back_to_default(config_file: Path) -> None:
    """A name the installed Textual doesn't know keeps the default theme."""
    app = _ThemeHost("no-such-theme")
    default = App().theme
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == default
    assert not config_file.exists(), "the fallback must not overwrite the saved value"


@pytest.mark.asyncio
async def test_startup_without_saved_theme_writes_nothing(config_file: Path) -> None:
    """With no persisted choice, startup leaves the config file alone."""
    app = _ThemeHost(None)
    async with app.run_test() as pilot:
        await pilot.pause()
    assert not config_file.exists()


@pytest.mark.asyncio
async def test_palette_pick_is_persisted(config_file: Path) -> None:
    """A genuine theme switch writes exactly that name to the config file."""
    app = _ThemeHost(None)
    async with app.run_test() as pilot:
        app.theme = "ansi-dark"
        await pilot.pause()
    assert "theme: ansi-dark" in config_file.read_text(encoding="utf-8")
    assert app._persisted_theme == "ansi-dark"


@pytest.mark.asyncio
async def test_failed_save_degrades_to_session_theme(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unwritable config keeps the theme for the session and warns, not crashes."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory must go", encoding="utf-8")
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(blocker / "config.yml"))

    app = _ThemeHost(None)
    async with app.run_test() as pilot:
        app.theme = "textual-light"
        await pilot.pause()
        assert app.theme == "textual-light"
        # The failed name was not recorded as persisted, so a later
        # pick retries the save.
        assert app._persisted_theme != "textual-light"
