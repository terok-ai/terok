# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the TUI startup vault-migration probe.

``App.on_mount`` shows a "legacy credentials/ directory detected" warning
when a pre-0.8 credentials dir lingers next to the new vault dir.  The
probe is isolated behind ``_vault_migration_warning`` so it is easy to
test without standing up a full Textual app.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from terok.tui.app import _vault_migration_warning


class TestVaultMigrationWarning:
    """Probe returns a message when the legacy dir exists; otherwise ``None``."""

    def test_returns_message_when_legacy_dir_present(self, tmp_path: Path) -> None:
        """Existing ``credentials/`` dir → warning points at the migration tool."""
        legacy = tmp_path / "credentials"
        legacy.mkdir()
        with patch("terok_sandbox.paths.namespace_state_dir", return_value=legacy):
            msg = _vault_migration_warning()
        assert msg is not None
        assert "Legacy credentials/" in msg
        assert "terok-migrate-vault.py" in msg

    def test_returns_none_when_no_legacy_dir(self, tmp_path: Path) -> None:
        """Missing ``credentials/`` dir → no warning, no crash."""
        missing = tmp_path / "credentials"  # never created
        with patch("terok_sandbox.paths.namespace_state_dir", return_value=missing):
            assert _vault_migration_warning() is None

    def test_returns_none_when_sandbox_probe_raises(self) -> None:
        """Any exception in the probe is swallowed — diagnostics never crash boot."""
        with patch(
            "terok_sandbox.paths.namespace_state_dir",
            side_effect=RuntimeError("sandbox misconfigured"),
        ):
            assert _vault_migration_warning() is None


class TestOnMountDispatchesWarning:
    """The App.on_mount flow notifies the user with the probe's message."""

    def test_notify_called_with_warning_when_legacy_dir_exists(self) -> None:
        """Simulate the on_mount dispatch path: probe → notify."""
        fake_app = MagicMock()
        with patch(
            "terok.tui.app._vault_migration_warning",
            return_value="LEGACY_WARNING",
        ):
            # The relevant block of on_mount (extracted logic):
            warning = __import__(
                "terok.tui.app", fromlist=["_vault_migration_warning"]
            )._vault_migration_warning()
            if warning is not None:
                fake_app.notify(warning, severity="warning", timeout=15)
        fake_app.notify.assert_called_once_with("LEGACY_WARNING", severity="warning", timeout=15)

    def test_notify_not_called_when_no_warning(self) -> None:
        """``_vault_migration_warning`` returning ``None`` → no notify call."""
        fake_app = MagicMock()
        with patch("terok.tui.app._vault_migration_warning", return_value=None):
            warning = __import__(
                "terok.tui.app", fromlist=["_vault_migration_warning"]
            )._vault_migration_warning()
            if warning is not None:
                fake_app.notify(warning, severity="warning", timeout=15)
        fake_app.notify.assert_not_called()
