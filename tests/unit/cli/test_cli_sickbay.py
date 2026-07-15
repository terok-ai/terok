# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the sickbay CLI command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from terok.cli.commands.sickbay import (
    _check_shield,
    _check_vault,
)
from tests.testfs import MOCK_BASE

MOCK_VAULT_DB = MOCK_BASE / "vault" / "credentials.db"


def _make_vault_snapshot(
    *,
    state: str = "unlocked",
    source: str | None = "keyring",
    providers: tuple[str, ...] | None = ("claude", "gh"),
    warnings: tuple[object, ...] = (),
    lock_reason: str | None = None,
    db_error: str | None = None,
) -> MagicMock:
    """Return a mock of the sandbox-owned ``VaultStatus`` snapshot."""
    from terok.lib.api.vault import VaultState

    s = MagicMock()
    s.state = VaultState(state)
    s.source = source
    s.providers = providers
    s.warnings = warnings
    s.lock_reason = lock_reason
    s.db_error = db_error
    return s


@pytest.mark.parametrize(
    ("health", "setup_hint", "issues", "side_effect", "expected_status", "expected_detail"),
    [
        pytest.param(
            "bypass",
            "",
            [],
            None,
            "warn",
            "bypass_firewall_no_protection",
            id="bypass",
        ),
        pytest.param(
            "stale-hooks",
            "",
            [],
            None,
            "warn",
            "hooks outdated",
            id="stale-hooks",
        ),
        pytest.param(
            "setup-needed",
            "run 'terok shield install-hooks --user'",
            ["nft not found"],
            None,
            "warn",
            "nft not found",
            id="setup-needed-with-hint",
        ),
        pytest.param(
            "setup-needed",
            "",
            [],
            None,
            "warn",
            "setup needed",
            id="setup-needed-no-hint",
        ),
        pytest.param(
            None,
            "",
            [],
            RuntimeError("nft binary not found"),
            "warn",
            "check failed",
            id="check-exception",
        ),
        pytest.param(
            "ok",
            "",
            [],
            None,
            "ok",
            "active",
            id="ok",
        ),
    ],
)
def test_check_shield_states(
    health: str | None,
    setup_hint: str,
    issues: list[str],
    side_effect: Exception | None,
    expected_status: str,
    expected_detail: str,
) -> None:
    """_check_shield maps EnvironmentCheck states to the correct severity and message."""
    mock_ec = MagicMock(
        health=health,
        hooks="per-container",
        dns_tier="dnsmasq",
        setup_hint=setup_hint,
        issues=issues,
    )
    with patch(
        "terok.cli.commands.sickbay.check_environment",
        return_value=mock_ec,
        side_effect=side_effect,
    ):
        status, label, detail = _check_shield()

    assert status == expected_status
    assert label == "Shield"
    assert expected_detail in detail


def test_check_shield_surfaces_apparmor_advisory_on_lower_tier() -> None:
    """On a dnsmasq→dig downgrade, _check_shield surfaces shield's precise reason."""
    advisory = (
        "dnsmasq is present but AppArmor confines it from the shield state "
        "directory — install the terok AppArmor profile (see docs/apparmor.md)"
    )
    mock_ec = MagicMock(
        health="ok", hooks="per-container", dns_tier="dig", setup_hint="", issues=[advisory]
    )
    with patch("terok.cli.commands.sickbay.check_environment", return_value=mock_ec):
        status, _label, detail = _check_shield()
    assert status == "ok"
    assert "AppArmor" in detail
    assert "docs/apparmor.md" in detail
    assert "install dnsmasq for live IP rotation" not in detail


def test_check_shield_generic_dnsmasq_hint_without_reported_reason() -> None:
    """With no shield-reported reason, the generic dnsmasq hint is still shown."""
    mock_ec = MagicMock(
        health="ok", hooks="per-container", dns_tier="dig", setup_hint="", issues=[]
    )
    with patch("terok.cli.commands.sickbay.check_environment", return_value=mock_ec):
        status, _label, detail = _check_shield()
    assert status == "ok"
    assert "install dnsmasq" in detail


@pytest.mark.parametrize(
    ("snapshot_kwargs", "side_effect", "expected_status", "expected_detail"),
    [
        pytest.param(
            {"providers": ("claude", "gh")},
            None,
            "ok",
            "2 credential(s)",
            id="unlocked-with-creds",
        ),
        pytest.param(
            {"state": "locked", "source": None, "providers": None},
            None,
            "warn",
            "locked",
            id="locked",
        ),
        pytest.param(
            {"state": "unprovisioned", "source": None, "providers": None},
            None,
            "warn",
            "not set up yet",
            id="unprovisioned",
        ),
        pytest.param(
            {"state": "error", "providers": None, "db_error": "schema drift"},
            None,
            "warn",
            "DB error",
            id="db-error",
        ),
        pytest.param(
            {},
            OSError("db gone"),
            "warn",
            "check failed",
            id="check-exception",
        ),
    ],
)
def test_check_vault_states(
    snapshot_kwargs: dict[str, object],
    side_effect: Exception | None,
    expected_status: str,
    expected_detail: str,
) -> None:
    """_check_vault maps DB-side facts to the correct severity and message."""
    snapshot = _make_vault_snapshot(**snapshot_kwargs)
    with patch(
        "terok.cli.commands.sickbay.load_vault_status",
        return_value=snapshot,
        side_effect=side_effect,
    ):
        status, label, detail = _check_vault()

    assert status == expected_status
    assert label == "Vault"
    assert expected_detail in detail
