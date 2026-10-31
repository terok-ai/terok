# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Native text editor / viewer modals.

The in-process editing surface for text the TUI edits — the only one
under the web TUI, where no terminal exists for ``$EDITOR`` to take
over (issue #473).  Both are plain in-process modals — no child
process, no terminal:

* [`TextEditorScreen`][terok.tui.text_screens.TextEditorScreen] — an
  editable ``TextArea``; dismisses with an
  [`EditedText`][terok.tui.text_screens.EditedText] on Save, ``None`` on
  Cancel.  An optional validator refuses a Save and shows why; an
  optional language turns on syntax highlighting.  A config file can add
  an options reference, and a text a checkbox saved along with it.
* [`TextViewScreen`][terok.tui.text_screens.TextViewScreen] — the same
  widget in read-only mode for display-only content.
* [`ConfigReferenceScreen`][terok.tui.text_screens.ConfigReferenceScreen]
  — the options of a config file, for reading while editing it.
* [`code_text_area`][terok.tui.text_screens.code_text_area],
  [`ValidationErrorLine`][terok.tui.text_screens.ValidationErrorLine],
  [`edit_externally_into`][terok.tui.text_screens.edit_externally_into] and
  [`external_editor_label`][terok.tui.text_screens.external_editor_label] —
  the syntax-highlighting ``TextArea`` setup, the refused-save line, and
  the ``$EDITOR`` round-trip for a text area and its button label, shared
  across the TUI's text editors.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, MarkdownViewer, Static, TextArea

MARKDOWN = "markdown"
"""The language of agent instructions."""

_SECTION_LIST_MIN_WIDTH = 100
"""Below this terminal width the options reference gives all its room to the text."""

_EDITOR_NAME_MAX = 16
"""The longest editor name a button shows, so that the button stays narrow."""


def code_text_area(
    text: str, *, language: str, widget_id: str, read_only: bool = False
) -> TextArea:
    """Return a syntax-highlighting ``TextArea`` over *text*, set up one way everywhere.

    Line numbers and the dark highlight theme come with ``code_editor``.
    Tab moves the focus instead of indenting, so Tab reaches the dialog
    buttons and the first Esc reaches the dialog's cancel binding.
    Markdown is prose, so its lines wrap; code keeps its own lines.
    """
    return TextArea.code_editor(
        text,
        language=language,
        theme="vscode_dark",
        tab_behavior="focus",
        soft_wrap=language == MARKDOWN,
        read_only=read_only,
        id=widget_id,
    )


def _text_area(
    text: str, *, language: str | None, widget_id: str, read_only: bool = False
) -> TextArea:
    """Return a highlighting ``TextArea`` for a *language*, and a plain one without."""
    if language is None:
        return TextArea(text, id=widget_id, read_only=read_only)
    return code_text_area(text, language=language, widget_id=widget_id, read_only=read_only)


class ValidationErrorLine(Static):
    """The line under an editor that says why its text was refused, hidden when there is none.

    *validate* returns why a text must not be saved, or ``None``; without
    one, every text passes.
    """

    DEFAULT_CSS = """
    ValidationErrorLine {
        color: $error;
        height: auto;
        max-height: 8;
        overflow-y: auto;
        margin-bottom: 1;
        display: none;
    }
    """

    def __init__(self, validate: Callable[[str], str | None] | None = None) -> None:
        """Create the line empty and hidden."""
        super().__init__("", markup=False)
        self._validate = validate

    def check(self, text: str) -> bool:
        """Show the verdict on *text*, or hide the line when it passes; return whether it passed."""
        verdict = self._validate(text) if self._validate else None
        self.update(verdict or "")
        self.display = verdict is not None
        return verdict is None


def external_editor_label() -> Content:
    """Return the label of the button that opens a text in ``$EDITOR``, naming the editor.

    Best effort: the file name of the command, so ``/usr/bin/vim --clean``
    reads "Edit in vim".  A wrapper shows as itself, and a command that
    does not parse keeps the literal ``$EDITOR``.  A long name is cut.
    The label is plain text, so a name can not be taken for markup.
    """
    try:
        command = shlex.split(os.environ.get("EDITOR", ""))
    except ValueError:
        command = []
    name = Path(command[0]).name if command else ""
    if len(name) > _EDITOR_NAME_MAX:
        name = f"{name[: _EDITOR_NAME_MAX - 1]}…"
    return Content(f"Edit in {name or '$EDITOR'}")


async def edit_externally_into(
    area: TextArea, edit_externally: Callable[[str], Awaitable[str | None]], *, title: str
) -> bool:
    """Send *area*'s text through ``$EDITOR``; an accepted text replaces it.

    *edit_externally* returns the accepted text, or ``None`` when the
    operator abandons the edit.  A failure is reported under *title*,
    never raised.  Returns whether the text was replaced.
    """
    try:
        edited = await edit_externally(area.text)
    except Exception as exc:  # noqa: BLE001 - an edit must never fail silently
        area.notify(f"Editing {title} failed: {exc}", severity="error", markup=False)
        return False
    if edited is None:
        return False
    area.load_text(edited)
    return True


class ConfigReferenceScreen(ModalScreen[None]):
    """The options of a config file, with a section list, to read while editing the file."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    CSS = """
    ConfigReferenceScreen {
        align: center middle;
    }

    #config-reference-dialog {
        width: 90%;
        height: 85%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #config-reference-view {
        height: 1fr;
        margin-bottom: 1;
    }

    #config-reference-buttons {
        height: 3;
        align-horizontal: right;
    }
    """

    def __init__(self, markdown: str, *, title: str) -> None:
        """Create the reference over *markdown* with *title* on the dialog border."""
        super().__init__()
        self._markdown = markdown
        self._title = title

    def compose(self) -> ComposeResult:
        """Lay out the section list and the rendered options, and a Close button."""
        dialog = Vertical(id="config-reference-dialog")
        dialog.border_title = self._title
        with dialog:
            yield MarkdownViewer(
                self._markdown,
                show_table_of_contents=self.app.size.width >= _SECTION_LIST_MIN_WIDTH,
                open_links=False,
                id="config-reference-view",
            )
            with Horizontal(id="config-reference-buttons"):
                yield Button("Close", id="config-reference-close", variant="default")

    @on(Button.Pressed, "#config-reference-close")
    def _on_close(self) -> None:
        """Close button — back to the editor."""
        self.dismiss()


@dataclass(frozen=True)
class EditedText:
    """What the integrated editor hands back on Save."""

    text: str
    checked: bool | None = None
    """The editor's checkbox, or ``None`` when it has none."""


