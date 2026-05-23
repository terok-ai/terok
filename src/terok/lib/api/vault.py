# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Vault operations and types — public API surface.

Re-export catalog: every symbol presentation code needs for talking to
the credential vault funnels through here.  Sources:
[`terok.lib.integrations.sandbox`][terok.lib.integrations.sandbox] for
the [`VaultManager`][terok_sandbox.VaultManager] /
[`VaultStatus`][terok_sandbox.VaultStatus] aggregates (terok-sandbox
owns the daemon), and [`terok.lib.domain.vault`][terok.lib.domain.vault]
for the ``vault_db`` context manager that terok layers on top.
"""

from terok.lib.domain.vault import vault_db  # noqa: F401 — re-exported public API
from terok.lib.integrations.sandbox import (  # noqa: F401 — re-exported public API
    NoPassphraseError,
    VaultManager,
    VaultStatus,
    WrongPassphraseError,
    handle_vault_seal,
    handle_vault_to_keyring,
)

__all__ = [
    "NoPassphraseError",
    "VaultManager",
    "VaultStatus",
    "WrongPassphraseError",
    "handle_vault_seal",
    "handle_vault_to_keyring",
    "vault_db",
]
