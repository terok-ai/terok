# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the native text editor / viewer modals.

[`TextEditorScreen`][terok.tui.text_screens.TextEditorScreen] and
[`TextViewScreen`][terok.tui.text_screens.TextViewScreen] replace the
``$EDITOR``-subprocess and suspended-terminal print workflows — these
drive them through ``run_test`` to confirm the dismissal contract.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest
from textual.app import App
from textual.widgets import Button, Checkbox, TextArea

from terok.tui.text_screens import (
    MARKDOWN,
    ConfigReferenceScreen,
    EditedText,
    TextEditorScreen,
    TextViewScreen,
    ValidationErrorLine,
    external_editor_label,
)
from tests.testfs import MOCK_BASE

_SENTINEL_PENDING = object()


class _EditorHost(App):
    """Minimal app that pushes a [`TextEditorScreen`][terok.tui.text_screens.TextEditorScreen]."""

    def __init__(
        self,
        text: str,
        *,
        language: str | None = None,
        validate: Callable[[str], str | None] | None = None,
        reference: Callable[[], str] | None = None,
        edit_externally: Callable[[str], Awaitable[str | None]] | None = None,
        checkbox_label: str | None = None,
        checked: bool = False,
    ) -> None:
        """Stash the initial text, the editor options and a pending-result sentinel."""
        super().__init__()
        self._text = text
        self._options = {
            "language": language,
            "validate": validate,
            "reference": reference,
            "edit_externally": edit_externally,
            "checkbox_label": checkbox_label,
            "checked": checked,
        }
        self.result: object = _SENTINEL_PENDING

    def on_mount(self) -> None:
        """Push the editor and capture its dismissal."""
        self.push_screen(TextEditorScreen(self._text, title="Edit", **self._options), self._capture)

    def _capture(self, result: object) -> None:
        self.result = result


class _ViewerHost(App):
    """Minimal app that pushes a [`TextViewScreen`][terok.tui.text_screens.TextViewScreen]."""

    def __init__(self, text: str) -> None:
        """Stash the text to view."""
        super().__init__()
        self._text = text

    def on_mount(self) -> None:
        """Push the read-only viewer."""
        self.push_screen(TextViewScreen(self._text, title="View"))


# ── TextEditorScreen ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_editor_loads_initial_text() -> None:
    """The editor's TextArea is seeded with the supplied text."""
    app = _EditorHost("hello world")
    async with app.run_test() as pilot:
        area = pilot.app.screen.query_one("#text-editor-area", TextArea)
        assert area.text == "hello world"


@pytest.mark.asyncio
async def test_editor_save_dismisses_with_current_text() -> None:
    """Save dismisses with whatever the TextArea currently holds."""
    app = _EditorHost("original")
    async with app.run_test() as pilot:
        area = pilot.app.screen.query_one("#text-editor-area", TextArea)
        area.text = "edited content"
        await pilot.click("#text-editor-save")
        await pilot.pause()
    assert app.result == EditedText("edited content")


@pytest.mark.asyncio
async def test_editor_cancel_dismisses_with_none() -> None:
    """Cancel discards edits — the caller gets None and must not persist."""
    app = _EditorHost("original")
    async with app.run_test() as pilot:
        pilot.app.screen.query_one("#text-editor-area", TextArea).text = "edited but discarded"
        await pilot.click("#text-editor-cancel")
        await pilot.pause()
    assert app.result is None


@pytest.mark.asyncio
async def test_editor_escape_dismisses_with_none() -> None:
    """Escape mirrors Cancel."""
    app = _EditorHost("original")
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
    assert app.result is None


@pytest.mark.asyncio
async def test_editor_ctrl_s_saves() -> None:
    """Ctrl+S is the keyboard Save, also while typing in the text area."""
    app = _EditorHost("original")
    async with app.run_test() as pilot:
        pilot.app.screen.query_one("#text-editor-area", TextArea).text = "edited"
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert app.result == EditedText("edited")


@pytest.mark.asyncio
async def test_editor_highlights_yaml() -> None:
    """A language turns the text area into the shared syntax-highlighting setup."""
    app = _EditorHost("key: value\n", language="yaml")
    async with app.run_test() as pilot:
        area = pilot.app.screen.query_one("#text-editor-area", TextArea)
        assert area.is_syntax_aware
        assert area.theme == "vscode_dark"
        assert area.tab_behavior == "focus"
        assert area.show_line_numbers


@pytest.mark.asyncio
async def test_editor_plain_text_by_default() -> None:
    app = _EditorHost("# Instructions\n")
    async with app.run_test() as pilot:
        assert not pilot.app.screen.query_one("#text-editor-area", TextArea).is_syntax_aware


