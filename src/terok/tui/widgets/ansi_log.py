# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""A ``RichLog`` for terminal-style output — ANSI colors on their own background.

Console panes render subprocess output (``podman build``, ``git``,
``terok setup``) whose ANSI color codes Textual translates to truecolor
through the app's *ANSI theme* (``App.ansi_theme``) — a palette
designed against that theme's own background, not against the Textual
theme's ``$surface`` that ``RichLog`` paints by default.  Nothing guarantees contrast between the two: ANSI
bright-black mapped through the default Monokai ANSI theme is a grey
that vanishes on a grey ``$surface`` — the invisible ``STEP 3/15``
build output.

[`AnsiLog`][terok.tui.widgets.ansi_log.AnsiLog] restores the invariant
*ANSI content is drawn on the background its palette was designed for*
by pinning its background to the ANSI theme's own background color.

When the app runs in native-ANSI mode (e.g. Textual's built-in
``ansi-dark`` theme) there is nothing to pin: ANSI codes pass through
to the terminal untranslated and the theme's ``ansi_default``
background shows the terminal's own — the terminal palette's contrast
contract applies, exactly as if the command ran outside the TUI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.color import Color
from textual.widgets import RichLog

if TYPE_CHECKING:
    from textual.theme import Theme


class AnsiLog(RichLog):
    """A ``RichLog`` whose background always matches the active ANSI palette."""

    def on_mount(self) -> None:
        """Pin the background now and re-pin whenever the app theme changes."""
        self._pin_background()
        self.app.theme_changed_signal.subscribe(self, self._on_theme_changed)

    def _on_theme_changed(self, _theme: Theme) -> None:
        """Re-pin after a theme switch — the ANSI theme or ANSI mode may have changed."""
        self._pin_background()

    def _pin_background(self) -> None:
        """Set the background to the ANSI theme's own; clear it in native-ANSI mode."""
        if self.app.native_ansi_color:
            self.styles.background = None
        else:
            self.styles.background = Color(*self.app.ansi_theme.background_color)
