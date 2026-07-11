# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for [`AnsiLog`][terok.tui.widgets.ansi_log.AnsiLog].

The widget's contract is a single invariant: *ANSI content is drawn on
the background its palette was designed for*.  Concretely —

* in truecolor themes the pane's background is pinned to
  ``app.ansi_theme.background_color`` (the palette Textual maps ANSI
  codes through), never the Textual theme's ``$surface``;
* a theme switch re-pins, so the pairing survives dark/light flips;
* in native-ANSI mode (Textual's ``ansi-dark`` theme) nothing is
  pinned — the terminal's own default background shows through and the
  terminal palette's contrast contract applies.

``TerokTUI`` additionally pairs light themes with the *dark* ANSI
palette (console output is authored for dark terminals), which is
asserted against the real app class.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.color import Color

from terok.tui.widgets.ansi_log import AnsiLog

_PANE_ID = "ansi-log-pane"


class _Host(App):
    """Minimal app hosting a single [`AnsiLog`][terok.tui.widgets.ansi_log.AnsiLog]."""

    def compose(self) -> ComposeResult:
        """Mount the pane under test."""
        yield AnsiLog(id=_PANE_ID)


def _ansi_background(app: App) -> Color:
    """The background color of *app*'s active ANSI palette."""
    return Color(*app.ansi_theme.background_color)


@pytest.mark.asyncio
async def test_background_is_the_ansi_palette_background() -> None:
    """On mount the pane's background is the ANSI theme's own, not ``$surface``."""
    app = _Host()
    async with app.run_test() as pilot:
        pane = pilot.app.query_one(f"#{_PANE_ID}", AnsiLog)
        assert pane.styles.background == _ansi_background(pilot.app)


@pytest.mark.asyncio
async def test_theme_switch_repins_to_the_new_ansi_palette() -> None:
    """Switching themes re-pins the background to the now-active ANSI palette."""
    app = _Host()
    async with app.run_test() as pilot:
        pane = pilot.app.query_one(f"#{_PANE_ID}", AnsiLog)
        before = pane.styles.background

        pilot.app.theme = "textual-light"
        await pilot.pause()

        after = pane.styles.background
        assert after == _ansi_background(pilot.app)
        assert after != before, "light theme selects a different ANSI palette"


@pytest.mark.asyncio
async def test_native_ansi_theme_leaves_the_terminal_background() -> None:
    """Under the built-in ``ansi-dark`` theme the pane paints no background of its own."""
    app = _Host()
    async with app.run_test() as pilot:
        pane = pilot.app.query_one(f"#{_PANE_ID}", AnsiLog)

        pilot.app.theme = "ansi-dark"
        await pilot.pause()

        assert pilot.app.native_ansi_color, "ansi-dark is a native-ANSI theme"
        # The inline pin is cleared, so the theme's transparent surface
        # applies and the *effective* background is ``ansi_default`` —
        # the terminal's own default background.
        assert not pane._inline_styles.has_rule("background")
        assert pane.background_colors[0].ansi == -1


def test_terok_tui_pairs_light_themes_with_the_dark_ansi_palette() -> None:
    """``TerokTUI`` keeps the dark ANSI palette in light themes too.

    Console output is authored for dark terminals; pairing light app
    themes with a near-white ANSI palette would re-break truecolor
    greys that Rich-based children emit assuming a dark background.
    """
    from terok.tui.app import TerokTUI

    app = TerokTUI()
    assert app.ansi_theme_light is app.ansi_theme_dark
