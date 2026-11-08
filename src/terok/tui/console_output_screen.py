# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Palette-reachable list of all dispatched-action console logs.

The ``Console output`` command surfaces every
[`ConsoleLogEntry`][terok.tui.console_log.ConsoleLogEntry] from the
session — running and finished alike — so output that would once have
scrolled past in a suspended terminal stays reachable inside the TUI
(issue #473). Selecting an entry opens its pending question, or its
[`WorkerLogScreen`][terok.tui.worker_log_screen.WorkerLogScreen] view when no question is pending.

The list updates as actions start, finish, or request input. Entries
are in-memory only — forgotten when the app closes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, NamedTuple

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListItem, ListView, Static

from ..lib.util.emoji import render_emoji
from .console_log import LogStatus
from .worker_log_screen import WorkerLogScreen

if TYPE_CHECKING:
    from .console_log import ConsoleLogEntry, ConsoleLogRegistry


class _StatusDisplay(NamedTuple):
    """Icon and text fallback for an action's lifecycle state."""

    emoji: str
    label: str


_STATUS_DISPLAY = {
    LogStatus.RUNNING: _StatusDisplay("⏳", "running"),
    LogStatus.WAITING: _StatusDisplay("❓", "needs input"),
    LogStatus.DONE: _StatusDisplay("✅", "done"),
    LogStatus.FAILED: _StatusDisplay("❌", "failed"),
}


class ConsoleActionRow(ListItem):
    """An action's description, updated when it finishes or requests input."""

    def __init__(self, entry: ConsoleLogEntry) -> None:
        """Keep the action identity independent of its position in the list."""
        self.entry = entry
        self._unsubscribe: Callable[[], None] | None = None
        super().__init__(
            Label(),
            Label(Text(f"$ {entry.command}"), classes="console-output-command"),
        )

    def on_mount(self) -> None:
        """Show the current status and subscribe to subsequent transitions."""
        self._update_status()
        self._unsubscribe = self.entry.subscribe_state(self._update_status)

    def on_unmount(self) -> None:
        """Stop updating the row after its list closes."""
        if self._unsubscribe is not None:
            self._unsubscribe()

    def _update_status(self) -> None:
        """Render literal titles so bracketed names cannot become Rich markup."""
        badge = _STATUS_DISPLAY[self.entry.status]
        self.query_one(Label).update(
            Text(f"{render_emoji(badge)} {self.entry.title} — {badge.label}")
        )


class BackgroundActions(Static):
    """Mouse-only status counter linking to the session's background actions."""

    can_focus = False

    DEFAULT_CSS = """
    BackgroundActions {
        width: auto;
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    BackgroundActions:hover {
        background: $primary;
        color: $text;
    }
    BackgroundActions.needs-input {
        color: $warning;
        text-style: bold;
    }
    """

    def __init__(self, registry: ConsoleLogRegistry) -> None:
        """Track user-dispatched actions, not periodic refresh workers."""
        super().__init__(id="background-actions")
        self._registry = registry
        self._unsubscribe: Callable[[], None] | None = None
        self.tooltip = "Open Console output (Ctrl+P → Console output)"

    def on_mount(self) -> None:
        """Show the initial count and follow registry changes."""
        self._update_count()
        self._unsubscribe = self._registry.subscribe(self._update_count)

    def on_unmount(self) -> None:
        """Release the registry subscription."""
        if self._unsubscribe is not None:
            self._unsubscribe()

    def _update_count(self) -> None:
        """Count waiting actions as active until they actually finish."""
        active = self._registry.running
        waiting = sum(bool(entry.pending_inputs) for entry in active)
        suffix = f" · {waiting} needs input" if waiting else ""
        self.update(Text(f"{len(active)} active{suffix}"))
        self.set_class(bool(waiting), "needs-input")

    def on_click(self) -> None:
        """Open the same action list offered by the command palette."""
        self.app.push_screen(ConsoleOutputScreen(self._registry))


class ConsoleOutputScreen(ModalScreen[None]):
    """Modal list of the session's [`ConsoleLogEntry`][terok.tui.console_log.ConsoleLogEntry] objects.

    Reachable from the command palette and the background-action counter.
    Selecting a waiting row opens its question. The Show log button keeps
    its captured output accessible without answering that question.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    CSS = """
    ConsoleOutputScreen {
        align: center middle;
    }

    #console-output-dialog {
        width: 80%;
        height: 70%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #console-output-list {
        height: 1fr;
        border: round $primary-darken-2;
        margin-bottom: 1;
    }

    #console-output-empty {
        height: 1fr;
        color: $text-muted;
        content-align: center middle;
    }

    .console-output-command {
        color: $text-muted;
    }

    #console-output-buttons {
        height: 3;
        align-horizontal: right;
    }
    """

    def __init__(self, registry: ConsoleLogRegistry) -> None:
        """Show *registry*'s actions most-recent first."""
        super().__init__()
        self._registry = registry
        self._entries: list[ConsoleLogEntry] = registry.entries
        self._unsubscribe: Callable[[], None] | None = None

    def compose(self) -> ComposeResult:
        """Lay out the entry list (or an empty-state label) and a Close button."""
        dialog = Vertical(id="console-output-dialog")
        dialog.border_title = "Console output"
        with dialog:
            yield ListView(
                *(ConsoleActionRow(entry) for entry in self._entries),
                id="console-output-list",
            )
            yield Label(
                "No console output yet — dispatched actions (image builds, "
                "gate / vault operations, container starts) appear here.",
                id="console-output-empty",
            )
            with Horizontal(id="console-output-buttons"):
                yield Button("Show log", id="console-output-show-log", disabled=not self._entries)
                yield Button("Close", id="console-output-close", variant="default")

    def on_mount(self) -> None:
        """Follow newly dispatched actions while keeping existing rows in place."""
        self._show_empty_state()
        self._unsubscribe = self._registry.subscribe(self._queue_sync)
        self._queue_sync()

    def on_unmount(self) -> None:
        """Stop listening when the list is dismissed."""
        if self._unsubscribe is not None:
            self._unsubscribe()

    def _queue_sync(self) -> None:
        """Serialize list insertions through the screen's message queue."""
        self.call_later(self._sync_entries)

    async def _sync_entries(self) -> None:
        """Prepend new actions without rebuilding rows or losing the selection."""
        known = {entry.id for entry in self._entries}
        new = [entry for entry in self._registry.entries if entry.id not in known]
        if new:
            listing = self.query_one(ListView)
            selected = listing.index
            self._entries = new + self._entries
            await listing.insert(0, [ConsoleActionRow(entry) for entry in new])
            listing.index = selected + len(new) if selected is not None else 0
        self._show_empty_state()

    def _show_empty_state(self) -> None:
        """Use the available space for either actions or the empty-state hint."""
        self.query_one(ListView).display = bool(self._entries)
        self.query_one("#console-output-empty").display = not self._entries
        self.query_one("#console-output-show-log", Button).disabled = not self._entries

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Answer a pending question, or inspect the selected action's output."""
        if isinstance(event.item, ConsoleActionRow):
            entry = event.item.entry
            if entry.pending_inputs:
                entry.request_input()
            else:
                self.app.push_screen(WorkerLogScreen(entry))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Keep logs accessible even when selecting a row would open its question."""
        if event.button.id == "console-output-close":
            self.dismiss()
        elif event.button.id == "console-output-show-log":
            row = self.query_one(ListView).highlighted_child
            if isinstance(row, ConsoleActionRow):
                self.app.push_screen(WorkerLogScreen(row.entry))
