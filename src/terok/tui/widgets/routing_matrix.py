# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""A patchbay-style grid for wiring keys to scopes.

Rows are keys, columns are scopes, and every intersection is a
connection the operator can toggle — the same mental model as a MIDI
routing matrix or a studio patchbay.  A moving crosshair (the cursor's
whole row and column light up) keeps "which key, which scope" legible
even when the grid is sparse.

The widget is a pure view: it renders the routing it is handed and emits
an intent when the operator toggles a cell or asks to mint a key — it
never touches a vault or a backend itself.  That keeps it reusable by any
Textual app willing to supply
[`MatrixKey`][terok.tui.widgets.routing_matrix.MatrixKey] rows and persist
the [`CellToggled`][terok.tui.widgets.routing_matrix.RoutingMatrix.CellToggled]
intents it posts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static

_LINKED_GLYPH = "●"
_EMPTY_GLYPH = "·"
_CELL_WIDTH = 3
"""Width each connection cell is padded to."""

_LABEL_WIDTH = 24
"""Key-label column width.  Longer labels are truncated with an ellipsis
so the grid body starts at a fixed offset."""

_CROSSHAIR_STYLE = "on grey30"
_CURSOR_STYLE = "reverse bold"
_HEADER_STYLE = "bold"
_DIM_STYLE = "dim"


@dataclass(frozen=True)
class MatrixKey:
    """One key on the row axis: a stable id and the label to show for it."""

    key_id: int
    label: str