def _refuse(text: str) -> str | None:
    """A validator that refuses any text containing ``bad``."""
    return "[bold]bad[/] is not allowed" if "bad" in text else None


@pytest.mark.asyncio
async def test_editor_refused_save_stays_open_and_says_why() -> None:
    """A refused Save keeps the editor open, with the reason shown as plain text."""
    app = _EditorHost("good", validate=_refuse)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        error_line = screen.query_one(ValidationErrorLine)
        assert not error_line.display
        screen.query_one("#text-editor-area", TextArea).text = "bad"
        await pilot.click("#text-editor-save")
        await pilot.pause()
        assert app.result is _SENTINEL_PENDING
        assert error_line.display
        assert str(error_line.content) == "[bold]bad[/] is not allowed"

        screen.query_one("#text-editor-area", TextArea).text = "fixed"
        # A second click within the button's press effect is debounced; use the key.
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert app.result == EditedText("fixed")


@pytest.mark.asyncio
async def test_editor_shows_the_problem_of_an_invalid_text_on_open() -> None:
    """A text that is already invalid — a broken project.yml — shows why at once."""
    app = _EditorHost("bad", validate=_refuse)
    async with app.run_test() as pilot:
        assert pilot.app.screen.query_one(ValidationErrorLine).display


# ── TextViewScreen ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_viewer_shows_text_read_only() -> None:
    """The viewer displays the text in a read-only TextArea."""
    app = _ViewerHost("read me")
    async with app.run_test() as pilot:
        area = pilot.app.screen.query_one("#text-view-area", TextArea)
        assert area.text == "read me"
        assert area.read_only is True


@pytest.mark.asyncio
async def test_viewer_close_button_dismisses() -> None:
    """The Close button dismisses the viewer."""
    app = _ViewerHost("content")
    async with app.run_test() as pilot:
        assert isinstance(pilot.app.screen, TextViewScreen)
        await pilot.click("#text-view-close")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, TextViewScreen)


@pytest.mark.asyncio
async def test_viewer_escape_dismisses() -> None:
    """Escape dismisses the viewer."""
    app = _ViewerHost("content")
    async with app.run_test() as pilot:
        assert isinstance(pilot.app.screen, TextViewScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, TextViewScreen)


# ── Markdown, the options reference and the checkbox ────────────────


@pytest.mark.asyncio
async def test_markdown_editor_highlights_and_wraps() -> None:
    """Instructions are prose: Markdown highlighting, and lines wrapped at the edge."""
    app = _EditorHost("# Title", language=MARKDOWN)
    async with app.run_test() as pilot:
        area = pilot.app.screen.query_one("#text-editor-area", TextArea)
        assert area.language == MARKDOWN
        assert area.soft_wrap is True


@pytest.mark.asyncio
async def test_code_editor_keeps_its_lines() -> None:
    """Code stays unwrapped, so its line structure stays visible."""
    app = _EditorHost("key: value", language="yaml")
    async with app.run_test() as pilot:
        assert pilot.app.screen.query_one("#text-editor-area", TextArea).soft_wrap is False


@pytest.mark.asyncio
async def test_markdown_viewer_highlights_and_stays_read_only() -> None:
    class _MarkdownViewerHost(App):
        def on_mount(self) -> None:
            self.push_screen(TextViewScreen("# Title", title="View", language=MARKDOWN))

    async with _MarkdownViewerHost().run_test() as pilot:
        area = pilot.app.screen.query_one("#text-view-area", TextArea)
        assert area.language == MARKDOWN
        assert area.read_only is True


@pytest.mark.asyncio
async def test_options_button_opens_the_reference_over_the_editor() -> None:
    app = _EditorHost("key: value", reference=lambda: "## Section\n\nText.")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#text-editor-options")
        await pilot.pause()
        assert isinstance(pilot.app.screen, ConfigReferenceScreen)
        await pilot.click("#config-reference-close")
        await pilot.pause()
        assert isinstance(pilot.app.screen, TextEditorScreen)


