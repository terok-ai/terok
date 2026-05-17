# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for [`WorkerLogScreen`][terok.tui.worker_log_screen.WorkerLogScreen].

``WorkerLogScreen`` is a pure *view* over a
[`ConsoleLogEntry`][terok.tui.console_log.ConsoleLogEntry] — it owns no
subprocess.  These tests hand-construct entries and drive their
``append`` / ``finish`` directly (standing in for the registry pump),
so the screen's replay / live-tail / button behaviour is exercised
without spawning processes.  The pump itself is covered in
``test_console_log.py``.
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Button, RichLog

from terok.tui.console_log import ConsoleLogEntry
from terok.tui.worker_log_screen import WorkerLogScreen

_SENTINEL_PENDING = object()


def _entry(*, lines: list[str] | None = None, finished: int | None = None) -> ConsoleLogEntry:
    """Build an entry, optionally pre-seeded with *lines* and a *finished* exit code."""
    entry = ConsoleLogEntry(id=1, title="Building images for proj", argv=["x"], command="terok x")
    for line in lines or []:
        entry.append(line)
    if finished is not None:
        entry.finish(finished)
    return entry


class _ViewerHost(App):
    """Minimal app that pushes a [`WorkerLogScreen`][terok.tui.worker_log_screen.WorkerLogScreen] over an entry."""

    def __init__(self, entry: ConsoleLogEntry) -> None:
        """Stash the entry to view and a pending-result sentinel."""
        super().__init__()
        self._entry = entry
        self.result: object = _SENTINEL_PENDING

    def on_mount(self) -> None:
        """Push the viewer and capture its dismissal."""
        self.push_screen(WorkerLogScreen(self._entry), self._capture)

    def _capture(self, result: object) -> None:
        self.result = result


def _rendered(screen: WorkerLogScreen) -> str:
    """Join the screen's RichLog visible text for substring assertions.

    Uses ``Strip.text`` (the concatenated visible characters across the
    line's segments) rather than ``str(strip)`` (which returns a
    debug repr) so substring checks survive ANSI-induced segmentation
    — ``hello world`` written from ``\\x1b[31mhello\\x1b[0m world``
    is two segments, and the repr would split the words across them.
    """
    log = screen.query_one("#worker-log-output", RichLog)
    return "\n".join(strip.text for strip in log.lines)


# ── Finished entries ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_finished_ok_entry_replays_lines_and_shows_done() -> None:
    """A viewer over a clean finished entry replays its output and shows a green Done."""
    entry = _entry(lines=["compiled step 1", "compiled step 2"], finished=0)
    app = _ViewerHost(entry)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, WorkerLogScreen)
        rendered = _rendered(screen)
        assert "compiled step 1" in rendered and "compiled step 2" in rendered
        button = screen.query_one("#worker-log-close", Button)
        assert str(button.label) == "Done" and button.variant == "success"
        await pilot.click("#worker-log-close")
        await pilot.pause()
    assert app.result is None, "the viewer dismisses with None — the entry holds the outcome"


@pytest.mark.asyncio
async def test_finished_failed_entry_shows_close_warning() -> None:
    """A viewer over a failed finished entry shows a warning-styled Close."""
    entry = _entry(lines=["boom"], finished=3)
    app = _ViewerHost(entry)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, WorkerLogScreen)
        button = screen.query_one("#worker-log-close", Button)
        assert str(button.label) == "Close" and button.variant == "warning"


# ── Running entries ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_running_entry_shows_hide_and_live_tails() -> None:
    """A viewer over a running entry shows Hide and tails lines appended after mount."""
    entry = _entry(lines=["initial line"])
    app = _ViewerHost(entry)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, WorkerLogScreen)
        button = screen.query_one("#worker-log-close", Button)
        assert str(button.label) == "Hide"
        assert "initial line" in _rendered(screen)

        # Stand in for the registry pump appending more output live.
        entry.append("streamed after mount")
        await pilot.pause()
        assert "streamed after mount" in _rendered(screen)


@pytest.mark.asyncio
async def test_entry_finishing_while_open_relabels_button() -> None:
    """When the entry finishes while the viewer is open, Hide becomes Done."""
    entry = _entry()
    app = _ViewerHost(entry)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, WorkerLogScreen)
        assert str(screen.query_one("#worker-log-close", Button).label) == "Hide"

        entry.finish(0)
        await pilot.pause()
        button = screen.query_one("#worker-log-close", Button)
        assert str(button.label) == "Done" and button.variant == "success"


@pytest.mark.asyncio
async def test_hiding_a_running_entry_dismisses_without_killing_it() -> None:
    """Hide dismisses the viewer but leaves the entry running — backgrounding it."""
    entry = _entry(lines=["in progress"])
    app = _ViewerHost(entry)
    async with app.run_test() as pilot:
        assert isinstance(pilot.app.screen, WorkerLogScreen)
        await pilot.click("#worker-log-close")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, WorkerLogScreen), "viewer dismissed"
    assert entry.running, "hiding the viewer must not finish the entry"


@pytest.mark.asyncio
async def test_escape_dismisses_running_viewer() -> None:
    """Escape mirrors Hide — safe at any time since dismissing never kills the action."""
    entry = _entry(lines=["working"])
    app = _ViewerHost(entry)
    async with app.run_test() as pilot:
        assert isinstance(pilot.app.screen, WorkerLogScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, WorkerLogScreen)
    assert entry.running, "escape backgrounds the entry, it keeps running"


# ── ANSI rendering + initial focus ────────────────────────────────────


@pytest.mark.asyncio
async def test_ansi_escapes_render_as_styled_text_not_raw_sequences() -> None:
    """Lines carrying ANSI colour escapes (``terok setup``, git, podman build…)
    render as styled Rich [`Text`][rich.text.Text], not as printed escape
    sequences.  The substring assertion checks the visible text comes through
    *without* the ``\\x1b[…m`` framing — the styling itself is in the line's
    spans, but we don't pin those here (Textual's RichLog handles the
    pixel-level rendering)."""
    # ``\x1b[31mhello\x1b[0m world`` → "hello world" visible, "31m" gone.
    entry = _entry(lines=["\x1b[31mhello\x1b[0m world"], finished=0)
    app = _ViewerHost(entry)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, WorkerLogScreen)
        rendered = _rendered(screen)
        assert "hello world" in rendered
        # Raw escape sequence and ANSI prefix must NOT bleed through.
        assert "\x1b" not in rendered
        assert "[31m" not in rendered


@pytest.mark.asyncio
async def test_dismiss_button_is_focused_on_mount() -> None:
    """The viewer is read-only — there's no other useful focus target —
    so the dismiss button gets focus on mount so Enter dismisses
    immediately, no Tab dance required."""
    entry = _entry(lines=["any"], finished=0)
    app = _ViewerHost(entry)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, WorkerLogScreen)
        button = screen.query_one("#worker-log-close", Button)
        assert screen.focused is button
        # And: pressing Enter on that focus dismisses the screen.
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, WorkerLogScreen)
