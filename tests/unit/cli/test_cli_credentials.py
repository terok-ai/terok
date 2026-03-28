# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``terokctl credential-proxy`` CLI and ``credential-proxy-serve``."""

from __future__ import annotations

import argparse
from unittest.mock import patch

from terok.cli.commands.credentials import dispatch, register


class TestCredentialProxyServeRegister:
    """Verify credential-proxy-serve registration."""

    def test_serve_registered(self) -> None:
        """credential-proxy-serve is parseable as a top-level command."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        args = parser.parse_args(["credential-proxy-serve"])
        assert args.cmd == "credential-proxy-serve"


class TestCredentialProxyServeDispatch:
    """Verify serve dispatch routing."""

    def test_dispatch_ignores_other_commands(self) -> None:
        """dispatch returns False for non-serve commands."""
        args = argparse.Namespace(cmd="task")
        assert dispatch(args) is False

    @patch("terok.cli.commands.credentials._cmd_serve")
    def test_dispatch_serve(self, mock_serve) -> None:
        """credential-proxy-serve dispatches to _cmd_serve."""
        args = argparse.Namespace(cmd="credential-proxy-serve")
        assert dispatch(args) is True
        mock_serve.assert_called_once_with(args)


class TestCredentialProxyWireGroup:
    """Verify credential-proxy commands are mounted via wire_group."""

    def test_credential_proxy_group_registered(self) -> None:
        """terokctl credential-proxy group is parseable."""
        from terok_agent import PROXY_COMMANDS
        from terok.cli.wiring import wire_group

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire_group(sub, "credential-proxy", PROXY_COMMANDS, help="test")
        for cmd in ("start", "stop", "status", "install", "uninstall", "routes"):
            args = parser.parse_args(["credential-proxy", cmd])
            assert args.cmd == "credential-proxy"
