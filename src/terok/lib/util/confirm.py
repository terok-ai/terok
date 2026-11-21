# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""One yes-or-no question before a launch proceeds, with an answer for unattended runs."""

from __future__ import annotations

import os
import sys

ASSUME_YES_ENV = "TEROK_ASSUME_YES"
"""Set by ``--yes``: every launch question on the way answers itself."""


def confirm_or_exit(question: str, *, hint: str) -> None:
    """Ask *question* on an interactive terminal; proceed on yes, stop with *hint* otherwise.

    Without a terminal the answer is ``TEROK_ASSUME_YES``: set, the launch
    proceeds; unset, it stops with *hint*.  A script never hangs on a
    prompt and never proceeds by accident.
    """
    if os.environ.get(ASSUME_YES_ENV, "").strip().lower() in ("1", "true", "yes"):
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SystemExit(hint)
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    if answer not in ("y", "yes"):
        raise SystemExit(hint)
