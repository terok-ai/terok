# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Best-effort file logging + structured stderr warnings.

Thin shim over
[`terok_sandbox.BestEffortLogger`][terok_sandbox.BestEffortLogger] —
the implementation lives once in terok-sandbox so terok and the
sandbox share the same idiom.  Module-level functions preserve the
legacy call shape so existing call sites stay untouched.
"""

from __future__ import annotations

from terok_sandbox import BestEffortLogger

LOG_FILENAME = "terok.log"
"""Filename for the best-effort terok library log (written under ``core_state_dir()``)."""


def _terok_log_path():
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
    """Print a structured warning to stderr and append it to the terok log."""
    _logger.warn_user(component, message)
