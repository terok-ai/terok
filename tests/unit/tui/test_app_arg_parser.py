# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the [`terok-tui`][terok.tui.app] argument parser.

The ``--tmux`` / ``--no-tmux`` pair shares one destination and must stay
tri-state (``None`` / ``True`` / ``False``).  Earlier the parser left
``args.tmux`` as a bool when neither flag was given, silently masking the
fallback to the global ``tui.default_tmux`` config setting.
"""

from __future__ import annotations

import pytest

from terok.tui.app import _build_arg_parser


class TestTmuxTriState:
    """``args.tmux`` must distinguish "no flag" from "--no-tmux"."""

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            pytest.param([], None, id="neither-flag-set"),
            pytest.param(["--tmux"], True, id="explicit-tmux"),
            pytest.param(["--no-tmux"], False, id="explicit-no-tmux"),
        ],
    )
    def test_tmux_destination(self, argv: list[str], expected: bool | None) -> None:
        """No flag → ``None`` so ``main`` can fall back to ``tui.default_tmux``."""
        assert _build_arg_parser().parse_args(argv).tmux is expected

    def test_passing_both_flags_is_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Mutual exclusion: ``--tmux`` and ``--no-tmux`` together is an argparse error."""
        with pytest.raises(SystemExit):
            _build_arg_parser().parse_args(["--tmux", "--no-tmux"])
        assert "not allowed with" in capsys.readouterr().err


class TestOtherFlags:
    """Sibling flags use their natural argparse defaults — covered to lock the contract."""

    def test_experimental_defaults_to_false(self) -> None:
        """``--experimental`` is off unless asked for."""
        assert _build_arg_parser().parse_args([]).experimental is False

    def test_experimental_can_be_enabled(self) -> None:
        """``--experimental`` flips on the experimental gate."""
        assert _build_arg_parser().parse_args(["--experimental"]).experimental is True

    def test_no_emoji_defaults_to_false(self) -> None:
        """``--no-emoji`` is off unless asked for."""
        assert _build_arg_parser().parse_args([]).no_emoji is False

    def test_no_emoji_can_be_enabled(self) -> None:
        """``--no-emoji`` swaps emojis for plain-text labels."""
        assert _build_arg_parser().parse_args(["--no-emoji"]).no_emoji is True

    def test_new_session_defaults_to_false(self) -> None:
        """Without ``--new-session`` the launcher attaches to the shared session."""
        assert _build_arg_parser().parse_args([]).new_session is False

    def test_new_session_can_be_enabled(self) -> None:
        """``--new-session`` opts out of attaching and forces a fresh session."""
        assert _build_arg_parser().parse_args(["--new-session"]).new_session is True
