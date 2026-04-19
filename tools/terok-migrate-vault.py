#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Migrate legacy terok state to the current vault layout.

Two independent steps, each idempotent and safe to re-run:

1. **0.7 → 0.8 vault rename.**  Move ``~/.local/share/terok/credentials/`` to
   ``~/.local/share/terok/vault/`` and remove the obsolete
   ``terok-credential-proxy`` systemd units.
2. **0.7.4 → 0.8 SSH-keys-to-DB.**  Walk the per-scope SSH keypairs under
   ``<sandbox-state-dir>/ssh-keys/`` and import them into
   ``<vault-dir>/credentials.db``.  The old directory is renamed to
   ``ssh-keys.migrated`` so nothing is destroyed; the obsolete
   ``vault/ssh-keys.json`` sidecar is removed.

Only the default XDG paths are handled — all known users use the default.

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


def _rename_credentials_to_vault(parent: Path) -> list[str]:
    """Rename the legacy ``credentials/`` dir to ``vault/`` when applicable."""
    old_dir = parent / "credentials"
    new_dir = parent / "vault"
    if not old_dir.is_dir():
        return []
    if new_dir.is_dir():
        raise SystemExit(f"Both {old_dir} and {new_dir} exist — merge manually, then re-run.")
    shutil.move(str(old_dir), str(new_dir))
    return [f"Moved {old_dir} → {new_dir}"]


def _default_sandbox_state_dir() -> Path:
    """Return the default sandbox state directory ( ~/.local/share/terok/sandbox )."""
    return _default_vault_parent() / "sandbox"


def _import_ssh_keys_to_db(state_dir: Path, vault_dir: Path) -> list[str]:
    """Import every on-disk scope keypair into ``vault/credentials.db``.

    No-op when the legacy ``ssh-keys/`` directory is absent.  On success,
    the directory is renamed to ``ssh-keys.migrated`` so a botched run
    can be retried without touching the source material.
    """
    legacy_dir = state_dir / "ssh-keys"
    if not legacy_dir.is_dir() or not any(legacy_dir.iterdir()):
        return []

    try:
        from terok_sandbox import CredentialDB, import_ssh_keypair
    except ImportError as exc:
        raise SystemExit(
            f"terok-sandbox is not importable ({exc}) — upgrade the package first, "
            "then re-run this script."
        )

    vault_dir.mkdir(parents=True, exist_ok=True)
    db = CredentialDB(vault_dir / "credentials.db")
    actions: list[str] = []
    try:
        for scope_dir in sorted(p for p in legacy_dir.iterdir() if p.is_dir()):
            scope = scope_dir.name
            for pub in sorted(scope_dir.glob("*.pub")):
                priv = pub.with_suffix("")
                if not priv.is_file():
                    continue
                try:
                    result = import_ssh_keypair(db, scope, priv, pub_path=pub)
                except Exception as exc:  # noqa: BLE001 — surface, don't abort the loop
                    actions.append(f"[skip] {scope}/{priv.name}: {exc}")
                    continue
                state = "already in DB" if result.already_present else "imported"
                actions.append(f"[ ok ] {scope}/{priv.name}: {state}")
    finally:
        db.close()

    archived = legacy_dir.with_suffix(".migrated")
    legacy_dir.rename(archived)
    actions.append(f"Archived {legacy_dir} → {archived}")

    json_sidecar = vault_dir / "ssh-keys.json"
    if json_sidecar.is_file():
        json_sidecar.unlink()
        actions.append(f"Removed obsolete {json_sidecar}")
    return actions


def main() -> int:
    """Run every migration step that applies to this install."""
    parent = _default_vault_parent()
    vault_dir = parent / "vault"
    state_dir = _default_sandbox_state_dir()

    print("terok state migration")
    print(f"  Data parent:       {parent}")
    print(f"  Sandbox state dir: {state_dir}")
    print()

    if os.getenv("TEROK_CREDENTIALS_DIR"):
        print(
            "WARNING: TEROK_CREDENTIALS_DIR is set in your environment.\n"
            "         Rename it to TEROK_VAULT_DIR in your shell profile.\n"
        )

    did_anything = False

    rename_actions = _rename_credentials_to_vault(parent)
    for action in rename_actions:
        print(action)
    if rename_actions:
        did_anything = True
        for action in _remove_old_systemd_units():
            print(f"  {action}")

    ssh_actions = _import_ssh_keys_to_db(state_dir, vault_dir)
    if ssh_actions:
        did_anything = True
        print()
        print("SSH keys → credential DB:")
        for action in ssh_actions:
            print(f"  {action}")

    if not did_anything:
        print("Nothing to migrate — install is already on the current layout.")
        return 0

    print()
    print("Migration complete. Next steps:")
    print("  1. Run 'terok vault install' to set up / refresh systemd units")
    print("  2. Run 'terok vault start' to (re)start the vault")
    if os.getenv("TEROK_CREDENTIALS_DIR"):
        print("  3. Update TEROK_CREDENTIALS_DIR → TEROK_VAULT_DIR in your shell profile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
