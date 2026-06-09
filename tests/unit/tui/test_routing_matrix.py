# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the RoutingMatrix widget's cursor, intents, and rendering.

The widget is constructed without Textual's app machinery (``__new__``
plus mocked ``refresh``/``post_message``) so the pure grid logic can be
exercised in isolation.
"""

from __future__ import annotations

from unittest import mock

import pytest

from terok.tui.widgets.routing_matrix import (
    RoutingMatrix,
    _clamp,
    _pad,
    _truncate,
)


@pytest.fixture()
def matrix():
    """A matrix pre-loaded with two keys, three scopes, and one link."""
    m = RoutingMatrix.__new__(RoutingMatrix)
    m._keys = []
    m._scopes = []
    m._links = set()
    m._row = 0
    m._col = 0
    m.refresh = mock.Mock()
    m.post_message = mock.Mock()
    from terok.tui.widgets.routing_matrix import MatrixKey

    m.set_routing(
        [MatrixKey(1, "tk-main:foo"), MatrixKey(2, "tk-main:bar")],
        ["foo", "bar", "quux"],
        {("bar", 2)},
    )
    return m


class TestHelpers:
    """The pure padding/clamping helpers underpinning alignment."""

    @pytest.mark.parametrize(
        ("value", "length", "expected"),
        [(-1, 3, 0), (5, 3, 2), (1, 3, 1), (0, 0, 0)],
    )
    def test_clamp(self, value, length, expected):
        """Clamp keeps an index inside the axis, collapsing to 0 when empty."""
        assert _clamp(value, length) == expected

    def test_truncate_marks_the_cut(self):
        """Over-long labels end in an ellipsis at the exact width."""
        assert _truncate("abcdefgh", 4) == "abc…"

    def test_truncate_leaves_short_text(self):
        """Short-enough labels pass through untouched."""
        assert _truncate("ab", 4) == "ab"

    def test_pad_left_justifies(self):
        """Pad fills to width on the right."""
        assert _pad("a", 3) == "a  "


class TestCursor:
    """Cursor movement stays within the grid and repaints."""

    def test_move_right(self, matrix):
        """Stepping right advances the column."""
        matrix.action_move(0, 1)
        assert (matrix._row, matrix._col) == (0, 1)

    def test_move_clamps_at_edges(self, matrix):
        """Moving past the last row clamps instead of overflowing."""
        matrix.action_move(99, 99)
        assert (matrix._row, matrix._col) == (1, 2)

    def test_move_refreshes(self, matrix):
        """Every move triggers a repaint."""
        matrix.refresh.reset_mock()
        matrix.action_move(1, 0)
        matrix.refresh.assert_called_once()


class TestIntents:
    """Toggling and minting post intents rather than mutating state."""

    def test_toggle_reports_current_state(self, matrix):
        """Toggling an unlinked cell reports linked=False for that (scope, key)."""
        matrix._row, matrix._col = 0, 0  # (foo, key 1) — not linked
        matrix.action_toggle_cell()
        msg = matrix.post_message.call_args[0][0]
        assert (msg.scope, msg.key_id, msg.linked) == ("foo", 1, False)

    def test_toggle_reads_existing_link(self, matrix):
        """Toggling a wired cell reports linked=True."""
        matrix._row, matrix._col = 1, 1  # (bar, key 2) — linked
        matrix.action_toggle_cell()
        msg = matrix.post_message.call_args[0][0]
        assert (msg.scope, msg.key_id, msg.linked) == ("bar", 2, True)

    def test_mint_targets_cursor_column(self, matrix):
        """Minting names the scope under the cursor column."""
        matrix._col = 2
        matrix.action_mint()
        msg = matrix.post_message.call_args[0][0]
        assert msg.scope == "quux"

    def test_toggle_noop_without_keys(self, matrix):
        """With no rows there is nothing to toggle."""
        matrix.set_routing([], ["foo"], set())
        matrix.post_message.reset_mock()
        matrix.action_toggle_cell()
        matrix.post_message.assert_not_called()


class TestRender:
    """The rendered grid shows the right glyphs and labels."""

    def test_shows_linked_and_empty_glyphs(self, matrix):
        """A wired cell renders ●; the rest render ·."""
        plain = matrix.render().plain
        assert "●" in plain
        assert "·" in plain

    def test_lists_key_labels_and_legend(self, matrix):
        """Key labels and the numbered scope legend both appear."""
        plain = matrix.render().plain
        assert "tk-main:foo" in plain
        assert "1=foo" in plain

    def test_legend_sits_above_the_numeric_header(self, matrix):
        """Scope names lead; the numeric header follows them."""
        lines = [line for line in matrix.render().plain.splitlines() if line.strip()]
        assert "1=foo" in lines[0]

    def test_status_line_echoes_cursor(self, matrix):
        """The status line names the cursor cell and its link state."""
        matrix._row, matrix._col = 1, 1
        plain = matrix.render().plain
        assert "bar" in plain
        assert "linked" in plain

    def test_empty_projects_message(self, matrix):
        """With no columns the widget says so instead of rendering a grid."""
        matrix.set_routing([], [], set())
        assert "No projects" in matrix.render().plain
