#!/bin/sh
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0
#
# Ptyxis launcher shim — opens the host command in a fresh Ptyxis
# window with the normal UI (container tabs, sidebar) instead of the
# standalone single-command mode that GIO's `Terminal=true` path would
# produce on Fedora.  Used only when the Terok .desktop launcher's
# install-time gate fires (`ptyxis` on PATH).  Background and rationale:
# see the Ptyxis-shim section of src/terok/cli/commands/_desktop_entry.py.
#
# Runtime degradation: install-time presence of ptyxis is not enough —
# the operator may uninstall it later without re-running `terok setup`,
# so the launcher would invoke this shim against a missing binary.
# Cascade: ptyxis → xdg-terminal-exec → hard failure with a desktop
# notification (gnome-shell-launched processes have stderr routed to
# the journal, so an unsurfaced failure looks like nothing happened).

_notify() {
    msg=$1
    if command -v notify-send >/dev/null 2>&1; then
        notify-send --app-name=Terok --icon=terok --urgency=critical \
            'Terok launcher' "$msg" 2>/dev/null || true
    fi
    printf 'terok-ptyxis-shim: %s\n' "$msg" >&2
    return 0
}

if command -v ptyxis >/dev/null 2>&1; then
    exec ptyxis --new-window --title=Terok -- "$@"
fi

if command -v xdg-terminal-exec >/dev/null 2>&1; then
    exec xdg-terminal-exec "$@"
fi

_notify "Cannot launch Terok: ptyxis is no longer installed and no xdg-terminal-exec fallback is available. Run 'terok setup' again to refresh the launcher."
exit 127
