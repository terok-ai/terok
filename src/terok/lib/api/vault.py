# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Vault status snapshot and DB access — public API surface.

Every container's supervisor embeds its own vault proxy, so the vault
has no host-side daemon to start or stop and no socket state to roll
up.  Status surfaces on the host reduce to DB-side facts: passphrase
tier (via [`RecoveryStatus`][terok_sandbox.RecoveryStatus]), stored
credentials (via the [`CredentialDB`][terok_sandbox.CredentialDB]
opened by [`vault_db`][terok.lib.domain.vault.vault_db]).

[`load_vault_status`][terok.lib.api.vault.load_vault_status] loads
the sandbox-owned [`VaultStatus`][terok_sandbox.VaultStatus] snapshot
(state classification + warning catalog) under terok's effective
config — one immutable value the TUI / CLI render.

The passphrase-management verbs ``vault seal`` and ``vault
to-keyring`` are operator-driven and ship from the sandbox CLI;
the matching handler entrypoints are re-exported here for the TUI
worker actions.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terok.lib.domain.vault import vault_db as vault_db
    from terok.lib.domain.vault_rekey import (
        DbHolder as DbHolder,
        RunningTask as RunningTask,
        find_db_holders as find_db_holders,
        find_running_tasks as find_running_tasks,
        restart_tasks_after_rekey as restart_tasks_after_rekey,
        stop_tasks_for_rekey as stop_tasks_for_rekey,
        terminate_stale_holders as terminate_stale_holders,
        wait_for_db_release as wait_for_db_release,
    )
    from terok.lib.integrations.sandbox import (
        NoPassphraseError as NoPassphraseError,
        PassphraseChangeResult as PassphraseChangeResult,
        PassphraseTier as PassphraseTier,
        ProvisioningPlan as ProvisioningPlan,
        RecoveryStatus as RecoveryStatus,
        SessionProvisionResult as SessionProvisionResult,
        SessionShadow as SessionShadow,
        TierProvisionResult as TierProvisionResult,
        TierRewrite as TierRewrite,
        VaultState as VaultState,
        VaultStatus as VaultStatus,
        VaultWarning as VaultWarning,
        VaultWarningKind as VaultWarningKind,
        WrongPassphraseError as WrongPassphraseError,
        change_passphrase as change_passphrase,
        clear_redundant_session_file as clear_redundant_session_file,
        credentials_provisioned as credentials_provisioned,
        handle_vault_seal as handle_vault_seal,
        handle_vault_to_keyring as handle_vault_to_keyring,
        keyring_backend_available as keyring_backend_available,
        plan_provisioning as plan_provisioning,
        provision_passphrase_tier as provision_passphrase_tier,
        provision_session_passphrase as provision_session_passphrase,
        purge_passphrase_tiers as purge_passphrase_tiers,
        session_shadow_state as session_shadow_state,
        systemd_creds_available as systemd_creds_available,
    )

