# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``terok vault`` CLI and ``vault-serve``."""

import argparse
import sys
from unittest.mock import MagicMock, patch

from terok.cli.commands.credentials import dispatch, register


class TestVaultServeRegister:
    """Verify vault-serve registration."""

    def test_serve_registered(self) -> None:
        """vault-serve is parseable as a top-level command."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        args = parser.parse_args(["vault-serve"])
        assert args.cmd == "vault-serve"


class TestVaultServeDispatch:
    """Verify serve dispatch routing."""

    def test_dispatch_ignores_other_commands(self) -> None:
        """dispatch returns False for non-serve commands."""
        args = argparse.Namespace(cmd="task")
        assert dispatch(args) is False

    @patch("terok.cli.commands.credentials._cmd_serve")
    def test_dispatch_serve(self, mock_serve: MagicMock) -> None:
        """vault-serve dispatches to _cmd_serve."""
        args = argparse.Namespace(cmd="vault-serve")
        assert dispatch(args) is True
        mock_serve.assert_called_once_with(args)

    @patch("terok_sandbox.vault.token_broker.main")
    def test_serve_passes_through_to_server_main(self, mock_main: MagicMock) -> None:
        """_cmd_serve strips argv prefix and delegates to server.main()."""
        captured_argv: list[str] = []
        mock_main.side_effect = lambda: captured_argv.extend(sys.argv)

        original_argv = sys.argv[:]
        sys.argv = ["terok", "vault-serve", "--log-level", "DEBUG"]
        try:
            args = argparse.Namespace(cmd="vault-serve")
            dispatch(args)
        finally:
            sys.argv = original_argv

        mock_main.assert_called_once()
        assert captured_argv == ["terok-vault-serve", "--log-level", "DEBUG"]
        assert sys.argv == original_argv  # restored after call


class TestVaultWireGroup:
    """Verify vault commands are mounted via wire_group."""

    def test_vault_group_registered(self) -> None:
        """terok vault group is parseable."""
        from terok_executor import VAULT_COMMANDS

        from terok.cli.wiring import wire_group

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire_group(sub, "vault", VAULT_COMMANDS, help="test")
        for cmd in ("start", "stop", "status", "install", "uninstall", "routes"):
            args = parser.parse_args(["vault", cmd])
            assert args.cmd == "vault"
