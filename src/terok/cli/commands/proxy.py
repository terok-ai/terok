# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Credential proxy management commands: start, stop, status.

Wraps the terok-sandbox proxy lifecycle with route generation from the
agent registry — ``terokctl proxy start`` writes ``routes.json`` before
launching the daemon so the proxy is always up-to-date with the YAML
agent definitions.
"""

from __future__ import annotations

import argparse
import sys

from terok_sandbox import (
    CredentialProxyStatus,
    get_proxy_status,
    is_proxy_running,
    start_proxy,
    stop_proxy,
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``proxy`` subcommand group."""
    p = subparsers.add_parser("proxy", help="Credential proxy commands")
    sub = p.add_subparsers(dest="proxy_cmd", required=True)

    sub.add_parser("start", help="Start the credential proxy daemon")
    sub.add_parser("stop", help="Stop the credential proxy daemon")
    sub.add_parser("status", help="Show credential proxy status")


def dispatch(args: argparse.Namespace) -> bool:
    """Handle proxy commands.  Returns True if handled."""
    if args.cmd != "proxy":
        return False

    cmd = args.proxy_cmd
    if cmd == "start":
        _cmd_start()
    elif cmd == "stop":
        _cmd_stop()
    elif cmd == "status":
        _cmd_status()
    else:
        return False
    return True


def _cmd_start() -> None:
    """Generate routes and start the credential proxy daemon."""
    from terok_agent import ensure_proxy_routes

    if is_proxy_running():
        print("Credential proxy is already running.")
        sys.exit(1)
    path = ensure_proxy_routes()
    print(f"Routes:  {path}")
    start_proxy()
    print("Credential proxy started.")


def _cmd_stop() -> None:
    """Stop the credential proxy daemon."""
    if not is_proxy_running():
        print("Credential proxy is not running.")
        return
    stop_proxy()
    print("Credential proxy stopped.")


def _cmd_status() -> None:
    """Show credential proxy status."""
    status: CredentialProxyStatus = get_proxy_status()
    state = "running" if status.running else "stopped"
    print(f"Status:      {state}")
    print(f"Socket:      {status.socket_path}")
    print(f"DB:          {status.db_path}")
    print(f"Routes:      {status.routes_path} ({status.routes_configured} configured)")
    if status.credentials_stored:
        print(f"Credentials: {', '.join(status.credentials_stored)}")
    else:
        print("Credentials: none stored")