#: Public name -> defining module (PEP 562 lazy resolution).  Every
#: ``terok_sandbox`` re-export is served on first access, so importing
#: this module (e.g. for [`load_vault_status`][terok.lib.api.vault.load_vault_status])
#: does not pull the sandbox wheel until a sandbox-backed name is touched.
_LAZY: dict[str, str] = {
    "DbHolder": "terok.lib.domain.vault_rekey",
    "NoPassphraseError": "terok.lib.integrations.sandbox",
    "PassphraseChangeResult": "terok.lib.integrations.sandbox",  # nosec: B105 — export-map path, not a secret
    "PassphraseTier": "terok.lib.integrations.sandbox",  # nosec: B105 — export-map path, not a secret
    "ProvisioningPlan": "terok.lib.integrations.sandbox",
    "RecoveryStatus": "terok.lib.integrations.sandbox",
    "RunningTask": "terok.lib.domain.vault_rekey",
    "SessionProvisionResult": "terok.lib.integrations.sandbox",
    "SessionShadow": "terok.lib.integrations.sandbox",
    "TierProvisionResult": "terok.lib.integrations.sandbox",
    "TierRewrite": "terok.lib.integrations.sandbox",
    "VaultState": "terok.lib.integrations.sandbox",
    "VaultStatus": "terok.lib.integrations.sandbox",
    "VaultWarning": "terok.lib.integrations.sandbox",
    "VaultWarningKind": "terok.lib.integrations.sandbox",
    "WrongPassphraseError": "terok.lib.integrations.sandbox",
    "change_passphrase": "terok.lib.integrations.sandbox",  # nosec: B105 — export-map path, not a secret
    "clear_redundant_session_file": "terok.lib.integrations.sandbox",
    "credentials_provisioned": "terok.lib.integrations.sandbox",
    "find_db_holders": "terok.lib.domain.vault_rekey",
    "find_running_tasks": "terok.lib.domain.vault_rekey",
    "handle_vault_seal": "terok.lib.integrations.sandbox",
    "handle_vault_to_keyring": "terok.lib.integrations.sandbox",
    "keyring_backend_available": "terok.lib.integrations.sandbox",
    "plan_provisioning": "terok.lib.integrations.sandbox",
    "provision_passphrase_tier": "terok.lib.integrations.sandbox",  # nosec: B105 — export-map path, not a secret
    "provision_session_passphrase": "terok.lib.integrations.sandbox",
    "purge_passphrase_tiers": "terok.lib.integrations.sandbox",
    "restart_tasks_after_rekey": "terok.lib.domain.vault_rekey",
    "session_shadow_state": "terok.lib.integrations.sandbox",
    "stop_tasks_for_rekey": "terok.lib.domain.vault_rekey",
    "systemd_creds_available": "terok.lib.integrations.sandbox",
    "terminate_stale_holders": "terok.lib.domain.vault_rekey",
    "vault_db": "terok.lib.domain.vault",
    "wait_for_db_release": "terok.lib.domain.vault_rekey",
}


def load_vault_status() -> VaultStatus:
    """Load the sandbox's one-call vault picture under terok's effective config.

    [`VaultStatus`][terok_sandbox.VaultStatus] is the single snapshot
    every surface renders — state classification, lock reason, chain
    table, provider/type/SSH-key inventory, and the shared warning
    catalog — computed sandbox-side so the CLI ``vault status``, the
    TUI pill, and sickbay can never drift apart in wording again.
    This helper only supplies terok's
    [`make_sandbox_config`][terok.lib.core.config.make_sandbox_config]
    so the read happens under the orchestrator's effective
    sub-environment rather than sandbox's bare defaults.
    """
    from terok.lib.core.config import make_sandbox_config
    from terok.lib.integrations.sandbox import VaultStatus

    return VaultStatus.load(make_sandbox_config())


__all__ = [
    "DbHolder",
    "NoPassphraseError",
    "PassphraseChangeResult",
    "PassphraseTier",
    "ProvisioningPlan",
    "RunningTask",
    "TierProvisionResult",
    "TierRewrite",
    "VaultState",
    "VaultStatus",
    "VaultWarning",
    "VaultWarningKind",
    "WrongPassphraseError",
    "change_passphrase",
    "clear_redundant_session_file",
    "credentials_provisioned",
    "find_db_holders",
    "find_running_tasks",
    "handle_vault_seal",
    "handle_vault_to_keyring",
    "keyring_backend_available",
    "load_vault_status",
    "plan_provisioning",
    "provision_passphrase_tier",
    "provision_session_passphrase",
    "purge_passphrase_tiers",
    "restart_tasks_after_rekey",
    "session_shadow_state",
    "stop_tasks_for_rekey",
    "systemd_creds_available",
    "terminate_stale_holders",
    "wait_for_db_release",
]


def __getattr__(name: str) -> object:
    """Resolve a re-exported name to its source module on first access (PEP 562)."""
    try:
        target = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module_path, _, source_name = target.partition(":")
    value = getattr(importlib.import_module(module_path), source_name or name)
    globals()[name] = value  # cache so subsequent lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    """Expose the lazy names to ``dir()`` / autocompletion."""
    return sorted({*globals(), *_LAZY})
