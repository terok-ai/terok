# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Vault serve command — foreground passthrough for systemd/debug.

The main vault commands (start, stop, status, install, uninstall, routes) are
mounted from terok-executor's ``VAULT_COMMANDS`` via ``wire_group`` in
:mod:`terok.cli.main`.  This module only provides the ``vault-serve`` top-level
command, which passes through to the token broker's own argparse.
"""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``vault-serve`` command."""
    subparsers.add_parser(
        "vault-serve",
        help="Run vault token broker in foreground (used by systemd)",
        add_help=False,
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle vault-serve.  Returns True if handled."""
    if args.cmd != "vault-serve":
        return False
    _cmd_serve(args)
    return True


def _cmd_serve(_args: argparse.Namespace) -> None:
    """Run the vault token broker in the foreground.

    Delegates to the token broker's own ``main()`` which handles its own
    argparse.  Used by systemd service units and ``start_daemon()``.
    """
    import sys as _sys

    # Strip the "vault-serve" prefix so the token broker's argparse
    # sees only its own flags (--socket-path, --db-path, etc.).
    idx = _sys.argv.index("vault-serve")
    saved = _sys.argv
    try:
        _sys.argv = ["terok-vault-serve", *_sys.argv[idx + 1 :]]
        from terok_sandbox.vault.token_broker import main as _serve

        _serve()
    finally:
        _sys.argv = saved
