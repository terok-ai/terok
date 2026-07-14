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
- the ``@terok-login`` window option stamp on every container-login
  window terok opens, so a repeated login switches to the container's
  existing window instead of piling up duplicates (this one works in
  *any* tmux — it only ever touches windows terok itself created);
- quit-time guidance for users unfamiliar with tmux: detecting that
  quitting the TUI will drop them into another tmux window, and flashing
  a status-line hint that survives the window switch.

Every tmux invocation here is best-effort: a missing binary, a dead
server, or an unsupported flag degrades to a quiet no-op, never an error
in the TUI.
"""

import os
import re
import subprocess  # nosec B404 — every call is a fixed "tmux" argv, no shell

SESSION_NAME = "terok"
"""Name of the shared tmux session ``terok tui --tmux`` creates/resumes."""

TEROK_TMUX_ENV = "TEROK_TMUX"
"""Session-environment marker present in every pane of a terok-managed tmux."""

MAIN_WINDOW_OPTION = "@terok-main"
"""tmux window option stamped on the window the TUI is running in."""

LOGIN_WINDOW_OPTION = "@terok-login"
"""tmux window option stamped with the container name a login window is attached to."""

TMUX_TIMEOUT_S = 5
"""Best-effort ceiling for any single host tmux command (a hung server must not stall the TUI)."""

_EXIT_HINT_MS = 10000
_EXIT_HINT = "terok closed — Ctrl-b d returns to your terminal; 'terok tui --tmux' reopens terok"


def _tmux(*args: str) -> str | None:
    """Run a tmux command quietly; return its stdout, or None on any failure."""
    try:
        result = subprocess.run(  # nosec B603 B607 — tmux from PATH, argv verbs built here
            ["tmux", *args],
            capture_output=True,
            text=True,
            timeout=TMUX_TIMEOUT_S,
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


def _version_at_least(major: int, minor: int) -> bool:
    """Return True when the installed tmux reports at least *major*.*minor*.

    Probes ``tmux -V`` (versions like ``3.2a`` or ``next-3.4``); an
    unprobeable tmux reads as too old — degrade, don't break.
    """
    version = re.search(r"(\d+)\.(\d+)", _tmux("-V") or "")
    if version is None:
        return False
    return (int(version.group(1)), int(version.group(2))) >= (major, minor)


def session_marker_args() -> tuple[str, ...]:
    """``new-session`` arguments carrying the terok marker, when tmux supports them.

    Setting a session-environment variable at creation (``new-session
    -e``) needs tmux >= 3.2; an older tmux rejects the flag outright,
    which would kill session creation entirely rather than merely losing
    the marker.  Without the marker the terok-specific niceties quietly
    stay off and the base behaviour is unchanged.
    """
    if _version_at_least(3, 2):
        return ("-e", f"{TEROK_TMUX_ENV}=1")
    return ()


def _window_stamps(option: str, *target: str) -> list[tuple[str, str]]:
    """List ``(window_id, stamp)`` pairs for *option* across the targeted session.

    With no *target* the current session (from ``$TMUX``) is listed;
    pass ``"-t", "=name"`` to query a named session from outside tmux.
    """
    out = _tmux("list-windows", *target, "-F", f"#{{window_id}} #{{{option}}}")
    return [
        (window_id, stamp)
        for window_id, _, stamp in (line.partition(" ") for line in (out or "").splitlines())
    ]


def find_main_window(session: str = SESSION_NAME) -> str | None:
    """Return the window id (``@N``) stamped as terok's main window, or None.

    Window ids are stable handles; window *indexes* are not — the host
    config sets ``renumber-windows on``, so index 1 can become a task
    window the moment an earlier window closes.
    """
    for window_id, stamp in _window_stamps(MAIN_WINDOW_OPTION, "-t", f"={session}"):
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
    for window_id, stamp in _window_stamps(MAIN_WINDOW_OPTION):
        if stamp and window_id != own:
            _tmux("set-option", "-w", "-t", window_id, "-u", MAIN_WINDOW_OPTION)
    _tmux("set-option", "-w", "-t", own, MAIN_WINDOW_OPTION, "1")


def revive_window_args(session: str = SESSION_NAME) -> list[str]:
    """``new-window`` arguments reviving the TUI as *session*'s first window.

    When a resume finds no stamped main window the TUI is respawned, and
    it should land where the original lived: window 1, ahead of the task
    windows that kept the session alive.  Inserting before the
    lowest-numbered window (``-b -t session:^``) needs tmux >= 3.2 — the
    same floor as the session marker; an older tmux appends at the
    session's next free index instead.  Both forms spell the window part
    of the target out explicitly: a bare session target resolves to the
    session's *current* window on newer tmuxes and fails with "index in
    use".
    """
    if _version_at_least(3, 2):
        return ["-b", "-t", f"={session}:^"]
    return ["-t", f"={session}:"]


def find_login_window(cname: str) -> str | None:
    """Return the current session's window logged into container *cname*, or None.

    Login windows close with their ``podman exec`` (no ``remain-on-exit``
    in the host config), so a stamp never outlives its session — whatever
    this finds is live.
    """
    for window_id, stamp in _window_stamps(LOGIN_WINDOW_OPTION):
        if stamp == cname:
            return window_id
    return None


def stamp_login_window(window_id: str, cname: str) -> None:
    """Stamp *window_id* as the login window for container *cname*."""
    _tmux("set-option", "-w", "-t", window_id, LOGIN_WINDOW_OPTION, cname)


def select_window(window_id: str) -> bool:
    """Make *window_id* the current window; True when tmux accepted it."""
    return _tmux("select-window", "-t", window_id) is not None


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
