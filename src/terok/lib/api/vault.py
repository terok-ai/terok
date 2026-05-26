# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Vault status snapshot and DB access — public API surface.

In the per-container-supervisor model the vault is **not** a host
daemon anymore — every container's supervisor embeds its own vault
proxy.  There is no ``VaultManager`` to start/stop on the host, no
``VaultStatus`` rolling up daemon socket state, no
``VaultUnreachableError``.  Status surfaces on the host reduce to
DB-side facts: passphrase tier (via
[`RecoveryStatus`][terok_sandbox.RecoveryStatus]), stored credentials
(via the [`CredentialDB`][terok_sandbox.CredentialDB] opened by
[`vault_db`][terok.lib.domain.vault.vault_db]).

[`VaultStatusSnapshot`][terok.lib.api.vault.VaultStatusSnapshot]
bundles those facts into one immutable value the TUI / CLI render —
local to terok, since the sandbox-side ``VaultStatus`` (which mixed
in daemon-mode + socket fields) is gone.

The legacy passphrase-management verbs ``vault seal`` and ``vault
to-keyring`` are still operator-driven and ship from the sandbox CLI;
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
)


@dataclass(frozen=True)
class VaultStatusSnapshot:
    """Host-side view of the vault store, replacing the deleted daemon ``VaultStatus``.

    The pre-supervisor surface fused two unrelated concerns into one
    type: the daemon's wire shape (running, socket, transport, ports)
    and the store's content (credentials + SSH keys, passphrase tier,
    plaintext-on-disk marker).  The first set is now per-container,
    composed inside the supervisor, and has no host-level analogue;
    only the second set survives — the operator still asks "is the
    vault locked?" and "what's in it?" on the host.

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
    """Count of SSH key rows across every scope, or ``None`` when the DB
    could not be read."""

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
        ``maybe_vault_db`` so the passphrase tier is resolved exactly
        once — preventing snapshots that report contradictory
        ``(locked, passphrase_source)`` pairs when host state changes
        mid-load.
        """
        from terok.lib.core.config import make_sandbox_config
        from terok.lib.domain.vault import maybe_vault_db
        from terok.lib.integrations.sandbox import plaintext_passphrase_config_path

        cfg = make_sandbox_config()
        recovery = RecoveryStatus.load(cfg)
        plaintext = plaintext_passphrase_config_path()
        plaintext_str = str(plaintext) if plaintext is not None else None

        credentials: tuple[str, ...] | None = None
        types: dict[str, str] = {}
        ssh_count: int | None = None
        # ``locked`` reflects the absence of any unlock source — only the
        # ``db is None`` branch can prove that.  Other DB-open failures
        # (schema drift, permission denied, corruption) go to ``db_error``;
        # conflating them with "locked" misclassifies the failure for
        # renderers that gate behaviour on ``locked``.
        locked = recovery.source is None
        db_error: str | None = None
        try:
            with maybe_vault_db(cfg=cfg) as db:
                if db is None:
                    locked = True
                else:
                    for cs in db.list_credential_sets():
                        for provider in db.list_credentials(cs):
                            row = db.load_credential(cs, provider)
                            types.setdefault(
                                provider,
                                row.get("type", "unknown") if row else "unknown",
                            )
                    credentials = tuple(sorted(types))
                    # Metadata-only probe avoids materialising plaintext
                    # private keys just to take ``len()``.
                    ssh_count = sum(
                        len(db.list_ssh_keys_for_scope(scope))
                        for scope in db.list_scopes_with_ssh_keys()
                    )
        except Exception as exc:  # noqa: BLE001 — surface every DB-open failure to the operator
            db_error = str(exc)

        return cls(
            locked=locked,
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
    "vault_db",
]
