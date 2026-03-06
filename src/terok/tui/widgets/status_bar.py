# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Status bar widget for the TUI footer."""

from typing import Any

from rich.text import Text
from textual.widgets import Static


class StatusBar(Static):
    """Bottom status bar showing minimal key hints plus status text.

    This replaces Textual's default Footer so we can free horizontal space
    for real status messages instead of a long list of shortcuts. The
    shortcut hints are kept very small here because the primary shortcut
    hints already live in the ProjectActions button bar.
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the status bar with an empty message."""
        super().__init__(**kwargs)
        # Initialize with an empty message; the App will populate this.
        self.message: str = ""
        self._update_content()

    def set_message(self, message: str) -> None:
        """Update the status message area.

        The left side of the bar is reserved for a couple of always-on
        shortcut hints ("q Quit" and "^P Palette"); the rest of the line is
        dedicated to this message text.
        """

        self.message = message
        self._update_content()

    def _update_content(self) -> None:
        """Re-render the status bar text from the current message."""
        self.update(Text(self.message or ""))
