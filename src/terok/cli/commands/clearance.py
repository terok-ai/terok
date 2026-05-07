# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``terok clearance`` — standalone TUI for live D-Bus shield verdicts.

Launches a minimal Textual app that listens on the whole D-Bus session
bus, shows blocked container connections in real-time, and lets the
operator Allow/Deny via keybindings.
"""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``clearance`` subcommand."""
    subparsers.add_parser(
        "clearance",
        help="Live TUI for shield clearance verdicts (D-Bus)",
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Launch the clearance TUI if invoked.  Returns True if handled."""
    if getattr(args, "cmd", None) != "clearance":
        return False

    import os

    os.execlp("terok-clearance", "terok-clearance")
    return True  # type: ignore[unreachable]  # in tests os.execlp is mocked
