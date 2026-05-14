# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for [`ConsoleOutputScreen`][terok.tui.console_output_screen.ConsoleOutputScreen].

The screen is a snapshot list over a
[`ConsoleLogRegistry`][terok.tui.console_log.ConsoleLogRegistry];
selecting a row opens that entry's
[`WorkerLogScreen`][terok.tui.worker_log_screen.WorkerLogScreen].
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.css.query import NoMatches
from textual.widgets import Label, ListView

from terok.tui.console_log import ConsoleLogRegistry
from terok.tui.console_output_screen import ConsoleOutputScreen
from terok.tui.worker_log_screen import WorkerLogScreen


class _Host(App):
    """Minimal app that pushes a [`ConsoleOutputScreen`][terok.tui.console_output_screen.ConsoleOutputScreen]."""

    def __init__(self, registry: ConsoleLogRegistry) -> None:
        """Stash the registry to display (``_log_registry`` — ``_registry`` is Textual's)."""
        super().__init__()
        self._log_registry = registry

    def on_mount(self) -> None:
        """Push the console-output list."""
        self.push_screen(ConsoleOutputScreen(self._log_registry))


@pytest.mark.asyncio
async def test_empty_registry_shows_empty_state() -> None:
    """With no entries the screen shows the empty-state label, not a list."""
    app = _Host(ConsoleLogRegistry())
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, ConsoleOutputScreen)
        assert screen.query_one("#console-output-empty", Label)
        with pytest.raises(NoMatches):
            screen.query_one("#console-output-list", ListView)


@pytest.mark.asyncio
async def test_entries_listed_most_recent_first() -> None:
    """Every registry entry gets a row, ordered newest-first."""
    registry = ConsoleLogRegistry()
    registry.create("Building images for alpha", ["x"], "terok build alpha")
    registry.create("Gate sync for beta", ["y"], "terok gate-sync beta")
    app = _Host(registry)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, ConsoleOutputScreen)
        listview = screen.query_one("#console-output-list", ListView)
        assert len(listview.children) == 2
        # Newest-first: the gate sync (created last) heads the snapshot.
        assert screen._entries[0].title == "Gate sync for beta"
        assert screen._entries[1].title == "Building images for alpha"


@pytest.mark.asyncio
async def test_selecting_entry_opens_its_worker_log_view() -> None:
    """Picking a row pushes a WorkerLogScreen bound to that exact entry."""
    registry = ConsoleLogRegistry()
    newest = registry.create("Newest action", ["x"], "terok newest")
    app = _Host(registry)
    async with app.run_test() as pilot:
        await pilot.press("enter")  # index 0 is highlighted by default
        await pilot.pause()
        opened = pilot.app.screen
        assert isinstance(opened, WorkerLogScreen)
        assert opened._entry is newest


@pytest.mark.asyncio
async def test_escape_dismisses() -> None:
    """Escape closes the console-output list."""
    app = _Host(ConsoleLogRegistry())
    async with app.run_test() as pilot:
        assert isinstance(pilot.app.screen, ConsoleOutputScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, ConsoleOutputScreen)
