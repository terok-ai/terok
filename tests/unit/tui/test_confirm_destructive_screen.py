# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the shared destructive-confirmation modal.

The regression these guard: a long message (e.g. a rekey stale-holder
listing with many entries) used to push the confirm/cancel buttons past
the dialog's ``max-height`` clip, leaving them unclickable.  The message
now lives in its own scroll box so the buttons stay on screen — and the
dismissal contract (confirm → ``True``, escape → ``False``) is pinned
alongside it.
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.containers import VerticalScroll
from textual.widgets import Button

from terok.tui.screens import ConfirmDestructiveScreen

_PENDING = object()


class _Host(App):
    """Minimal app that pushes a [`ConfirmDestructiveScreen`][terok.tui.screens.ConfirmDestructiveScreen]."""

    def __init__(self, message: str) -> None:
        """Stash the message and a pending-result sentinel."""
        super().__init__()
        self._message = message
        self.result: object = _PENDING

    def on_mount(self) -> None:
        """Push the modal and capture its dismissal value."""
        self.push_screen(
            ConfirmDestructiveScreen(self._message, title="T", confirm_label="Kill & continue"),
            self._capture,
        )

    def _capture(self, result: object) -> None:
        self.result = result


_HUGE_MESSAGE = "Stale holders:\n" + "\n".join(f"  • pid {p} · vault · task-{p}" for p in range(60))


class TestButtonsStayReachable:
    """A message far taller than the dialog must not clip the buttons away."""

    @pytest.mark.asyncio
    async def test_message_is_scrollable_and_buttons_visible(self) -> None:
        """The message sits in a VerticalScroll; both buttons render on screen."""
        app = _Host(_HUGE_MESSAGE)
        async with app.run_test(size=(90, 24)) as pilot:
            await pilot.pause()
            scroll = app.screen.query_one("#confirm-scroll", VerticalScroll)
            # The long message is inside the scroll box, not a bare Static
            # that would grow past the dialog and shove the buttons out.
            assert scroll.query_one("#confirm-message") is not None
            buttons = app.screen.query(Button)
            assert {b.id for b in buttons} == {"btn-cancel", "btn-confirm"}
            for button in buttons:
                assert button.region.height > 0, f"{button.id} is clipped to zero height"
                assert button.region.bottom <= app.size.height, f"{button.id} is below the screen"

    @pytest.mark.asyncio
    async def test_confirm_button_dismisses_true(self) -> None:
        """Clicking the confirm button returns ``True`` even with a huge message."""
        app = _Host(_HUGE_MESSAGE)
        async with app.run_test(size=(90, 24)) as pilot:
            await pilot.pause()
            await pilot.click("#btn-confirm")
            await pilot.pause()
        assert app.result is True

    @pytest.mark.asyncio
    async def test_escape_dismisses_false(self) -> None:
        """Escape cancels the destructive action."""
        app = _Host(_HUGE_MESSAGE)
        async with app.run_test(size=(90, 24)) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
        assert app.result is False
