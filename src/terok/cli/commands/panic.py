# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Emergency panic command — cut all resource access immediately.

Raises shields on every running container and kills the per-container
supervisors (which stops each container's embedded vault and gate).
Always kills containers without confirmation; they are not removed.
Recovery requires the escrowed vault passphrase.

Exit codes:
- 0: all operations succeeded
- 1: one or more operations failed
"""

from __future__ import annotations

import argparse
import sys

from ...lib.domain.panic import (
    clear_panic_lock,
    execute_panic,
    format_panic_report,
    is_panicked,
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``panic`` subcommand."""
    p = subparsers.add_parser(
        "panic",
        help="Emergency kill-switch: cut all resource access immediately",
    )
    p.add_argument(
        "--clear",
        action="store_true",
        help="Clear panic state",
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle the panic command.  Returns True if handled."""
    if args.cmd != "panic":
        return False

    if getattr(args, "clear", False):
        _cmd_clear()
    else:
        _cmd_panic()
    return True


def _cmd_clear() -> None:
    """Clear the panic lock file."""
    if is_panicked():
        clear_panic_lock()
        print("Panic state cleared.")
        print("Containers remain stopped. Re-supply the recovery passphrase before restarting.")
        print(
            "Per-container services (vault, gate) come back automatically when you "
            "start a fresh task — the supervisor stands them up at container launch."
        )
    else:
        print("No panic state to clear.")


def _cmd_panic() -> None:
    """Execute the full panic sequence."""
    print("PANIC — cutting all resource access", file=sys.stderr)
    print(file=sys.stderr)

    result = execute_panic()

    print()
    print(format_panic_report(result))

    if result.has_errors:
        sys.exit(1)
