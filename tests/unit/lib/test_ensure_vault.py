# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ensure_vault() — the reattach-path vault startup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from terok.lib.orchestration.environment import ensure_vault

_CFG = "terok.lib.core.config"
_ENV = "terok.lib.orchestration.environment"


class TestEnsureVault:
    """Verify ensure_vault respects bypass and delegates correctly."""

    def test_noop_when_bypass_enabled(self) -> None:
        """Does nothing when bypass_no_secret_protection is set."""
        with (
            patch(f"{_CFG}.get_vault_bypass", return_value=True),
            patch("terok_sandbox.ensure_vault_reachable") as mock_reach,
        ):
            ensure_vault()
        mock_reach.assert_not_called()

    def test_calls_ensure_vault_reachable(self) -> None:
        """Delegates to sandbox ensure_vault_reachable with correct config."""
        mock_cfg = MagicMock()
        with (
            patch(f"{_CFG}.get_vault_bypass", return_value=False),
            patch(f"{_ENV}.make_sandbox_config", return_value=mock_cfg),
            patch("terok_sandbox.ensure_vault_reachable") as mock_reach,
        ):
            ensure_vault()
        mock_reach.assert_called_once_with(mock_cfg)

    def test_propagates_system_exit(self) -> None:
        """SystemExit from ensure_vault_reachable propagates to caller."""
        with (
            patch(f"{_CFG}.get_vault_bypass", return_value=False),
            patch(f"{_ENV}.make_sandbox_config"),
            patch(
                "terok_sandbox.ensure_vault_reachable",
                side_effect=SystemExit("vault down"),
            ),
        ):
            with pytest.raises(SystemExit, match="vault down"):
                ensure_vault()
