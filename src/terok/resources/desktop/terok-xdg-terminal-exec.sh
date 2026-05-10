#!/bin/sh
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0
#
# Ptyxis-aware shim around xdg-terminal-exec.  Used only when the Terok
# .desktop launcher's install-time gate fires (both `ptyxis` and
# `xdg-terminal-exec` on PATH).  Background and rationale: see the
# Ptyxis-shim section of src/terok/cli/commands/_desktop_entry.py.

xdg=$(command -v xdg-terminal-exec) || {
    printf 'terok-xdg-terminal-exec: xdg-terminal-exec not on PATH\n' >&2
    exit 127
}

case $("$xdg" --print-id 2>/dev/null) in
    org.gnome.Ptyxis*) exec ptyxis --new-window --title=Terok -- "$@" ;;
    *)                 exec "$xdg" "$@" ;;
esac