class RoutingMatrix(Static):
    """An interactive keys×scopes grid that emits wiring intents."""

    can_focus = True

    DEFAULT_CSS = """
    RoutingMatrix {
        height: auto;
        padding: 1 2;
    }
    RoutingMatrix:focus {
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("up,k", "move(-1, 0)", "Up", show=False),
        Binding("down,j", "move(1, 0)", "Down", show=False),
        Binding("left,h", "move(0, -1)", "Left", show=False),
        Binding("right,l", "move(0, 1)", "Right", show=False),
        Binding("space,enter", "toggle_cell", "Toggle link"),
        Binding("n", "mint", "Mint key for column"),
    ]

    class CellToggled(Message):
        """Posted when the operator toggles the cursor cell.

        ``linked`` is the cell's state *before* the toggle, so a handler
        knows whether it is wiring a new link or cutting an existing one.
        """

        def __init__(self, scope: str, key_id: int, linked: bool) -> None:
            super().__init__()
            self.scope = scope
            self.key_id = key_id
            self.linked = linked

    class MintRequested(Message):
        """Posted when the operator asks to mint a key for the cursor column."""

        def __init__(self, scope: str) -> None:
            super().__init__()
            self.scope = scope

    def __init__(self, **kwargs: Any) -> None:
        """Start empty; the screen feeds routing through ``set_routing``."""
        super().__init__(**kwargs)
        self._keys: list[MatrixKey] = []
        self._scopes: list[str] = []
        self._links: set[tuple[str, int]] = set()
        self._row = 0
        self._col = 0

    # ── Data feed ──────────────────────────────────────────────────────

    def set_routing(
        self, keys: list[MatrixKey], scopes: list[str], links: set[tuple[str, int]]
    ) -> None:
        """Replace the rendered routing and clamp the cursor into the new grid."""
        self._keys = keys
        self._scopes = scopes
        self._links = links
        self._row = _clamp(self._row, len(keys))
        self._col = _clamp(self._col, len(scopes))
        self.refresh()

    @property
    def cursor_key(self) -> MatrixKey | None:
        """The key under the cursor, or ``None`` when there are no rows."""
        return self._keys[self._row] if self._keys else None

    @property
    def cursor_scope(self) -> str | None:
        """The scope under the cursor, or ``None`` when there are no columns."""
        return self._scopes[self._col] if self._scopes else None

    # ── Navigation & intents ───────────────────────────────────────────

    def action_move(self, drow: int, dcol: int) -> None:
        """Step the cursor, clamped to the grid edges."""
        self._row = _clamp(self._row + drow, len(self._keys))
        self._col = _clamp(self._col + dcol, len(self._scopes))
        self.refresh()

    def action_toggle_cell(self) -> None:
        """Emit a toggle intent for the cursor cell."""
        key, scope = self.cursor_key, self.cursor_scope
        if key is None or scope is None:
            return
        self.post_message(self.CellToggled(scope, key.key_id, (scope, key.key_id) in self._links))

    def action_mint(self) -> None:
        """Emit a mint intent for the cursor column."""
        if (scope := self.cursor_scope) is not None:
            self.post_message(self.MintRequested(scope))

    # ── Rendering ──────────────────────────────────────────────────────

    def render(self) -> Text:
        """Assemble the legend, the numbered header, the key rows, and a status line."""
        if not self._scopes:
            return Text("No projects to route keys to.", style=_DIM_STYLE)
        out = Text()
        out.append_text(self._render_legend())
        out.append("\n")
        out.append_text(self._render_header())
        out.append("\n")
        if self._keys:
            for index, key in enumerate(self._keys):
                out.append_text(self._render_row(index, key))
                out.append("\n")
        else:
            out.append(_pad(" ", _LABEL_WIDTH))
            out.append("no keys yet — press n to mint one\n", style=_DIM_STYLE)
        out.append("\n")
        out.append_text(self._render_status())
        return out

    def _render_header(self) -> Text:
        """The numbered column header, with the cursor column highlighted."""
        line = Text(_pad(" ", _LABEL_WIDTH))
        for index in range(len(self._scopes)):
            style = _CROSSHAIR_STYLE if index == self._col else _HEADER_STYLE
            line.append(_pad(str(index + 1), _CELL_WIDTH), style=style)
        return line

    def _render_row(self, row: int, key: MatrixKey) -> Text:
        """One key's label followed by a connection cell per scope."""
        on_row = row == self._row
        label_style = _HEADER_STYLE if on_row else ""
        line = Text(_pad(_truncate(key.label, _LABEL_WIDTH), _LABEL_WIDTH), style=label_style)
        for col, scope in enumerate(self._scopes):
            glyph = _LINKED_GLYPH if (scope, key.key_id) in self._links else _EMPTY_GLYPH
            line.append(_pad(glyph, _CELL_WIDTH), style=self._cell_style(row, col))
        return line

    def _cell_style(self, row: int, col: int) -> str:
        """Style a body cell: cursor wins, then the crosshair row/column."""
        if row == self._row and col == self._col:
            return _CURSOR_STYLE
        if row == self._row or col == self._col:
            return _CROSSHAIR_STYLE
        return ""

    def _render_legend(self) -> Text:
        """Name each numbered column, ahead of the grid."""
        legend = Text("", style=_DIM_STYLE)
        for index, scope in enumerate(self._scopes):
            legend.append(f"{index + 1}={scope}  ")
        return legend

    def _render_status(self) -> Text:
        """Echo what the cursor is pointing at and whether it is wired."""
        key, scope = self.cursor_key, self.cursor_scope
        if key is None or scope is None:
            return Text("", style=_DIM_STYLE)
        linked = (scope, key.key_id) in self._links
        state = "linked" if linked else "not linked"
        return Text(f"{key.label}  ↔  {scope}   [{state}]", style=_DIM_STYLE)


def _clamp(value: int, length: int) -> int:
    """Keep *value* a valid index into a *length*-long axis (0 when empty)."""
    if length == 0:
        return 0
    return max(0, min(value, length - 1))


def _truncate(text: str, width: int) -> str:
    """Shorten *text* to *width*, marking the cut with an ellipsis."""
    return text if len(text) <= width else text[: width - 1] + "…"


def _pad(text: str, width: int) -> str:
    """Left-justify *text* in a *width*-wide field."""
    return text.ljust(width)
