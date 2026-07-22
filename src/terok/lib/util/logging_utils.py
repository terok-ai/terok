# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Best-effort file logging + structured stderr warnings.

Module-level helpers over
[`terok_util.logging.BestEffortLogger`][terok_util.logging.BestEffortLogger]
bound to terok's library log.

[`warn_user`][terok.lib.util.logging_utils.warn_user] runs both the
*component* tag and the *message* through
[`sanitize_tty`][terok_util.security.sanitize_tty] so a bad config file or
remote-supplied string can't smuggle ANSI escape sequences into the
operator's terminal (CWE-150).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from terok_util import BestEffortLogger, sanitize_tty

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


@contextmanager
def timed_phase(name: str) -> Iterator[None]:
    """Bracket a launch/lifecycle phase with best-effort timing DEBUG lines.

    Emits ``<name>: start`` on entry and ``<name>: done in N.NNs`` on
    clean exit (``failed in N.NNs`` when the wrapped body raises), so a
    slow ``terok task run`` is diagnosable from ``terok.log`` alone —
    the shared file log only ever timestamps to the second, so the
    monotonic delta is what resolves the sub-second phase boundaries.
    The wrapped exception always propagates unchanged; logging never
    masks it.
    """
    _log_debug(f"{name}: start")
    started = time.monotonic()
    ok = False
    try:
        yield
        ok = True
    finally:
        elapsed = time.monotonic() - started
        _log_debug(f"{name}: {'done' if ok else 'failed'} in {elapsed:.2f}s")


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
