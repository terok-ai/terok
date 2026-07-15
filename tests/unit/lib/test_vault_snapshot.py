# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``load_vault_status`` — the one terok-side vault-snapshot contract.

The snapshot itself ([`VaultStatus`][terok_sandbox.VaultStatus]:
classification, chain, warning catalog) is sandbox-owned and tested
there; terok's only added behavior is loading it under the
orchestrator's effective config instead of sandbox's bare defaults.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from terok.lib.api.vault import load_vault_status


class TestLoadVaultStatus:
    """The loader threads terok's ``make_sandbox_config`` into ``VaultStatus.load``."""

    def test_loads_under_terok_effective_config(self) -> None:
        sentinel_cfg = MagicMock(name="terok-sandbox-config")
        sentinel_status = MagicMock(name="vault-status")
        with (
            patch(
                "terok.lib.core.config.make_sandbox_config", return_value=sentinel_cfg
            ) as make_cfg,
            # Patch the adapter attribute, not the wheel: the PEP 562
            # re-export caches the resolved symbol in the adapter's
            # globals on first access, so a wheel-level patch is
            # invisible once any earlier test touched the name.
            patch("terok.lib.integrations.sandbox.VaultStatus") as status_cls,
        ):
            status_cls.load.return_value = sentinel_status
            assert load_vault_status() is sentinel_status
        make_cfg.assert_called_once_with()
        status_cls.load.assert_called_once_with(sentinel_cfg)
