# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the native text editor / viewer modals.

[`TextEditorScreen`][terok.tui.text_screens.TextEditorScreen] and
[`TextViewScreen`][terok.tui.text_screens.TextViewScreen] replace the
``$EDITOR``-subprocess and suspended-terminal print workflows — these
drive them through ``run_test`` to confirm the dismissal contract.
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import TextArea

from terok.tui.text_screens import TextEditorScreen, TextViewScreen

_SENTINEL_PENDING = object()


class _EditorHost(App):
    """Minimal app that pushes a [`TextEditorScreen`][terok.tui.text_screens.TextEditorScreen]."""

    def __init__(self, text: str) -> None:
        """Stash the initial text and a pending-result sentinel."""
        super().__init__()
        self._text = text
        self.result: object = _SENTINEL_PENDING

    def on_mount(self) -> None:
        """Push the editor and capture its dismissal."""
        self.push_screen(TextEditorScreen(self._text, title="Edit"), self._capture)

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
    assert app.result == "edited content"


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
