# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the suspended-child terminal pollution guard and its runner.

Children run under a suspended TUI can exit leaving the shared tty in
raw mode and/or the stdio file descriptions ``O_NONBLOCK``; the guard
must scrub both so the resume prompt and Textual's writer thread get a
sane terminal back (see ``_terminal_pollution_guard``).  ``_run_suspended``
is the shared suspend-run-scrub-prompt dance built on top of it.
"""

import asyncio
import fcntl
import os
import pty
import sys
import termios
import tty
from collections.abc import Iterator
from types import SimpleNamespace
from unittest import mock

import pytest

from terok.tui.project_actions import ProjectActionsMixin, _terminal_pollution_guard


def _nonblock(fd: int) -> bool:
    return bool(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_NONBLOCK)


def _set_nonblock(fd: int) -> None:
    fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) | os.O_NONBLOCK)


@pytest.fixture
def guarded_tty(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    """A pty wired up as the process's stdin/stdout, as the guard sees them."""
    primary, replica = pty.openpty()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(fileno=lambda: replica))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(fileno=lambda: replica))
    yield replica
    os.close(replica)
    os.close(primary)


def test_restores_cooked_mode_after_raw_child(guarded_tty: int) -> None:
    """A tty left in raw mode comes back with canonical line input and echo."""
    with _terminal_pollution_guard():
        tty.setraw(guarded_tty)
    attrs = termios.tcgetattr(guarded_tty)
    assert attrs[3] & termios.ICANON
    assert attrs[3] & termios.ECHO
    assert attrs[0] & termios.ICRNL


def test_clears_nonblock_left_by_child(guarded_tty: int) -> None:
    """``O_NONBLOCK`` left on the stdio file description is cleared."""
    with _terminal_pollution_guard():
        _set_nonblock(guarded_tty)
    assert not _nonblock(guarded_tty)


def _crashing_polluting_child(fd: int) -> None:
    """Simulate a child that trashes the tty and then dies."""
    tty.setraw(fd)
    _set_nonblock(fd)
    raise RuntimeError("child launch failed")


def test_scrubs_even_when_child_raises(guarded_tty: int) -> None:
    """The scrub happens on the exception path too, not just clean exits."""
    with pytest.raises(RuntimeError), _terminal_pollution_guard():
        _crashing_polluting_child(guarded_tty)
    assert termios.tcgetattr(guarded_tty)[3] & termios.ICANON
    assert not _nonblock(guarded_tty)


def test_clears_nonblock_on_non_tty_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pipes have no termios to restore, but still get ``O_NONBLOCK`` scrubbed."""
    read_end, write_end = os.pipe()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(fileno=lambda: read_end))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(fileno=lambda: write_end))
    try:
        with _terminal_pollution_guard():
            _set_nonblock(write_end)
        assert not _nonblock(write_end)
    finally:
        os.close(read_end)
        os.close(write_end)


def test_noop_without_usable_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Closed or fd-less stdio (headless runs) degrades to a silent no-op."""

    def _no_fileno() -> int:
        raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(sys, "stdin", SimpleNamespace(fileno=_no_fileno))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(fileno=_no_fileno))
    with _terminal_pollution_guard():
        pass  # must simply not raise


class TestRunSuspended:
    """``_run_suspended`` — the shared suspend-run-scrub-prompt dance."""

    def _instance(self) -> mock.Mock:
        """Build a mixin mock with ``suspend`` wired as a context manager."""
        instance = mock.Mock(spec=ProjectActionsMixin)
        instance.suspend = mock.MagicMock()
        return instance

    def _run(self, instance: mock.Mock, *argv: str, **kwargs: bool) -> int | None:
        """Drive the unbound coroutine against *instance* to completion."""
        return asyncio.run(ProjectActionsMixin._run_suspended(instance, *argv, **kwargs))

    def _proc(self, exit_code: int) -> mock.AsyncMock:
        """A fake asyncio subprocess whose ``wait()`` returns *exit_code*."""
        proc = mock.AsyncMock()
        proc.wait.return_value = exit_code
        return proc

    def test_clean_exit_prompts_and_returns_code(self) -> None:
        """Default mode: the child runs suspended and the prompt always shows."""
        instance = self._instance()
        with (
            mock.patch(
                "terok.tui.project_actions.asyncio.create_subprocess_exec",
                new=mock.AsyncMock(return_value=self._proc(3)),
            ) as exec_mock,
            mock.patch("builtins.input") as input_mock,
        ):
            code = self._run(instance, "agent", "--flag")
        exec_mock.assert_awaited_once_with("agent", "--flag")
        assert code == 3
        instance.suspend.assert_called_once_with()
        input_mock.assert_called_once()

    def test_prompt_on_success_false_skips_prompt(self) -> None:
        """A zero exit returns straight to the TUI when the prompt is opted out."""
        instance = self._instance()
        with (
            mock.patch(
                "terok.tui.project_actions.asyncio.create_subprocess_exec",
                new=mock.AsyncMock(return_value=self._proc(0)),
            ),
            mock.patch("builtins.input") as input_mock,
        ):
            code = self._run(instance, "vim", prompt_on_success=False)
        assert code == 0
        input_mock.assert_not_called()

    def test_nonzero_exit_prompts_even_without_prompt_on_success(self) -> None:
        """A failing child's last output stays readable — the prompt is forced."""
        instance = self._instance()
        with (
            mock.patch(
                "terok.tui.project_actions.asyncio.create_subprocess_exec",
                new=mock.AsyncMock(return_value=self._proc(2)),
            ),
            mock.patch("builtins.input") as input_mock,
        ):
            code = self._run(instance, "vim", prompt_on_success=False)
        assert code == 2
        input_mock.assert_called_once()

    def test_launch_failure_returns_none_and_always_prompts(self) -> None:
        """A child that never launched reports ``None`` and keeps the error visible."""
        instance = self._instance()
        with (
            mock.patch(
                "terok.tui.project_actions.asyncio.create_subprocess_exec",
                new=mock.AsyncMock(side_effect=FileNotFoundError("no such agent")),
            ),
            mock.patch("builtins.input") as input_mock,
        ):
            code = self._run(instance, "bogus", prompt_on_success=False)
        assert code is None
        input_mock.assert_called_once()

    def test_eof_at_the_prompt_is_survived(self) -> None:
        """EOF instead of Enter (dead stdin) must not crash the resume path."""
        instance = self._instance()
        with (
            mock.patch(
                "terok.tui.project_actions.asyncio.create_subprocess_exec",
                new=mock.AsyncMock(return_value=self._proc(0)),
            ),
            mock.patch("builtins.input", side_effect=EOFError),
        ):
            assert self._run(instance, "agent") == 0
