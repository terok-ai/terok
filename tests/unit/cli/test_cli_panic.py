# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the panic CLI command."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from terok.cli.commands.panic import _cmd_clear, _cmd_panic, dispatch
from terok.lib.domain.panic import PanicResult


class TestDispatch:
    """Tests for panic dispatch routing."""

    def test_ignores_other_commands(self):
        """Dispatch returns False for non-panic commands."""
        from argparse import Namespace

        assert dispatch(Namespace(cmd="task")) is False

    def test_routes_to_clear(self):
        """Dispatch routes --clear to _cmd_clear."""
        from argparse import Namespace

        with patch("terok.cli.commands.panic._cmd_clear") as mock_clear:
            dispatch(Namespace(cmd="panic", clear=True))
            mock_clear.assert_called_once()

    def test_routes_to_panic(self):
        """Dispatch routes default to _cmd_panic."""
        from argparse import Namespace

        with patch("terok.cli.commands.panic._cmd_panic") as mock_panic:
            dispatch(Namespace(cmd="panic", clear=False))
            mock_panic.assert_called_once_with()


class TestCmdClear:
    """Tests for _cmd_clear."""

    @patch("terok.cli.commands.panic.is_panicked", return_value=True)
    @patch("terok.cli.commands.panic.clear_panic_lock")
    def test_clears_when_panicked(self, mock_clear, _is, capsys):
        """Clears lock and prints instructions when panicked."""
        _cmd_clear()
        mock_clear.assert_called_once()
        out = capsys.readouterr().out
        assert "Panic state cleared" in out
        assert "does not wait for or cancel shutdown work" in out
        # No host services to restart — per-container vault/gate come back
        # automatically at the next task launch via the supervisor.
        assert "supervisor stands them up" in out

    @patch("terok.cli.commands.panic.is_panicked", return_value=False)
    def test_noop_when_not_panicked(self, _is, capsys):
        """Prints info when no panic state exists."""
        _cmd_clear()
        assert "No panic state" in capsys.readouterr().out


class TestCmdPanic:
    """Tests for _cmd_panic."""

    @patch("terok.cli.commands.panic.execute_panic")
    @patch("terok.cli.commands.panic.format_panic_report", return_value="report")
    def test_success_no_running(self, _fmt, mock_exec, capsys):
        """Clean panic with no running containers exits 0."""
        mock_exec.return_value = PanicResult(vault_stopped=True)
        _cmd_panic()
        out = capsys.readouterr().out
        assert "report" in out

    @patch("terok.cli.commands.panic.execute_panic")
    @patch("terok.cli.commands.panic.format_panic_report", return_value="report")
    def test_exits_1_on_errors(self, _fmt, mock_exec):
        """Exits 1 when has_errors is True."""
        mock_exec.return_value = PanicResult(vault_error="fail")
        with pytest.raises(SystemExit, match="1"):
            _cmd_panic()

    @patch("terok.cli.commands.panic.execute_panic")
    @patch("builtins.input", side_effect=AssertionError("panic must never ask"))
    def test_never_prompts_with_running_containers(self, _input, execute):
        """Firing the kill-switch is the only confirmation needed."""
        execute.return_value = PanicResult(vault_stopped=True, total_running=2)
        _cmd_panic()
        execute.assert_called_once_with()
