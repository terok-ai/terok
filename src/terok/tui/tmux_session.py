# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Helpers for the terok-managed host tmux session.

``terok tui --tmux`` wraps the TUI in a tmux session so login windows and
long-running tasks survive terminal closes.  This module owns everything
that makes that session recognisably *terok's*:

- the ``TEROK_TMUX`` session environment marker (set at session creation
  via ``new-session -e``, tmux >= 3.2) that lets any process inside the
  session tell a terok-managed tmux apart from the user's own;
- the ``@terok-main`` window option stamp the TUI puts on its own window
  at startup, so a resume can land the client back on terok even after
  the user killed and relaunched the TUI in a different window;
- quit-time guidance for users unfamiliar with tmux: detecting that
  quitting the TUI will drop them into another tmux window, and flashing
  a status-line hint that survives the window switch.

Every tmux invocation here is best-effort: a missing binary, a dead
server, or an unsupported flag degrades to a quiet no-op, never an error
in the TUI.
"""

import os
import subprocess

SESSION_NAME = "terok"
"""Name of the shared tmux session ``terok tui --tmux`` creates/resumes."""

TEROK_TMUX_ENV = "TEROK_TMUX"
"""Session-environment marker present in every pane of a terok-managed tmux."""

MAIN_WINDOW_OPTION = "@terok-main"
"""tmux window option stamped on the window the TUI is running in."""

_TMUX_TIMEOUT_S = 5
_EXIT_HINT_MS = 10000
_EXIT_HINT = "terok closed — Ctrl-b d returns to your terminal; 'terok tui --tmux' reopens terok"


def _tmux(*args: str) -> str | None:
    """Run a tmux command quietly; return its stdout, or None on any failure."""
    try:
        result = subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            timeout=_TMUX_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def is_terok_tmux() -> bool:
    """Return True when running inside a tmux session that terok launched.

    Both conditions must hold: we are inside *some* tmux (``TMUX`` set by
    tmux itself) and that session carries the terok marker — a user's
    custom tmux never gets terok-specific behaviour.
    """
    return bool(os.environ.get("TMUX")) and bool(os.environ.get(TEROK_TMUX_ENV))


def session_exists() -> bool:
    """Return True when the shared terok session exists on the default server."""
    return _tmux("has-session", "-t", f"={SESSION_NAME}") is not None


def find_main_window(session: str = SESSION_NAME) -> str | None:
    """Return the window id (``@N``) stamped as terok's main window, or None.

    Window ids are stable handles; window *indexes* are not — the host
    config sets ``renumber-windows on``, so index 1 can become a task
    window the moment an earlier window closes.
    """
    out = _tmux(
        "list-windows", "-t", f"={session}", "-F", f"#{{window_id}} #{{{MAIN_WINDOW_OPTION}}}"
    )
    for line in (out or "").splitlines():
        window_id, _, stamp = line.partition(" ")
        if stamp == "1":
            return window_id
    return None


def stamp_main_window() -> None:
    """Stamp the calling process's window as terok's main window.

    Called by the TUI at startup so the stamp self-heals: when the user
    kills terok and relaunches it in a different window, the new instance
    marks its own window and clears any stale stamp left behind.  No-op
    outside a terok-managed tmux.
    """
    if not is_terok_tmux():
        return
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return
    own = (_tmux("display-message", "-p", "-t", pane, "#{window_id}") or "").strip()
    if not own:
        return
    listed = _tmux("list-windows", "-F", f"#{{window_id}} #{{{MAIN_WINDOW_OPTION}}}") or ""
    for line in listed.splitlines():
        window_id, _, stamp = line.partition(" ")
        if stamp and window_id != own:
            _tmux("set-option", "-w", "-t", window_id, "-u", MAIN_WINDOW_OPTION)
    _tmux("set-option", "-w", "-t", own, MAIN_WINDOW_OPTION, "1")


def _pane_is_root_command() -> bool:
    """Return True when this process *is* the pane's command (window dies with us)."""
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return False
    pane_pid = (_tmux("display-message", "-p", "-t", pane, "#{pane_pid}") or "").strip()
    return pane_pid == str(os.getpid())


def quit_lands_in_other_window() -> int:
    """Return how many sibling windows would catch the user when the TUI quits.

    Non-zero means: we are inside a terok-managed tmux, this process is
    the pane's root command (so quitting closes the window), and other
    windows remain — the user will be dropped into one of them instead of
    their terminal.  Zero means quitting behaves unsurprisingly (last
    window ends the session, or a shell below us survives) and no tmux
    guidance is needed.
    """
    if not (is_terok_tmux() and _pane_is_root_command()):
        return 0
    windows = (_tmux("display-message", "-p", "#{session_windows}") or "").strip()
    try:
        return max(int(windows) - 1, 0)
    except ValueError:
        return 0


def detach_client() -> None:
    """Detach the attached tmux client, returning the user to their terminal."""
    _tmux("detach-client")


def flash_exit_hint() -> None:
    """Flash a status-line hint telling the user how to leave tmux.

    ``display-message`` is per-*client*, so a message fired just before
    the TUI's window dies stays visible in whatever window the client
    lands in.  No-op unless quitting actually drops the user into another
    window.
    """
    if not quit_lands_in_other_window():
        return
    _tmux("display-message", "-d", str(_EXIT_HINT_MS), _EXIT_HINT)
