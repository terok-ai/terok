# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""What a task reached for, laid out for a promotion decision."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.table import Table
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Static

if TYPE_CHECKING:
    from collections.abc import Sequence

_VERDICTS = (
    ("blocked", "Refused by the policy"),
    ("bypass", "Let through by the timed window"),
)
_EMPTY = (
    "Nothing harvested yet.\n\n"
    "This task has not been refused anything, and no bypass window let anything\n"
    "through — there is nothing to promote."
)


class ShieldHarvestScreen(ModalScreen[None]):
    """A read-only review of one task's refusals and window accepts.

    Grouped by what the shield did, because that is what decides the options: a
    refusal is a candidate for an allowlist entry or a curated set, while a
    window accept is something the task already proved it needed.  Nothing here
    changes policy — promotion stays an explicit act elsewhere, and the tier
    that refused a host still decides whether it can be promoted at all.
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    CSS = """
    ShieldHarvestScreen { align: center middle; }
    #harvest-body { width: 96; max-height: 80%; padding: 1 2; background: $surface; overflow: auto; }
    """

    def __init__(self, task_label: str, entries: Sequence[Any]) -> None:
        """Render the harvest *entries* shield returned for *task_label*.

        Typed structurally rather than by import: the entries are shield's
        harvest rows, and the screen reads only ``action``, ``target``,
        ``count``, ``ports`` and ``last_seen`` off them.
        """
        super().__init__()
        self._task_label = task_label
        self._entries = list(entries)

    def compose(self) -> ComposeResult:
        """Show one table per verdict, or a sentence saying there is nothing."""
        body = Static(self._body(), id="harvest-body")
        body.border_title = f"What {self._task_label} reached for"
        body.border_subtitle = "Esc to close"
        yield body

    def _body(self) -> Table | str:
        """Build the grouped table, or the empty-state text."""
        if not self._entries:
            return _EMPTY
        table = Table.grid(padding=(0, 2))
        table.add_column("Target", ratio=3)
        table.add_column("Tries", justify="right")
        table.add_column("Ports")
        table.add_column("Last seen")
        for action, heading in _VERDICTS:
            rows = [entry for entry in self._entries if entry.action == action]
            if not rows:
                continue
            table.add_row(f"[bold]{heading}[/bold]", "", "", "")
            for entry in rows:
                table.add_row(
                    entry.target,
                    str(entry.count),
                    ",".join(str(port) for port in entry.ports) or "-",
                    entry.last_seen,
                )
            table.add_row("", "", "", "")
        return table

    def action_close(self) -> None:
        """Dismiss the review."""
        self.dismiss(None)
