# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``terok-xdg-terminal-exec`` — Ptyxis-aware shim around ``xdg-terminal-exec``.

The Terok ``.desktop`` launcher uses this binary instead of ``Terminal=true``
when the install-time gate detects Ptyxis + ``xdg-terminal-exec``.  The
shim works around a Ptyxis-specific bug:

* ``Terminal=true`` causes ``xdg-terminal-exec`` to invoke Ptyxis as
  ``ptyxis -- terok-tui``.  The trailing ``--`` token triggers Ptyxis's
  *standalone* mode (``G_APPLICATION_NON_UNIQUE``) — a chrome-less
  window with ``single_terminal_mode=true``: the tab bar is permanently
  hidden, ``win.new-tab`` is disabled, and the window is invisible to
  the Ptyxis daemon.
* Subsequent ``ptyxis --tab`` from inside Terok then talks to the
  *daemon* (a different process), which can't see the standalone-mode
  Terok window and dumps the new tab into whatever unrelated Ptyxis
  window the daemon last had focus in.

Bypass: invoke Ptyxis with ``--new-window`` ourselves, which forces
shared-instance mode (`ptyxis/src/main.c:check_early_opts` —
``--new-window`` sets ``ignore_standalone=TRUE``, suppressing the
``--`` trigger).  Terok then runs in a daemon-owned window with a real
tab bar; later ``ptyxis --tab`` calls land in that window.

For all non-Ptyxis terminals, this shim is a transparent passthrough to
``xdg-terminal-exec`` — same arguments, same ``execvp``.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 — we only invoke the system xdg-terminal-exec
import sys
from typing import NoReturn

#: Binary name we shim around.
_XDG_TERMINAL_EXEC = "xdg-terminal-exec"

#: Ptyxis binary we re-exec to when Ptyxis is the resolved default.
_PTYXIS = "ptyxis"

#: Desktop Entry ID prefix that identifies Ptyxis (matches the upstream
#: ``org.gnome.Ptyxis.desktop`` and any reverse-DNS variants such as
#: ``org.gnome.Ptyxis.Devel.desktop``).
_PTYXIS_DESKTOP_ID_PREFIX = "org.gnome.Ptyxis"

#: Window title applied to the launched Terok window.
_WINDOW_TITLE = "Terok"

#: Timeout for the ``--print-id`` introspection probe.  The probe reads
#: a small config file and prints a string — anything past 1s indicates
#: a stuck binary, and waiting longer would silently freeze the GNOME
#: launcher with no visual feedback.
_PROBE_TIMEOUT_S = 1


def main() -> NoReturn:
    """Resolve the user's default terminal and exec into it.

    Never returns: every successful path replaces the process via
    ``os.execvp``; the only failure (``xdg-terminal-exec`` missing)
    raises ``SystemExit(127)``, the standard "command not found" code.
    """
    args = sys.argv[1:]

    xdg = shutil.which(_XDG_TERMINAL_EXEC)
    if xdg is None:
        print(
            f"terok-xdg-terminal-exec: required binary '{_XDG_TERMINAL_EXEC}' "
            "not found on PATH.  Install it from your package manager "
            "(Fedora: 'dnf install xdg-terminal-exec', Debian/Ubuntu: "
            "'apt install xdg-terminal-exec') or set 'tui.desktop_entry: skip' "
            "in config.yml and re-run 'terok setup' to drop the desktop entry.",
            file=sys.stderr,
        )
        raise SystemExit(127)

    # Both execvp argv lists are our literal flags + the caller's args
    # forwarded verbatim.  S606/B606 fire on any os.exec*; suppression
    # is per-call rather than blanket so a future addition has to make
    # the same conscious choice.
    if _resolved_terminal_is_ptyxis(xdg) and (ptyxis := shutil.which(_PTYXIS)):
        os.execvp(  # noqa: S606  # nosec B606
            ptyxis,
            [ptyxis, "--new-window", "--title", _WINDOW_TITLE, "--", *args],
        )
    else:
        os.execvp(xdg, [xdg, *args])  # noqa: S606  # nosec B606


def _resolved_terminal_is_ptyxis(xdg_path: str) -> bool:
    """Return True iff ``xdg-terminal-exec --print-id`` resolves to Ptyxis.

    ``--print-id`` was added to ``xdg-terminal-exec`` in v0.13.0 (2024).
    On older versions the flag is parsed as a positional command name
    and the call typically fails; we treat any non-zero exit, timeout,
    or non-Ptyxis prefix as "not Ptyxis" and fall through to the
    transparent passthrough.
    """
    try:
        result = subprocess.run(  # noqa: S603  # nosec B603
            [xdg_path, "--print-id"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return result.stdout.strip().startswith(_PTYXIS_DESKTOP_ID_PREFIX)
