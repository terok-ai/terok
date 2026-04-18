#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Migrate terok 0.7 credentials layout to 0.8 vault layout.

Moves the default credentials directory to the new vault location and
removes obsolete systemd units.  Only handles the default XDG path —
all known users use the default.

Usage::

    python3 tools/terok-migrate-vault.py
    # or, if installed:
    terok-migrate-vault
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_OLD_SOCKET_UNIT = "terok-credential-proxy.socket"
_OLD_SERVICE_UNIT = "terok-credential-proxy.service"


def _default_vault_parent() -> Path:
    """Return the parent of both old and new vault dirs (XDG data dir)."""
    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "terok"
    return Path.home() / ".local" / "share" / "terok"


def _systemd_user_unit_dir() -> Path:
    """Return the systemd user unit directory."""
    xdg_config = os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg_config) / "systemd" / "user"


def _remove_old_systemd_units() -> list[str]:
    """Stop, disable, and remove old credential proxy systemd units."""
    actions: list[str] = []
    unit_dir = _systemd_user_unit_dir()

    socket_file = unit_dir / _OLD_SOCKET_UNIT
    service_file = unit_dir / _OLD_SERVICE_UNIT

    if not socket_file.is_file() and not service_file.is_file():
        return actions

    # Stop and disable the old socket
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", _OLD_SOCKET_UNIT],
        check=False,
        capture_output=True,
        timeout=10,
    )
    actions.append(f"Disabled and stopped {_OLD_SOCKET_UNIT}")

    for unit_file in (socket_file, service_file):
        if unit_file.is_file():
            unit_file.unlink()
            actions.append(f"Removed {unit_file}")

    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        check=False,
        capture_output=True,
        timeout=10,
    )
    actions.append("Reloaded systemd daemon")
    return actions


def main() -> int:
    """Run the migration."""
    parent = _default_vault_parent()
    old_dir = parent / "credentials"
    new_dir = parent / "vault"

    print("terok 0.7 → 0.8 vault migration")
    print(f"  Old: {old_dir}")
    print(f"  New: {new_dir}")
    print()

    # Check old env var
    if os.getenv("TEROK_CREDENTIALS_DIR"):
        print(
            "WARNING: TEROK_CREDENTIALS_DIR is set in your environment.\n"
            "         Rename it to TEROK_VAULT_DIR in your shell profile.\n"
        )

    if not old_dir.is_dir():
        print("Nothing to migrate — old credentials directory does not exist.")
        return 0

    if new_dir.is_dir():
        print(f"ERROR: New directory already exists: {new_dir}")
        print("       Remove it or merge manually, then re-run.")
        return 1

    # Move the directory
    shutil.move(str(old_dir), str(new_dir))
    print(f"Moved {old_dir} → {new_dir}")

    # Remove old systemd units
    actions = _remove_old_systemd_units()
    for action in actions:
        print(f"  {action}")

    print()
    print("Migration complete. Next steps:")
    print("  1. Run 'terok vault install' to set up new systemd units")
    print("  2. Run 'terok vault start' to start the vault")
    if os.getenv("TEROK_CREDENTIALS_DIR"):
        print("  3. Update TEROK_CREDENTIALS_DIR → TEROK_VAULT_DIR in your shell profile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
