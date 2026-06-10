# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Vault status snapshot and DB access — public API surface.

Every container's supervisor embeds its own vault proxy, so the vault
has no host-side daemon to start or stop and no socket state to roll
up.  Status surfaces on the host reduce to DB-side facts: passphrase
tier (via [`RecoveryStatus`][terok_sandbox.RecoveryStatus]), stored
credentials (via the [`CredentialDB`][terok_sandbox.CredentialDB]
opened by [`vault_db`][terok.lib.domain.vault.vault_db]).

[`VaultStatusSnapshot`][terok.lib.api.vault.VaultStatusSnapshot]
bundles those facts into one immutable value the TUI / CLI render,
local to terok.

The passphrase-management verbs ``vault seal`` and ``vault
to-keyring`` are operator-driven and ship from the sandbox CLI;
the matching handler entrypoints are re-exported here for the TUI
worker actions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from terok.lib.domain.vault import vault_db  # noqa: F401 — re-exported public API
from terok.lib.integrations.sandbox import (  # noqa: F401 — re-exported public API
    NoPassphraseError,
    RecoveryStatus,
    WrongPassphraseError,
    handle_vault_seal,
    handle_vault_to_keyring,
    provision_session_passphrase,
    purge_passphrase_tiers,
)


@dataclass(frozen=True)
class VaultStatusSnapshot:
    """Host-side view of the vault store.

    This captures the store's content (credentials + SSH keys,
    passphrase tier, plaintext-on-disk marker) — the host-level
    questions the operator asks: "is the vault locked?" and "what's
    in it?".  Wire shape (socket, transport, ports) is per-container
    and composed inside the supervisor, with no host-level analogue.

    All fields are derived from
    [`RecoveryStatus`][terok_sandbox.RecoveryStatus],
    [`CredentialDB`][terok_sandbox.CredentialDB], and
    [`SandboxConfig`][terok_sandbox.SandboxConfig] — no daemon
    queries, no IPC.
    """

    locked: bool
    """``True`` when no resolver tier currently unlocks the store."""

    passphrase_source: str | None
    """Tier name (``systemd-creds`` / ``keyring`` / ``session-file`` /
    ``config``) or ``None`` when locked."""

    credentials_stored: tuple[str, ...] | None
    """Sorted, deduplicated provider slugs across every credential set,
    or ``None`` when the DB could not be read (locked or errored)."""

    ssh_keys_stored: int | None
    """Count of distinct keypairs stored in the vault, or ``None`` when the
    DB could not be read."""

    plaintext_passphrase_path: str | None
    """Filesystem path of the plaintext-passphrase file when present, else ``None``."""

    db_path: str
    """Filesystem path of the SQLCipher store (display-only)."""

    recovery_acknowledged: bool
    """``True`` when the operator has confirmed the recovery passphrase is saved off-host."""

    db_error: str | None
    """Diagnostic message when the DB couldn't be opened for a reason other
    than 'locked' (schema drift, permission denied, plaintext-DB found, …).
    Renderers should surface this verbatim — it's the actionable signal."""

    lock_reason: str | None = None
    """Why ``locked`` is ``True`` — ``None`` when unlocked.

    "Locked" hides three different operator problems with three
    different remedies: *no passphrase in any tier* (provision one),
    *the resolved value doesn't open the DB* (typo / DB from another
    install — re-enter the right one), and *a configured tier is
    unreadable* (broken systemd-creds seal after a machine change,
    dead ``passphrase_command`` — fix or purge the tier).  Renderers
    append this to the bare "locked" so the operator isn't left
    guessing which of the three they're in."""

    credential_types: Mapping[str, str] = field(default_factory=dict)
    """Mapping ``provider → type`` (``api_key`` / ``oauth_token`` / …),
    populated by the same DB pass that built ``credentials_stored`` so
    renderers don't need to reopen the DB just to look up a type.
    Wrapped in a read-only proxy by ``load()`` so the public snapshot
    can't be mutated in place by callers."""

    @property
    def session_only_passphrase(self) -> bool:
        """``True`` when the passphrase lives only in the session-unlock tmpfs file."""
        # "session-file" is a passphrase-tier label, not a secret.
        return not self.locked and self.passphrase_source == "session-file"  # nosec B105

    @classmethod
    def load(cls) -> VaultStatusSnapshot:
        """Open the DB if unlockable and assemble the snapshot.

        The single ``cfg`` is shared between ``RecoveryStatus.load`` and
        ``vault_db`` so the passphrase tier is resolved exactly once —
        preventing snapshots that report contradictory
        ``(locked, passphrase_source)`` pairs when host state changes
        mid-load.
        """
        from terok.lib.core.config import make_sandbox_config
        from terok.lib.domain.vault import vault_db
        from terok.lib.integrations.sandbox import plaintext_passphrase_config_path

        cfg = make_sandbox_config()
        recovery = RecoveryStatus.load(cfg)
        plaintext = plaintext_passphrase_config_path()
        plaintext_str = str(plaintext) if plaintext is not None else None

        credentials: tuple[str, ...] | None = None
        types: dict[str, str] = {}
        ssh_count: int | None = None
        # Classify rather than conflate: ``lock_reason`` separates the
        # three passphrase problems "locked" used to hide, while DB-open
        # failures for non-passphrase reasons (schema drift, permission
        # denied, corruption) stay on ``db_error`` — renderers gate
        # behaviour on ``locked`` and must not misread a broken DB as a
        # locked one (or vice versa).  Wording mirrors sandbox's
        # ``vault status`` classifier so both surfaces tell one story.
        lock_reason: str | None = None
        db_error: str | None = None
        if recovery.resolve_error is not None:
            lock_reason = f"a configured tier is unreadable — {recovery.resolve_error}"
        elif recovery.source is None:
            lock_reason = "no passphrase in any tier"
        else:
            try:
                with vault_db(cfg=cfg) as db:
                    for cs in db.list_credential_sets():
                        for provider in db.list_credentials(cs):
                            row = db.load_credential(cs, provider)
                            types.setdefault(
                                provider,
                                row.get("type", "unknown") if row else "unknown",
                            )
                    credentials = tuple(sorted(types))
                    ssh_count = db.count_ssh_keys()
            except NoPassphraseError:
                # Tier vanished between the resolve and the open — plain lock.
                lock_reason = "no passphrase in any tier"
            except WrongPassphraseError:
                lock_reason = (
                    f"the passphrase via {recovery.source} does not open the DB"
                    " — wrong key, or a DB from another install"
                )
            except Exception as exc:  # noqa: BLE001 — surface every DB-open failure to the operator
                db_error = str(exc)

        return cls(
            locked=lock_reason is not None,
            lock_reason=lock_reason,
            passphrase_source=recovery.source,
            credentials_stored=credentials,
            credential_types=MappingProxyType(types),
            ssh_keys_stored=ssh_count,
            plaintext_passphrase_path=plaintext_str,
            db_path=str(cfg.db_path),
            recovery_acknowledged=recovery.acknowledged,
            db_error=db_error,
        )


__all__ = [
    "NoPassphraseError",
    "RecoveryStatus",
    "VaultStatusSnapshot",
    "WrongPassphraseError",
    "handle_vault_seal",
    "handle_vault_to_keyring",
    "provision_session_passphrase",
    "purge_passphrase_tiers",
    "vault_db",
]
