# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``VaultStatusSnapshot.load`` — the per-supervisor host view."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from terok.lib.api.vault import VaultStatusSnapshot
from terok.lib.integrations.sandbox import NoPassphraseError, WrongPassphraseError


@contextmanager
def _mock_db(db: MagicMock | None):
    """Yield *db* from ``vault_db``; raise ``NoPassphraseError`` for the locked case."""
    if db is None:
        raise NoPassphraseError("no SQLCipher passphrase available")
    yield db


def _recovery(
    *, acknowledged: bool = False, source: str | None = None, resolve_error: str | None = None
) -> MagicMock:
    """A ``RecoveryStatus`` stand-in with every classifier-read field pinned.

    ``resolve_error`` must be explicit — a bare ``MagicMock`` attribute is
    truthy and would silently route ``load()`` into the broken-tier branch.
    """
    return MagicMock(acknowledged=acknowledged, source=source, resolve_error=resolve_error)


def _patches(*, db, recovery, plaintext):
    """Build the four patches ``VaultStatusSnapshot.load`` reaches for.

    ``load`` does function-local imports — patch each symbol at its
    *source* module so the local ``import`` picks the mock up.  *db* may
    be a MagicMock (unlocked), ``None`` (open raises NoPassphraseError),
    or an exception instance (open raises it).
    """
    cfg = MagicMock()
    cfg.db_path = "/var/lib/terok/vault/credentials.db"

    if isinstance(db, BaseException):

        def _opener(**_kw):
            raise db
    else:

        def _opener(**_kw):
            return _mock_db(db)

    return [
        patch("terok.lib.core.config.make_sandbox_config", return_value=cfg),
        patch("terok.lib.api.vault.RecoveryStatus.load", return_value=recovery),
        patch("terok.lib.domain.vault.vault_db", side_effect=_opener),
        patch(
            "terok.lib.integrations.sandbox.plaintext_passphrase_config_path",
            return_value=plaintext,
        ),
    ]


def _load_with(*, db, recovery, plaintext=None) -> VaultStatusSnapshot:
    """Run ``VaultStatusSnapshot.load`` under the standard patch set."""
    patches = _patches(db=db, recovery=recovery, plaintext=plaintext)
    for p in patches:
        p.start()
    try:
        return VaultStatusSnapshot.load()
    finally:
        for p in reversed(patches):
            p.stop()


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
        db.count_ssh_keys.return_value = 3

        snap = _load_with(db=db, recovery=_recovery(acknowledged=True, source="keyring"))

        assert snap.locked is False
        assert snap.lock_reason is None
        assert snap.passphrase_source == "keyring"
        # dedup'd across sets, sorted
        assert snap.credentials_stored == ("anthropic", "claude", "openai")
        assert snap.credential_types == {
            "anthropic": "anthropic-type",
            "claude": "claude-type",
            "openai": "openai-type",
        }
        # distinct keypairs stored in the vault
        assert snap.ssh_keys_stored == 3
        assert snap.plaintext_passphrase_path is None
        assert snap.recovery_acknowledged is True
        assert snap.db_error is None

    def test_locked_snapshot_skips_db_calls(self) -> None:
        """A locked DB still returns a snapshot; content fields are ``None``."""
        snap = _load_with(db=None, recovery=_recovery())

        assert snap.locked is True
        assert snap.lock_reason == "no passphrase in any tier"
        assert snap.passphrase_source is None
        assert snap.credentials_stored is None
        assert snap.ssh_keys_stored is None
        assert snap.recovery_acknowledged is False
        assert snap.db_error is None

    def test_wrong_passphrase_names_the_tier(self) -> None:
        """A resolved value the DB rejects is a *named* lock, not a silent one."""
        snap = _load_with(
            db=WrongPassphraseError("could not decrypt"),
            recovery=_recovery(source="session-file"),
        )

        assert snap.locked is True
        assert snap.lock_reason is not None
        assert "via session-file does not open the DB" in snap.lock_reason
        assert snap.db_error is None

    def test_broken_tier_reports_resolve_error(self) -> None:
        """A fail-closed resolver (broken seal) surfaces its message, skipping the open."""
        snap = _load_with(
            db=None,  # never reached — classification short-circuits before the open
            recovery=_recovery(resolve_error="sealed credential could not be unsealed"),
        )

        assert snap.locked is True
        assert snap.lock_reason is not None
        assert "a configured tier is unreadable" in snap.lock_reason
        assert "could not be unsealed" in snap.lock_reason

    def test_other_open_failure_is_db_error_not_lock(self) -> None:
        """Schema drift / permissions land on ``db_error``; the vault is not 'locked'."""
        snap = _load_with(db=RuntimeError("schema drift"), recovery=_recovery(source="config"))

        assert snap.locked is False
        assert snap.lock_reason is None
        assert snap.db_error == "schema drift"

    def test_plaintext_passphrase_path_surfaced(self) -> None:
        """``plaintext_passphrase_config_path()`` value flows into the snapshot."""
        db = MagicMock()
        db.list_credential_sets.return_value = []
        db.list_scopes_with_ssh_keys.return_value = []
        snap = _load_with(
            db=db,
            recovery=_recovery(acknowledged=True, source="config"),
            plaintext="/etc/terok/passphrase.yml",
        )

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
