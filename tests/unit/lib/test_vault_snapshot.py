# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``VaultStatusSnapshot.load`` — the per-supervisor host view."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from terok.lib.api.vault import VaultStatusSnapshot


@contextmanager
def _mock_db(db: MagicMock | None):
    """Yield *db* from ``maybe_vault_db`` (or ``None`` for the locked case)."""
    yield db


def _patches(*, db: MagicMock | None, recovery, plaintext):
    """Build the four patches ``VaultStatusSnapshot.load`` reaches for.

    ``load`` does function-local imports — patch each symbol at its
    *source* module so the local ``import`` picks the mock up.
    """
    cfg = MagicMock()
    cfg.db_path = "/var/lib/terok/vault/credentials.db"
    return [
        patch("terok.lib.core.config.make_sandbox_config", return_value=cfg),
        patch("terok.lib.api.vault.RecoveryStatus.load", return_value=recovery),
        patch("terok.lib.domain.vault.maybe_vault_db", side_effect=lambda **_kw: _mock_db(db)),
        patch(
            "terok.lib.integrations.sandbox.plaintext_passphrase_config_path",
            return_value=plaintext,
        ),
    ]


class TestVaultStatusSnapshotLoad:
    """``load()`` bundles DB-side facts into one snapshot value."""

    def test_unlocked_lists_credentials_and_ssh_keys(self) -> None:
        """An unlocked DB surfaces credential providers and SSH key counts."""
        db = MagicMock()
        db.list_credential_sets.return_value = ["default", "work"]
        db.list_credentials.side_effect = lambda cs: (
            ["claude", "openai"] if cs == "default" else ["openai", "anthropic"]
        )
        db.load_credential.side_effect = lambda _cs, name: {"type": f"{name}-type"}
        db.list_scopes_with_ssh_keys.return_value = ["proj-a", "proj-b"]
        db.list_ssh_keys_for_scope.side_effect = lambda _scope: [MagicMock(), MagicMock()]
        recovery = MagicMock(acknowledged=True, source="keyring")

        patches = _patches(db=db, recovery=recovery, plaintext=None)
        for p in patches:
            p.start()
        try:
            snap = VaultStatusSnapshot.load()
        finally:
            for p in reversed(patches):
                p.stop()

        assert snap.locked is False
        assert snap.passphrase_source == "keyring"
        # dedup'd across sets, sorted
        assert snap.credentials_stored == ("anthropic", "claude", "openai")
        assert snap.credential_types == {
            "anthropic": "anthropic-type",
            "claude": "claude-type",
            "openai": "openai-type",
        }
        # two scopes, two keys each → four total
        assert snap.ssh_keys_stored == 4
        assert snap.plaintext_passphrase_path is None
        assert snap.recovery_acknowledged is True
        assert snap.db_error is None

    def test_locked_snapshot_skips_db_calls(self) -> None:
        """A locked DB still returns a snapshot; content fields are ``None``."""
        recovery = MagicMock(acknowledged=False, source=None)
        patches = _patches(db=None, recovery=recovery, plaintext=None)
        for p in patches:
            p.start()
        try:
            snap = VaultStatusSnapshot.load()
        finally:
            for p in reversed(patches):
                p.stop()

        assert snap.locked is True
        assert snap.passphrase_source is None
        assert snap.credentials_stored is None
        assert snap.ssh_keys_stored is None
        assert snap.recovery_acknowledged is False
        assert snap.db_error is None

    def test_plaintext_passphrase_path_surfaced(self) -> None:
        """``plaintext_passphrase_config_path()`` value flows into the snapshot."""
        db = MagicMock()
        db.list_credential_sets.return_value = []
        db.list_scopes_with_ssh_keys.return_value = []
        recovery = MagicMock(acknowledged=True, source="config")
        patches = _patches(
            db=db,
            recovery=recovery,
            plaintext="/etc/terok/passphrase.yml",
        )
        for p in patches:
            p.start()
        try:
            snap = VaultStatusSnapshot.load()
        finally:
            for p in reversed(patches):
                p.stop()

        assert snap.plaintext_passphrase_path == "/etc/terok/passphrase.yml"

    def test_session_only_property_detects_session_file_tier(self) -> None:
        """``session_only_passphrase`` shortcut over ``passphrase_source``."""
        unlocked = VaultStatusSnapshot(
            locked=False,
            passphrase_source="session-file",
            credentials_stored=(),
            ssh_keys_stored=0,
            plaintext_passphrase_path=None,
            db_path="/tmp/db",
            recovery_acknowledged=True,
            db_error=None,
        )
        assert unlocked.session_only_passphrase is True
        # A locked snapshot must NOT report session-only unlock even if
        # the passphrase tier still resolves — the vault is not actually
        # accessible until something unlocks it again.
        locked = VaultStatusSnapshot(
            locked=True,
            passphrase_source="session-file",
            credentials_stored=None,
            ssh_keys_stored=None,
            plaintext_passphrase_path=None,
            db_path="/tmp/db",
            recovery_acknowledged=True,
            db_error=None,
        )
        assert locked.session_only_passphrase is False
