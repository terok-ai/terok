# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Modal that views a [`ConsoleLogEntry`][terok.tui.console_log.ConsoleLogEntry]'s output.

Retires the ``app.suspend()`` + "run it in the underlying terminal"
pattern for long-running shell-outs (podman build, git clone --mirror,
terok setup, …) so they work identically over ``terok-web`` /
textual-serve where there is no terminal to suspend *to* (issue #473).

``WorkerLogScreen`` no longer owns the subprocess — the
[`ConsoleLogRegistry`][terok.tui.console_log.ConsoleLogRegistry] pump
does.  This screen is a pure *view*: it replays the entry's captured
lines, tails new ones live, and offers a single button —

* **Hide** while the action is still running: dismisses the modal but
  the action keeps going in the background (the pump worker is
  app-scoped), fires its completion toast, and stays re-openable from
  the ``Console output`` palette command.
* **Done** / **Close** once it has finished.

Escape mirrors the button.  Because dismissing never kills the action,
Escape is safe at any time — unlike the old owns-the-process screen,
which had to refuse mid-run dismissal.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, RichLog

if TYPE_CHECKING:
    from .console_log import ConsoleLogEntry


class WorkerLogScreen(ModalScreen[None]):
    """Modal view over a [`ConsoleLogEntry`][terok.tui.console_log.ConsoleLogEntry].

    Example::

        entry = app.dispatch_console_action(
            "terok.lib.api:build_images",
            project_id,
            title=f"Building images for {project_id}",
        )
        app.push_screen(WorkerLogScreen(entry))

    Pushed non-blocking — the caller does not await a result; the
    entry's completion toast (and any ``on_complete`` passed to
    ``dispatch_*``) is the signal that the action finished.  A caller
    that genuinely needs the outcome awaits ``entry.wait()`` instead.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    CSS = """
    WorkerLogScreen {
        align: center middle;
    }

    #worker-log-dialog {
        width: 90%;
        height: 80%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #worker-log-command {
        color: $text-muted;
        margin-bottom: 1;
        height: auto;
    }

    #worker-log-output {
        height: 1fr;
        border: round $primary-darken-2;
        margin-bottom: 1;
    }

    #worker-log-buttons {
        height: 3;
        align-horizontal: right;
    }

    #worker-log-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, entry: ConsoleLogEntry) -> None:
        """Create the modal as a view over an existing *entry*.

        Args:
            entry: The [`ConsoleLogEntry`][terok.tui.console_log.ConsoleLogEntry]
                to display.  It may be still running or already
                finished — both are handled on mount.
        """
        super().__init__()
        self._entry = entry
        self._unsubscribe: Callable[[], None] | None = None

    def compose(self) -> ComposeResult:
        """Lay out the command line, log pane, and single-action button row."""
        dialog = Vertical(id="worker-log-dialog")
        dialog.border_title = self._entry.title
        with dialog:
            yield Label(f"$ {self._entry.command}", id="worker-log-command")
            yield RichLog(id="worker-log-output", markup=False, wrap=True)
            with Horizontal(id="worker-log-buttons"):
                yield Button("Hide", id="worker-log-close", variant="default")

    def on_mount(self) -> None:
        """Replay captured output, then live-tail if the entry is still running."""
        log = self.query_one("#worker-log-output", RichLog)
        for line in self._entry.lines:
            log.write(line)
        if self._entry.running:
            self._unsubscribe = self._entry.subscribe(self._append_line, self._on_entry_finished)
        else:
            self._render_finished()

    def on_unmount(self) -> None:
        """Drop the live subscription so a hidden entry has no dangling viewer."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _append_line(self, line: str) -> None:
        """Write one live-tailed *line* into the log pane."""
        self.query_one("#worker-log-output", RichLog).write(line)

    def _on_entry_finished(self) -> None:
        """The entry finished while this view was open — reflect it in the button."""
        self._render_finished()

    def _render_finished(self) -> None:
        """Relabel the button to the entry's terminal state."""
        button = self.query_one("#worker-log-close", Button)
        if self._entry.ok:
            button.label = "Done"
            button.variant = "success"
        else:
            button.label = "Close"
            button.variant = "warning"

    @on(Button.Pressed, "#worker-log-close")
    def _on_close(self) -> None:
        """Hide (if running) or Close (if finished) — both just dismiss the view.

        The entry's pump worker is app-scoped, so dismissing never kills
        a running action: it keeps going in the background.
        """
        self.dismiss()
