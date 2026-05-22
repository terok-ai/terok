# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Best-effort file logging + structured stderr warnings.

Thin shim over
[`terok_sandbox.BestEffortLogger`][terok_sandbox.BestEffortLogger] —
the implementation lives once in terok-sandbox so terok and the
sandbox share the same idiom.  Module-level functions preserve the
legacy call shape so existing call sites stay untouched.

[`warn_user`][terok.lib.util.logging_utils.warn_user] runs both the
*component* tag and the *message* through
[`sanitize_tty`][terok_util.security.sanitize_tty] before handing
them off to the sandbox logger so a bad config file or remote-supplied
string can't smuggle ANSI escape sequences into the operator's
terminal (CWE-150).  The file log keeps the original bytes — the
forensic record matters more than rendering aesthetics there.
"""

from __future__ import annotations

from pathlib import Path

from terok_util import sanitize_tty

from terok.lib.integrations.sandbox import BestEffortLogger

LOG_FILENAME = "terok.log"
"""Filename for the best-effort terok library log (written under ``core_state_dir()``)."""


def _terok_log_path() -> Path:
    """Resolve terok's log path lazily so XDG / env-var overrides take effect."""
    from ..core.paths import core_state_dir

    return core_state_dir() / LOG_FILENAME


_logger = BestEffortLogger(_terok_log_path)


def _log(message: str, *, level: str = "DEBUG") -> None:
    """Append a timestamped line to the terok library log.  Never raises."""
    _logger.log(message, level=level)


def _log_debug(message: str) -> None:
    """Append a DEBUG line to the terok library log."""
    _logger.debug(message)


def log_warning(message: str) -> None:
    """Append a WARNING line to the terok library log."""
    _logger.warning(message)


def warn_user(component: str, message: str) -> None:
    """Print a structured warning to stderr and append it to the terok log.

    Both *component* and *message* are run through
    [`sanitize_tty`][terok_util.security.sanitize_tty] so any C0/C1
    control characters or ANSI escape sequences in attacker-influenced
    strings (e.g. ``project.yml`` values, SSH key comments, gate
    server diagnostics) get rendered as ``\\xNN`` hex escapes rather
    than executing on the operator's terminal.
    """
    _logger.warn_user(sanitize_tty(component), sanitize_tty(message))
