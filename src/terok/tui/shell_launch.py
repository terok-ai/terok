# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Helpers for launching interactive login shells from the TUI.

Provides tmux detection, desktop terminal detection, and an orchestrator
that picks the best available method.

Container login is only ever attempted from a *local-terminal* TUI —
[`_launch_terminal_session`][terok.tui.project_actions.ProjectActionsMixin._launch_terminal_session]
refuses it under textual-serve before reaching here (issue #473) — so
nothing in this module needs to handle the web-served case.
"""

import os
import shlex
import subprocess


def is_inside_tmux() -> bool:
    """Return True if the current process is running inside a tmux session."""
    return bool(os.environ.get("TMUX"))


def is_inside_gnome_terminal() -> bool:
    """Return True if the current process is running inside GNOME Terminal.

    Checks multiple methods for detection:
    1. TERM_PROGRAM environment variable
    2. GNOME_TERMINAL_SERVICE environment variable
    3. Parent process name (fallback only if above are not set)
    """
    if os.environ.get("TERM_PROGRAM") == "gnome-terminal":
        return True
    if os.environ.get("GNOME_TERMINAL_SERVICE"):
        return True
    if os.environ.get("TERM_PROGRAM"):
        return False
    return _parent_process_has_name("gnome-terminal")


def is_inside_konsole() -> bool:
    """Return True if the current process is running inside Konsole.

    Checks multiple methods for detection:
    1. TERM_PROGRAM environment variable
    2. Parent process name (fallback only if TERM_PROGRAM is not set)
    """
    if os.environ.get("TERM_PROGRAM") == "konsole":
        return True
    if os.environ.get("TERM_PROGRAM"):
        return False
    return _parent_process_has_name("konsole")


def is_inside_ptyxis() -> bool:
    """Return True if the current process is running inside Ptyxis.

    Checks multiple methods for detection:
    1. TERM_PROGRAM environment variable
    2. Parent process name (fallback only if TERM_PROGRAM is not set);
       both ``ptyxis`` and the per-tab ``ptyxis-agent`` qualify
    """
    if os.environ.get("TERM_PROGRAM") == "ptyxis":
        return True
    if os.environ.get("TERM_PROGRAM"):
        return False
    return _parent_process_has_name("ptyxis", "ptyxis-agent")


def _parent_process_has_name(*names: str) -> bool:
    """Check if any parent process matches one of the given names."""
    candidates = set(names)
    try:
        pid = os.getppid()
        result = subprocess.run(
            ["ps", "-o", "comm=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode == 0:
            proc_name = result.stdout.strip()
            if proc_name in candidates:
                return True
        for _ in range(3):
            result = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if result.returncode != 0:
                return False
            ppid_str = result.stdout.strip()
            if not ppid_str:
                return False
            if ppid_str == "1":
                return False
            try:
                pid = int(ppid_str)
            except ValueError:
                return False
            result = subprocess.run(
                ["ps", "-o", "comm=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if result.returncode != 0:
                return False
            proc_name = result.stdout.strip()
            if proc_name in candidates:
                return True
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return False


def tmux_new_window(command: list[str], title: str | None = None) -> bool:
    """Open a new tmux window running the given command.

    Returns True if the tmux command succeeded, False otherwise.
    The caller must verify that we are inside tmux before calling this.
    """
    shell_cmd = " ".join(shlex.quote(c) for c in command)
    tmux_cmd: list[str] = ["tmux", "new-window"]
    if title:
        tmux_cmd += ["-n", title]
    tmux_cmd.append(shell_cmd)
    try:
        subprocess.run(tmux_cmd, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def spawn_terminal_with_command(command: list[str], title: str | None = None) -> bool:
    """Spawn a new terminal tab running the given command.

    Only spawns if already running inside a supported terminal emulator.
    Opens a new tab in the existing window.

    Returns True if the terminal was spawned, False if not running inside
    a supported terminal or if the spawn failed.
    """
    shell_cmd = " ".join(shlex.quote(c) for c in command)

    try:
        if is_inside_gnome_terminal():
            args = ["--tab"]
            if title:
                args.extend(["--title", title])
            args.extend(["--", "bash", "-c", shell_cmd])
            subprocess.Popen(
                ["gnome-terminal"] + args,
                start_new_session=True,
            )
            return True
        if is_inside_konsole():
            args = ["--new-tab"]
            if title:
                args.extend(["--title", title])
            args.extend(["-e", "bash", "-c", shell_cmd])
            subprocess.Popen(
                ["konsole"] + args,
                start_new_session=True,
            )
            return True
        if is_inside_ptyxis():
            args = ["--tab"]
            if title:
                args.extend(["--title", title])
            args.extend(["--", "bash", "-c", shell_cmd])
            subprocess.Popen(
                ["ptyxis"] + args,
                start_new_session=True,
            )
            return True
        return False
    except (FileNotFoundError, OSError):
        return False


def is_web_mode() -> bool:
    """Detect textual-serve (web) mode from the environment — pre-app only.

    ``textual-serve`` sets the ``TEXTUAL_DRIVER`` env var to a web
    driver.  This env probe is for callers that run *before* the app
    exists (e.g. the tmux-wrap decision in ``main()``); once the app is
    running, prefer the canonical [`App.is_web`][textual.app.App.is_web].
    """
    driver = os.environ.get("TEXTUAL_DRIVER", "")
    return "web" in driver.lower()


def launch_login(
    command: list[str],
    title: str | None = None,
) -> tuple[str, int | None]:
    """Launch a login session using the best available method.

    Only reached from a local-terminal TUI (the web case is refused
    upstream), so a host terminal is assumed available.  Returns a
    tuple of (method, port):

    - ("tmux", None): opened in a new tmux window
    - ("terminal", None): opened in a new desktop terminal window
    - ("none", None): no external method available; caller should suspend
    """
    if is_inside_tmux() and tmux_new_window(command, title=title):
        return ("tmux", None)
    if spawn_terminal_with_command(command, title=title):
        return ("terminal", None)
    return ("none", None)