class TextEditorScreen(ModalScreen[EditedText | None]):
    """Modal ``TextArea`` editor.

    Dismisses with the (possibly edited) text on Save, or ``None`` on
    Cancel / Escape — the caller decides whether to persist.  A
    *validate* callback returns why a text must not be saved, or
    ``None``: a refused Save keeps the editor open with the reason under
    the text.  The check also runs when the editor opens, so a text that
    is already invalid shows its problem at once.

    *reference* returns the Markdown options reference of a config file;
    with one, the Options button and F1 open it over the editor.
    *edit_externally* sends the text through ``$EDITOR`` and returns the
    accepted text, or ``None`` when the operator abandons the edit; with
    one, a button naming the editor offers that round-trip.  An accepted
    text comes back here, and Save writes it.  *checkbox_label* adds a
    checkbox over the buttons, starting as *checked*, for a setting that
    is saved along with the text.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
        Binding("f1", "show_options", "Options"),
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
        min-width: 10;
    }

    #text-editor-checkbox {
        margin-bottom: 1;
    }
    """

    def __init__(
        self,
        text: str,
        *,
        title: str,
        language: str | None = None,
        validate: Callable[[str], str | None] | None = None,
        reference: Callable[[], str] | None = None,
        edit_externally: Callable[[str], Awaitable[str | None]] | None = None,
        checkbox_label: str | None = None,
        checked: bool = False,
    ) -> None:
        """Create the editor over *text* with *title* on the dialog border."""
        super().__init__()
        self._text = text
        self._title = title
        self._language = language
        self._validate = validate
        self._reference = reference
        self._edit_externally = edit_externally
        self._checkbox_label = checkbox_label
        self._checked = checked

    def compose(self) -> ComposeResult:
        """Lay out the editable text area, the error line, the checkbox and the buttons."""
        dialog = Vertical(id="text-editor-dialog")
        dialog.border_title = self._title
        options_hint = " · F1 options" if self._reference else ""
        dialog.border_subtitle = f"Ctrl+S save{options_hint} · Esc cancel"
        with dialog:
            yield _text_area(self._text, language=self._language, widget_id="text-editor-area")
            yield ValidationErrorLine(self._validate)
            if self._checkbox_label is not None:
                yield Checkbox(
                    self._checkbox_label,
                    self._checked,
                    compact=True,
                    id="text-editor-checkbox",
                )
            with Horizontal(id="text-editor-buttons"):
                if self._reference is not None:
                    yield Button("Options", id="text-editor-options", variant="default")
                if self._edit_externally is not None:
                    yield Button(
                        external_editor_label(), id="text-editor-external", variant="default"
                    )
                yield Button("Cancel", id="text-editor-cancel", variant="default")
                yield Button("Save", id="text-editor-save", variant="primary")

    def on_mount(self) -> None:
        """Show any problem the text already has, and focus the text area for typing."""
        self.query_one(ValidationErrorLine).check(self._text)
        self.query_one("#text-editor-area", TextArea).focus()

    def action_cancel(self) -> None:
        """Escape — dismiss without saving."""
        self.dismiss(None)

    def action_save(self) -> None:
        """Ctrl+S / Save — dismiss with the current text, unless the validator refuses it."""
        text = self.query_one("#text-editor-area", TextArea).text
        if self.query_one(ValidationErrorLine).check(text):
            checkboxes = self.query("#text-editor-checkbox").results(Checkbox)
            self.dismiss(EditedText(text, next((box.value for box in checkboxes), None)))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """F1 exists only on an editor that has an options reference."""
        return self._reference is not None if action == "show_options" else True

    def action_show_options(self) -> None:
        """F1 / Options — open the options reference over the editor."""
        if self._reference is not None:
            show_config_reference(self.app, self._reference, title=self._title)

    @on(Button.Pressed, "#text-editor-options")
    def _on_options(self) -> None:
        """Options button — same as F1."""
        self.action_show_options()

    @on(Button.Pressed, "#text-editor-external")
    def _on_edit_externally(self) -> None:
        """Editor button — the round-trip runs as a worker, so the screen stays responsive."""
        self._run_external_edit()

    @work(exclusive=True, group="text-editor-external", exit_on_error=False)
    async def _run_external_edit(self) -> None:
        """Send the text through ``$EDITOR``; an accepted text replaces the editor's content."""
        if self._edit_externally is None:
            return
        area = self.query_one("#text-editor-area", TextArea)
        if await edit_externally_into(area, self._edit_externally, title=self._title):
            self.query_one(ValidationErrorLine).check(area.text)

    @on(Button.Pressed, "#text-editor-cancel")
    def _on_cancel(self) -> None:
        """Cancel button — dismiss without saving."""
        self.dismiss(None)

    @on(Button.Pressed, "#text-editor-save")
    def _on_save(self) -> None:
        """Save button — same as Ctrl+S."""
        self.action_save()


def show_config_reference(app: App, reference: Callable[[], str], *, title: str) -> None:
    """Open the options reference of the config file edited under *title*."""
    app.push_screen(ConfigReferenceScreen(reference(), title=f"Options — {title}"))


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

    def __init__(self, text: str, *, title: str, language: str | None = None) -> None:
        """Create the viewer over *text* with *title* on the dialog border."""
        super().__init__()
        self._text = text
        self._title = title
        self._language = language

    def compose(self) -> ComposeResult:
        """Lay out the read-only text area and a Close button."""
        dialog = Vertical(id="text-view-dialog")
        dialog.border_title = self._title
        with dialog:
            yield _text_area(
                self._text, language=self._language, widget_id="text-view-area", read_only=True
            )
            with Horizontal(id="text-view-buttons"):
                yield Button("Close", id="text-view-close", variant="default")

    @on(Button.Pressed, "#text-view-close")
    def _on_close(self) -> None:
        """Close button — dismiss the viewer."""
        self.dismiss()
