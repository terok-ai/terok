# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``terokctl proxy`` CLI subcommand."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from terok.cli.commands.proxy import dispatch, register
from tests.testfs import MOCK_BASE

MOCK_PROXY_SOCKET = MOCK_BASE / "run" / "credential-proxy.sock"
MOCK_PROXY_DB = MOCK_BASE / "proxy" / "credentials.db"
MOCK_PROXY_ROUTES = MOCK_BASE / "proxy" / "routes.json"


def _make_parser() -> argparse.ArgumentParser:
    """Build a minimal parser with the proxy subcommand registered."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    register(sub)
    return parser


def _make_status(*, running: bool = False) -> MagicMock:
    """Build a proxy status mock."""
    status = MagicMock()
    status.running = running
    status.socket_path = MOCK_PROXY_SOCKET
    status.db_path = MOCK_PROXY_DB
    status.routes_path = MOCK_PROXY_ROUTES
    status.routes_configured = 3
    status.credentials_stored = ("claude", "gh")
    return status


class TestProxyRegister:
    """Verify subcommand registration."""

    def test_proxy_subcommands_registered(self) -> None:
        """All proxy subcommands are parseable."""
        parser = _make_parser()
        for sub in ("start", "stop", "status"):
            args = parser.parse_args(["proxy", sub])
            assert args.cmd == "proxy"
            assert args.proxy_cmd == sub


class TestProxyDispatch:
    """Verify dispatch routing."""

    def test_dispatch_ignores_other_commands(self) -> None:
        """dispatch returns False for non-proxy commands."""
        args = argparse.Namespace(cmd="task")
        assert dispatch(args) is False

    @patch("terok.cli.commands.proxy.get_proxy_status")
    def test_dispatch_status(self, mock_status, capsys) -> None:
        """'proxy status' prints status info."""
        mock_status.return_value = _make_status()
        parser = _make_parser()
        args = parser.parse_args(["proxy", "status"])
        assert dispatch(args) is True
        out = capsys.readouterr().out
        assert "stopped" in out
        assert "claude" in out
        assert "3 configured" in out

    @patch("terok.cli.commands.proxy.start_proxy")
    @patch("terok_agent.ensure_proxy_routes")
    @patch("terok.cli.commands.proxy.is_proxy_running", return_value=False)
    def test_dispatch_start(self, mock_running, mock_routes, mock_start, capsys) -> None:
        """'proxy start' generates routes and starts the daemon."""
        mock_routes.return_value = MOCK_PROXY_ROUTES
        parser = _make_parser()
        args = parser.parse_args(["proxy", "start"])
        assert dispatch(args) is True
        mock_routes.assert_called_once()
        mock_start.assert_called_once()
        assert "started" in capsys.readouterr().out

    @patch("terok.cli.commands.proxy.is_proxy_running", return_value=True)
    def test_dispatch_start_already_running(self, mock_running) -> None:
        """'proxy start' exits if already running."""
        parser = _make_parser()
        args = parser.parse_args(["proxy", "start"])
        with pytest.raises(SystemExit):
            dispatch(args)

    @patch("terok.cli.commands.proxy.stop_proxy")
    @patch("terok.cli.commands.proxy.is_proxy_running", return_value=True)
    def test_dispatch_stop(self, mock_running, mock_stop, capsys) -> None:
        """'proxy stop' calls stop_proxy."""
        parser = _make_parser()
        args = parser.parse_args(["proxy", "stop"])
        assert dispatch(args) is True
        mock_stop.assert_called_once()
        assert "stopped" in capsys.readouterr().out

    @patch("terok.cli.commands.proxy.is_proxy_running", return_value=False)
    def test_dispatch_stop_not_running(self, mock_running, capsys) -> None:
        """'proxy stop' prints info when not running."""
        parser = _make_parser()
        args = parser.parse_args(["proxy", "stop"])
        assert dispatch(args) is True
        assert "not running" in capsys.readouterr().out