@pytest.mark.asyncio
async def test_f1_opens_the_reference_and_esc_keeps_the_edit() -> None:
    """Reading the options costs nothing: Esc lands back in the editor, with the edit intact."""
    app = _EditorHost("key: value", reference=lambda: "## Section")
    async with app.run_test(size=(120, 40)) as pilot:
        pilot.app.screen.query_one("#text-editor-area", TextArea).text = "edited"
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(pilot.app.screen, ConfigReferenceScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(pilot.app.screen, TextEditorScreen)
        assert pilot.app.screen.query_one("#text-editor-area", TextArea).text == "edited"


@pytest.mark.asyncio
async def test_editor_without_a_reference_has_no_options() -> None:
    app = _EditorHost("text")
    async with app.run_test() as pilot:
        assert not pilot.app.screen.query("#text-editor-options")
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(pilot.app.screen, TextEditorScreen)


@pytest.mark.asyncio
async def test_checkbox_comes_back_with_the_text() -> None:
    app = _EditorHost("text", checkbox_label="Include", checked=True)
    async with app.run_test() as pilot:
        pilot.app.screen.query_one("#text-editor-checkbox", Checkbox).value = False
        await pilot.click("#text-editor-save")
        await pilot.pause()
    assert app.result == EditedText("text", checked=False)


@pytest.mark.asyncio
async def test_cancel_drops_the_checkbox_with_the_text() -> None:
    app = _EditorHost("text", checkbox_label="Include", checked=True)
    async with app.run_test() as pilot:
        pilot.app.screen.query_one("#text-editor-checkbox", Checkbox).value = False
        await pilot.click("#text-editor-cancel")
        await pilot.pause()
    assert app.result is None


# ── The $EDITOR button ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("editor", "expected"),
    [
        ("vim", "Edit in vim"),
        (f"{MOCK_BASE}/bin/vim --clean", "Edit in vim"),
        (f'"{MOCK_BASE}/My Editor/edit" --wait', "Edit in edit"),
        ("code --wait", "Edit in code"),
        ("sensible-editor", "Edit in sensible-editor"),
        ("exactly-16-chars", "Edit in exactly-16-chars"),
        ("seventeen-letters", "Edit in seventeen-lette…"),
        ("[bold]", "Edit in [bold]"),
        ('vim "unbalanced', "Edit in $EDITOR"),
        ("/", "Edit in $EDITOR"),
    ],
    ids=[
        "name",
        "path-and-option",
        "quoted-path",
        "gui-wait",
        "sensible-editor",
        "at-the-cap",
        "over-the-cap",
        "looks-like-markup",
        "does-not-parse",
        "no-file-name",
    ],
)
def test_label_names_the_editor(
    monkeypatch: pytest.MonkeyPatch, editor: str, expected: str
) -> None:
    monkeypatch.setenv("EDITOR", editor)
    assert external_editor_label().plain == expected


@pytest.mark.asyncio
async def test_button_names_the_editor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDITOR", f"{MOCK_BASE}/bin/vim --clean")
    app = _EditorHost("text", edit_externally=AsyncMock(return_value=None))
    async with app.run_test(size=(120, 40)) as pilot:
        button = pilot.app.screen.query_one("#text-editor-external", Button)
        assert str(button.label) == "Edit in vim"


@pytest.mark.asyncio
async def test_no_round_trip_means_no_button() -> None:
    app = _EditorHost("text")
    async with app.run_test() as pilot:
        assert not pilot.app.screen.query("#text-editor-external")


@pytest.mark.asyncio
async def test_accepted_text_comes_back_to_the_editor_unsaved() -> None:
    """The round-trip fills the editor; only Save hands the text back."""
    edit = AsyncMock(return_value="from vim")
    app = _EditorHost("text", edit_externally=edit)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#text-editor-external")
        await pilot.pause()
        edit.assert_awaited_once_with("text")
        assert pilot.app.screen.query_one("#text-editor-area", TextArea).text == "from vim"
        assert app.result is _SENTINEL_PENDING
        await pilot.click("#text-editor-save")
        await pilot.pause()
    assert app.result == EditedText("from vim")


@pytest.mark.asyncio
async def test_abandoned_round_trip_keeps_the_text() -> None:
    app = _EditorHost("text", edit_externally=AsyncMock(return_value=None))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#text-editor-external")
        await pilot.pause()
        assert pilot.app.screen.query_one("#text-editor-area", TextArea).text == "text"


@pytest.mark.asyncio
async def test_round_trip_clears_a_problem_it_fixed() -> None:
    def validate(text: str) -> str | None:
        return None if text == "good" else "bad text"

    app = _EditorHost("broken", validate=validate, edit_externally=AsyncMock(return_value="good"))
    async with app.run_test(size=(120, 40)) as pilot:
        line = pilot.app.screen.query_one(ValidationErrorLine)
        assert line.display is True
        await pilot.click("#text-editor-external")
        await pilot.pause()
        assert line.display is False


@pytest.mark.asyncio
async def test_failed_round_trip_keeps_the_text_and_the_editor_open() -> None:
    app = _EditorHost("text", edit_externally=AsyncMock(side_effect=OSError("no such editor")))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#text-editor-external")
        await pilot.pause()
        assert isinstance(pilot.app.screen, TextEditorScreen)
        assert pilot.app.screen.query_one("#text-editor-area", TextArea).text == "text"
