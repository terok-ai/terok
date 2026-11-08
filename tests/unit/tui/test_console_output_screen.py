# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for [`ConsoleOutputScreen`][terok.tui.console_output_screen.ConsoleOutputScreen].

The screen is a live list over a
[`ConsoleLogRegistry`][terok.tui.console_log.ConsoleLogRegistry];
selecting a row opens that entry's
[`WorkerLogScreen`][terok.tui.worker_log_screen.WorkerLogScreen].
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Label, ListView

from terok.tui.console_log import ConsoleLogRegistry
from terok.tui.console_output_screen import BackgroundActions, ConsoleActionRow, ConsoleOutputScreen
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
        assert not screen.query_one("#console-output-list", ListView).display


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


@pytest.mark.asyncio
async def test_close_button_dismisses() -> None:
    """The Close button closes the console-output list."""
    app = _Host(ConsoleLogRegistry())
    async with app.run_test() as pilot:
        assert isinstance(pilot.app.screen, ConsoleOutputScreen)
        await pilot.click("#console-output-close")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, ConsoleOutputScreen)


@pytest.mark.asyncio
async def test_rows_update_status_and_preserve_literal_titles() -> None:
    """Waiting and terminal icons change without reopening the list."""
    registry = ConsoleLogRegistry()
    entry = registry.create("Build [demo]", ["x"], "x")
    app = _Host(registry)
    async with app.run_test() as pilot:
        label = app.screen.query_one(ConsoleActionRow).query_one(Label)
        assert "⏳ Build [demo]" in str(label.render())
        entry.add_input("key", lambda: None)
        await pilot.pause()
        assert "❓ Build [demo] — needs input" in str(label.render())
        entry.remove_input("key")
        entry.finish(0)
        await pilot.pause()
        assert "✅ Build [demo]" in str(label.render())


@pytest.mark.asyncio
async def test_select_waiting_action_opens_question_without_interrupting_focus() -> None:
    """A new question waits for selection, rather than taking over the screen."""
    registry = ConsoleLogRegistry()
    entry = registry.create("Gate sync", ["x"], "x")
    opened: list[bool] = []
    app = _Host(registry)
    async with app.run_test() as pilot:
        before = app.focused
        entry.add_input("key", lambda: opened.append(True))
        await pilot.pause()
        assert app.focused is before
        assert not opened
        await pilot.press("enter")
        assert opened == [True]
        assert isinstance(app.screen, ConsoleOutputScreen)


@pytest.mark.asyncio
async def test_new_actions_appear_in_open_list_without_losing_selection() -> None:
    """A running task manager can discover work dispatched after it opened."""
    registry = ConsoleLogRegistry()
    app = _Host(registry)
    async with app.run_test() as pilot:
        first = registry.create("First", ["x"], "x")
        await pilot.pause()
        listing = app.screen.query_one(ListView)
        assert listing.display
        assert listing.index == 0
        registry.create("Second", ["x"], "x")
        await pilot.pause()
        assert len(listing.children) == 2
        assert listing.index == 1
        assert listing.highlighted_child.entry is first
        first.finish(1)
        await pilot.pause()
        assert "❌" in str(listing.highlighted_child.query_one(Label).render())


class _CounterHost(App):
    """Render the counter between focusable controls to exercise keyboard navigation."""

    def __init__(self, registry: ConsoleLogRegistry) -> None:
        super().__init__()
        self.logs = registry

    def compose(self) -> ComposeResult:
        yield Button("Before", id="before")
        yield BackgroundActions(self.logs)
        yield Button("After", id="after")


@pytest.mark.asyncio
async def test_counter_tracks_active_and_waiting_and_opens_list_without_tab_focus() -> None:
    """The counter remains clickable at zero and never enters the focus chain."""
    registry = ConsoleLogRegistry()
    app = _CounterHost(registry)
    async with app.run_test() as pilot:
        counter = app.query_one(BackgroundActions)
        assert "0 active" in str(counter.render())
        app.query_one("#before").focus()
        await pilot.press("tab")
        assert app.focused is app.query_one("#after")
        first = registry.create("First", ["x"], "x")
        second = registry.create("Second", ["x"], "x")
        first.add_input("key", lambda: None)
        first.add_input("other", lambda: None)
        await pilot.pause()
        assert "2 active · 1 needs input" in str(counter.render())
        assert counter.has_class("needs-input")
        second.finish(0)
        first.finish(1)
        await pilot.pause()
        assert "0 active" in str(counter.render())
        assert not counter.has_class("needs-input")
        await pilot.click("#background-actions")
        assert isinstance(app.screen, ConsoleOutputScreen)


@pytest.mark.asyncio
async def test_action_created_before_list_mount_is_not_lost() -> None:
    """Opening the list cannot miss dispatches between composition and subscription."""
    registry = ConsoleLogRegistry()
    screen = ConsoleOutputScreen(registry)
    entry = registry.create("During mount", ["x"], "x")
    app = App()
    async with app.run_test() as pilot:
        await app.push_screen(screen)
        await pilot.pause()
        assert screen._entries == [entry]
        assert len(screen.query_one(ListView).children) == 1


@pytest.mark.asyncio
async def test_waiting_action_log_can_be_inspected_without_answering() -> None:
    """Pending questions must not hide diagnostic output behind the prompt."""
    registry = ConsoleLogRegistry()
    entry = registry.create("Gate sync", ["x"], "x")
    entry.add_input("key", lambda: pytest.fail("Show log opened the question"))
    app = _Host(registry)
    async with app.run_test() as pilot:
        await pilot.click("#console-output-show-log")
        assert isinstance(app.screen, WorkerLogScreen)
        assert app.screen._entry is entry
        assert entry.pending_inputs == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_counter_is_at_bottom_right_of_main_layout(size: tuple[int, int]) -> None:
    """The real main-screen composition reserves a visible, clickable status row."""
    from terok.tui.app import TerokTUI

    class LayoutHost(App):
        CSS = TerokTUI.CSS

        def __init__(self) -> None:
            super().__init__()
            self.console_logs = ConsoleLogRegistry()

        def compose(self) -> ComposeResult:
            yield from TerokTUI.compose(self)

    app = LayoutHost()
    async with app.run_test(size=size) as pilot:
        counter = app.query_one(BackgroundActions)
        assert counter.region.right == size[0]
        assert counter.region.y == size[1] - 2
        assert counter not in app.screen.focus_chain
        await pilot.click("#background-actions")
        assert isinstance(app.screen, ConsoleOutputScreen)


@pytest.mark.asyncio
async def test_status_icons_honor_no_emoji_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """The console action icons use the same text fallback as the rest of the TUI."""
    from terok.lib.util import emoji

    monkeypatch.setattr(emoji, "_emoji_enabled", False)
    registry = ConsoleLogRegistry()
    registry.create("Build [demo]", ["x"], "x")
    app = _Host(registry)
    async with app.run_test():
        label = app.screen.query_one(ConsoleActionRow).query_one(Label)
        assert "[running] Build [demo]" in str(label.render())
