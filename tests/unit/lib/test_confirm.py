# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""A launch question is answered by the operator, by --yes, or by stopping; never by hanging."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from terok.lib.util import confirm
from terok.lib.util.confirm import ASSUME_YES_ENV, confirm_or_exit


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch):
    """Make the question see a terminal (True) or a pipe (False)."""

    def _set(tty: bool) -> None:
        streams = SimpleNamespace(isatty=lambda: tty)
        monkeypatch.setattr(confirm, "sys", SimpleNamespace(stdin=streams, stdout=streams))

    monkeypatch.delenv(ASSUME_YES_ENV, raising=False)
    return _set


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_assume_yes_answers_without_asking(
    terminal, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    terminal(False)
    monkeypatch.setenv(ASSUME_YES_ENV, value)
    with patch("builtins.input") as ask:
        confirm_or_exit("Go on?", hint="stopped")
    ask.assert_not_called()


def test_without_a_terminal_the_launch_stops_with_the_hint(terminal) -> None:
    terminal(False)
    with patch("builtins.input") as ask, pytest.raises(SystemExit, match="^stopped$"):
        confirm_or_exit("Go on?", hint="stopped")
    ask.assert_not_called()


@pytest.mark.parametrize(
    ("answer", "proceeds"), [("y", True), ("Yes", True), ("", False), ("n", False)]
)
def test_the_terminal_answer_decides(terminal, answer: str, proceeds: bool) -> None:
    terminal(True)
    with patch("builtins.input", return_value=answer):
        if proceeds:
            confirm_or_exit("Go on?", hint="stopped")
        else:
            with pytest.raises(SystemExit, match="^stopped$"):
                confirm_or_exit("Go on?", hint="stopped")


def test_a_closed_stdin_counts_as_no(terminal) -> None:
    terminal(True)
    with (
        patch("builtins.input", side_effect=EOFError),
        pytest.raises(SystemExit, match="^stopped$"),
    ):
        confirm_or_exit("Go on?", hint="stopped")
