# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Native text editor / viewer modals.

The web-compatible replacement for the ``$EDITOR``-subprocess and
"print to a suspended terminal" instruction workflows (issue #473).
Both are plain in-process modals — no child process, no terminal:

* [`TextEditorScreen`][terok.tui.text_screens.TextEditorScreen] — an
  editable [`TextArea`][textual.widgets.TextArea]; dismisses with the
  edited text on Save, ``None`` on Cancel.
* [`TextViewScreen`][terok.tui.text_screens.TextViewScreen] — the same
  widget in read-only mode for display-only content.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, TextArea


class TextEditorScreen(ModalScreen[str | None]):
    """Modal [`TextArea`][textual.widgets.TextArea] editor.

    Dismisses with the (possibly edited) text on Save, or ``None`` on
    Cancel / Escape — the caller decides whether to persist.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    TextEditorScreen {
        align: center middle;
    }

    #text-editor-dialog {
        width: 90%;
        height: 85%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #text-editor-area {
        height: 1fr;
        margin-bottom: 1;
    }

    #text-editor-buttons {
        height: 3;
        align-horizontal: right;
    }

    #text-editor-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, text: str, *, title: str) -> None:
        """Create the editor over *text* with *title* on the dialog border."""
        super().__init__()
        self._text = text
        self._title = title

    def compose(self) -> ComposeResult:
        """Lay out the editable text area and the Cancel / Save buttons."""
        dialog = Vertical(id="text-editor-dialog")
        dialog.border_title = self._title
        with dialog:
            yield TextArea(self._text, id="text-editor-area")
            with Horizontal(id="text-editor-buttons"):
                yield Button("Cancel", id="text-editor-cancel", variant="default")
                yield Button("Save", id="text-editor-save", variant="primary")

    def on_mount(self) -> None:
        """Focus the text area so the operator can type immediately."""
        self.query_one("#text-editor-area", TextArea).focus()

    def action_cancel(self) -> None:
        """Escape — dismiss without saving."""
        self.dismiss(None)

    @on(Button.Pressed, "#text-editor-cancel")
    def _on_cancel(self) -> None:
        """Cancel button — dismiss without saving."""
        self.dismiss(None)

    @on(Button.Pressed, "#text-editor-save")
    def _on_save(self) -> None:
        """Save button — dismiss with the current text."""
        self.dismiss(self.query_one("#text-editor-area", TextArea).text)


class TextViewScreen(ModalScreen[None]):
    """Read-only modal text viewer — scrollable, selectable, no terminal."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    CSS = """
    TextViewScreen {
        align: center middle;
    }

    #text-view-dialog {
        width: 90%;
        height: 85%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #text-view-area {
        height: 1fr;
        margin-bottom: 1;
    }

    #text-view-buttons {
        height: 3;
        align-horizontal: right;
    }
    """

    def __init__(self, text: str, *, title: str) -> None:
        """Create the viewer over *text* with *title* on the dialog border."""
        super().__init__()
        self._text = text
        self._title = title

    def compose(self) -> ComposeResult:
        """Lay out the read-only text area and a Close button."""
        dialog = Vertical(id="text-view-dialog")
        dialog.border_title = self._title
        with dialog:
            yield TextArea(self._text, id="text-view-area", read_only=True)
            with Horizontal(id="text-view-buttons"):
                yield Button("Close", id="text-view-close", variant="default")

    @on(Button.Pressed, "#text-view-close")
    def _on_close(self) -> None:
        """Close button — dismiss the viewer."""
        self.dismiss()
