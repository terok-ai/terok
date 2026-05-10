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

#: Timeout for the ``--print-id`` introspection probe.
_PROBE_TIMEOUT_S = 5


def main() -> int:
    """Resolve the user's default terminal and exec into it.

    Returns:
        On success this function does not return — control is handed
        off via ``os.execvp`` to the chosen terminal.  On hard failure
        (no ``xdg-terminal-exec`` on PATH) returns 127, the standard
        "command not found" exit code.
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
        return 127

    if _resolved_terminal_is_ptyxis(xdg) and (ptyxis := shutil.which(_PTYXIS)):
        os.execvp(  # noqa: S606  # nosec B606 — argv is our literal flags + caller args
            ptyxis,
            [ptyxis, "--new-window", "--title", _WINDOW_TITLE, "--", *args],
        )
    else:
        os.execvp(xdg, [xdg, *args])  # noqa: S606  # nosec B606
    return 0  # unreachable in production (execvp replaces the process); satisfies the type checker


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
