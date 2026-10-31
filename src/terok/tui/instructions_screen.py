# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The instructions every project starts from, on one screen.

Shows how a task's instructions build up, and whether the global layer
keeps the bundled default.  That setting lives here as well as in the
integrated editor, because ``$EDITOR`` has no checkbox to show it.
The project level has its own switch on the project details screen.
"""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Label

EDIT_GLOBAL = "edit_global"
"""Dismiss value: the operator wants to edit the global instructions."""
SHOW_DEFAULT = "show_default"
"""Dismiss value: the operator wants to read the bundled default."""


class InstructionsScreen(ModalScreen[str | None]):
    """The bundled default and the global instructions, and how they combine.

    Dismisses with [`EDIT_GLOBAL`][terok.tui.instructions_screen.EDIT_GLOBAL]
    or [`SHOW_DEFAULT`][terok.tui.instructions_screen.SHOW_DEFAULT] for the
    caller to act on, and ``None`` on Close.  The inherit setting has no
    Save button to wait for: a change is written at once.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    CSS = """
    InstructionsScreen {
        align: center middle;
    }

    #instructions-dialog {
        width: 76;
        max-width: 100%;
        height: auto;
        max-height: 90%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    .instructions-text {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    .instructions-note {
        width: 100%;
        height: auto;
        margin-bottom: 1;
        color: $text-muted;
    }

    #instructions-inherit {
        margin-bottom: 1;
    }

    #instructions-buttons {
        height: 3;
        align-horizontal: right;
    }

    #instructions-buttons Button {
        margin-left: 1;
    }
    """

    def compose(self) -> ComposeResult:
        """Lay out the layers, the file's state, the inherit setting and the buttons."""
        from terok.lib.api import get_config
        from terok.lib.api.agents import global_instructions_path, inherits_instructions

        path = global_instructions_path()
        inherit = inherits_instructions(get_config().global_config_path)
        dialog = Vertical(id="instructions-dialog")
        dialog.border_title = "Instructions"
        with dialog:
            yield Label(
                "A task receives the bundled default, then the global instructions, "
                "then the instructions of its project.",
                classes="instructions-text",
            )
            yield Label(f"Global instructions: {_describe(path)}", classes="instructions-text")
            yield Checkbox(
                "Include the bundled default instructions",
                inherit is not False,
                disabled=inherit is None,
                compact=True,
                id="instructions-inherit",
            )
            yield Label(
                "Turn this off to replace the bundled default with the global instructions.  "
                "A project keeps or replaces all of this with its own setting."
                if inherit is not None
                else "agent.instructions in config.yml is not a list: edit config.yml to change it.",
                classes="instructions-note",
            )
            with Horizontal(id="instructions-buttons"):
                yield Button("Edit global", id="instructions-edit-global", variant="primary")
                yield Button("View default", id="instructions-show-default", variant="default")
                yield Button("Close", id="instructions-close", variant="default")

    @on(Checkbox.Changed, "#instructions-inherit")
    def _on_inherit_changed(self, event: Checkbox.Changed) -> None:
        """Write the new setting; a refused write puts the checkbox back and says why."""
        from terok.lib.api import get_config
        from terok.lib.api.agents import set_instructions_inherit

        try:
            set_instructions_inherit(get_config().global_config_path, event.value)
        except (OSError, SystemExit, ValueError) as exc:
            self.notify(f"Setting not written: {exc}", severity="error", markup=False)
            with event.checkbox.prevent(Checkbox.Changed):
                event.checkbox.value = not event.value

    @on(Button.Pressed, "#instructions-edit-global")
    def _on_edit_global(self) -> None:
        """Edit global — the caller opens the editor."""
        self.dismiss(EDIT_GLOBAL)

    @on(Button.Pressed, "#instructions-show-default")
    def _on_show_default(self) -> None:
        """View default — the caller opens the viewer."""
        self.dismiss(SHOW_DEFAULT)

    @on(Button.Pressed, "#instructions-close")
    def _on_close(self) -> None:
        """Close button — nothing more to do."""
        self.dismiss(None)


def _describe(path: Path) -> str:
    """Return where the global file is, and whether it has any text yet."""
    return f"{path}" if path.is_file() and path.stat().st_size else f"{path} (empty)"
