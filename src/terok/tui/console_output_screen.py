# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Palette-reachable list of all dispatched-action console logs.

The ``Console output`` command surfaces every
[`ConsoleLogEntry`][terok.tui.console_log.ConsoleLogEntry] from the
session — running and finished alike — so output that would once have
scrolled past in a suspended terminal stays reachable inside the TUI
(issue #473).  Selecting an entry opens its
[`WorkerLogScreen`][terok.tui.worker_log_screen.WorkerLogScreen] view.

The list is a snapshot taken when the screen opens; reopen it to pick
up newly dispatched actions.  Entries are in-memory only — forgotten
when the app closes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListItem, ListView

from .console_log import LogStatus
from .worker_log_screen import WorkerLogScreen

if TYPE_CHECKING:
    from .console_log import ConsoleLogEntry, ConsoleLogRegistry

#: Short status word shown at the head of each list row.
_STATUS_LABEL: dict[LogStatus, str] = {
    LogStatus.RUNNING: "running",
    LogStatus.DONE: "done",
    LogStatus.FAILED: "failed",
}


class ConsoleOutputScreen(ModalScreen[None]):
    """Modal list of the session's [`ConsoleLogEntry`][terok.tui.console_log.ConsoleLogEntry] objects.

    Pushed from the ``Console output`` command-palette entry.  Picking a
    row opens that entry's
    [`WorkerLogScreen`][terok.tui.worker_log_screen.WorkerLogScreen] on
    top — closing it returns here.
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
        """Snapshot *registry*'s entries (most-recent first) for display."""
        super().__init__()
        self._entries: list[ConsoleLogEntry] = registry.entries

    def compose(self) -> ComposeResult:
        """Lay out the entry list (or an empty-state label) and a Close button."""
        dialog = Vertical(id="console-output-dialog")
        dialog.border_title = "Console output"
        with dialog:
            if self._entries:
                yield ListView(
                    *(self._row(entry) for entry in self._entries),
                    id="console-output-list",
                )
            else:
                yield Label(
                    "No console output yet — dispatched actions (image builds, "
                    "gate / vault operations, container starts) appear here.",
                    id="console-output-empty",
                )
            with Horizontal(id="console-output-buttons"):
                yield Button("Close", id="console-output-close", variant="default")

    @staticmethod
    def _row(entry: ConsoleLogEntry) -> ListItem:
        """Build the two-line list row for *entry* — status + title, then command."""
        return ListItem(
            Label(f"[{_STATUS_LABEL[entry.status]}]  {entry.title}"),
            Label(f"$ {entry.command}", classes="console-output-command"),
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Open the picked entry's [`WorkerLogScreen`][terok.tui.worker_log_screen.WorkerLogScreen] view."""
        index = event.list_view.index
        if index is not None and 0 <= index < len(self._entries):
            self.app.push_screen(WorkerLogScreen(self._entries[index]))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Close the screen on the Close button."""
        if event.button.id == "console-output-close":
            self.dismiss()
