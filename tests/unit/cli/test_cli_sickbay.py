# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the sickbay CLI command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from terok_sandbox import GateServerStatus

from terok.cli.commands import sickbay as _sickbay_module
from terok.cli.commands.sickbay import (
    _check_shield,
    _check_vault,
    _cmd_sickbay,
)
from tests.testfs import MOCK_BASE
from tests.testgate import OUTDATED_UNITS_MESSAGE, make_gate_server_status

MOCK_VAULT_SOCKET = MOCK_BASE / "run" / "vault.sock"
MOCK_VAULT_DB = MOCK_BASE / "vault" / "credentials.db"


def _make_vault_status(
    *, running: bool = True, mode: str = "systemd", transport: str | None = "tcp"
) -> MagicMock:
    """Return a mock VaultStatus."""
    s = MagicMock()
    s.running = running
    s.mode = mode
    s.transport = transport
    s.socket_path = MOCK_VAULT_SOCKET
    s.db_path = MOCK_VAULT_DB
    s.credentials_stored = ["claude", "gh"]
    return s


@pytest.mark.parametrize(
    ("status", "outdated", "systemd_available", "exit_code", "expected", "unexpected"),
    [
        pytest.param(
            make_gate_server_status("systemd", running=True, transport="tcp"),
            None,
            False,
            None,
            ["Gate server", "ok", "systemd"],
            [],
            id="all-ok",
        ),
        pytest.param(
            make_gate_server_status("systemd", running=True),
            OUTDATED_UNITS_MESSAGE,
            False,
            1,
            ["WARN", "outdated", "terok gate start"],
            [],
            id="outdated-units",
        ),
        pytest.param(
            make_gate_server_status("none"),
            None,
            True,
            1,
            ["WARN", "gate start"],
            [],
            id="not-running-with-systemd",
        ),
        pytest.param(
            make_gate_server_status("none"),
            None,
            False,
            1,
            ["WARN", "disabled", "no user systemd"],
            # Lock the regression boundary: the old "run terok gate start"
            # hint must NOT appear on no-systemd hosts — installing it
            # wouldn't help, so suggesting it would be actively misleading.
            ["terok gate start"],
            id="not-running-without-systemd",
        ),
        pytest.param(
            make_gate_server_status("systemd"),
            None,
            False,
            2,
            ["ERROR", "not active"],
            [],
            id="socket-inactive",
        ),
    ],
)
def test_cmd_sickbay_reports_health(
    status: GateServerStatus,
    outdated: str | None,
    systemd_available: bool,
    exit_code: int | None,
    expected: list[str],
    unexpected: list[str],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Sickbay exits with warning/error codes and prints useful remediation hints."""
    import json

    # SSH agent check needs a valid ssh-keys.json with an existing key file
    dummy_key = tmp_path / "id"
    dummy_key.write_text("k")
    (tmp_path / "id.pub").write_text("pub")
    ssh_keys = tmp_path / "ssh-keys.json"
    ssh_keys.write_text(
        json.dumps({"p": {"private_key": str(dummy_key), "public_key": str(tmp_path / "id.pub")}})
    )

    mock_ec = MagicMock(health="ok", hooks="per-container", dns_tier="dnsmasq")
    mock_cfg = MagicMock()
    mock_cfg.return_value.ssh_keys_json_path = ssh_keys

    # Stub checks that hit the real filesystem, so the test stays hermetic
    # without masking unrelated lookups.  Each stub reports ok, keeping the
    # fixture narrowly about the gate-server assertions above.
    _stubs = {
        "_check_vault_migration": ("ok", "Vault migration", "no legacy directory"),
        "_check_clearance_hub": (
            "ok",
            "Clearance hub",
            "terok-clearance-hub.service not installed",
        ),
        "_check_clearance_notifier": (
            "ok",
            "Clearance notifier",
            "terok-clearance-notifier.service not installed",
        ),
        # contract covered in test_sickbay.py::TestCheckDefaultAgents
        "_check_default_agents": ("ok", "Default agents", "image.agents = 'all'"),
        # Recovery-key check needs the sandbox marker file present; stub
        # to ok here so the gate-server assertions above stay focused.
        "_check_recovery_acknowledged": (
            "ok",
            "Recovery key acknowledged",
            "recovery key acknowledged",
        ),
    }
    patched_checks = [
        (label, (lambda r=_stubs[fn.__name__]: r) if fn.__name__ in _stubs else fn)
        for label, fn in _sickbay_module._GLOBAL_CHECKS
    ]
    with (
        patch("terok.cli.commands.sickbay._GLOBAL_CHECKS", patched_checks),
        patch("terok.cli.commands.sickbay.GateServerManager.get_status", return_value=status),
        patch(
            "terok.cli.commands.sickbay.GateServerManager.check_units_outdated",
            return_value=outdated,
        ),
        patch(
            "terok.cli.commands.sickbay.GateServerManager.is_systemd_available",
            return_value=systemd_available,
        ),
        # ``_check_gate_server`` now branches on git availability before
        # systemd; pin to "present" so the parametrised cases above
        # exercise the systemd / not-running branches as intended.
        patch("terok.cli.commands.sickbay.shutil.which", return_value="/usr/bin/git"),
        patch("terok.cli.commands.sickbay.check_environment", return_value=mock_ec),
        patch(
            "terok.cli.commands.sickbay.VaultManager.get_status", return_value=_make_vault_status()
        ),
        patch("terok.cli.commands.sickbay.VaultManager.is_systemd_available", return_value=False),
        patch("terok.cli.commands.sickbay.get_services_mode", return_value="tcp"),
        patch("terok.cli.commands.sickbay.make_sandbox_config", mock_cfg),
    ):
        if exit_code is None:
            _cmd_sickbay()
        else:
            with pytest.raises(SystemExit) as exc_info:
                _cmd_sickbay()
            assert exc_info.value.code == exit_code

    output = capsys.readouterr().out
    for needle in expected:
        assert needle in output
    for needle in unexpected:
        assert needle not in output, f"{needle!r} should not appear in {output!r}"


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


@pytest.mark.parametrize(
    ("running", "mode", "systemd_avail", "side_effect", "expected_status", "expected_detail"),
    [
        pytest.param(
            True,
            "systemd",
            False,
            None,
            "ok",
            "2 credential(s)",
            id="running-with-creds",
        ),
        pytest.param(
            False,
            "systemd",
            True,
            None,
            "error",
            "not active",
            id="socket-inactive",
        ),
        pytest.param(
            False,
            "none",
            True,
            None,
            "warn",
            "install",
            id="not-running-systemd-available",
        ),
        pytest.param(
            False,
            "none",
            False,
            None,
            "warn",
            "start",
            id="not-running-no-systemd",
        ),
        pytest.param(
            False,
            "none",
            False,
            OSError("socket gone"),
            "warn",
            "check failed",
            id="check-exception",
        ),
    ],
)
def test_check_vault_states(
    running: bool,
    mode: str,
    systemd_avail: bool,
    side_effect: Exception | None,
    expected_status: str,
    expected_detail: str,
) -> None:
    """_check_vault maps vault states to the correct severity and message."""
    with (
        patch(
            "terok.cli.commands.sickbay.VaultManager.get_status",
            return_value=_make_vault_status(running=running, mode=mode),
            side_effect=side_effect,
        ),
        patch(
            "terok.cli.commands.sickbay.VaultManager.is_systemd_available",
            return_value=systemd_avail,
        ),
        # systemd-idle branch consults is_vault_socket_active(); pin it to
        # False so the test doesn't read host state (the only parametrised
        # case that reaches this branch is ``socket-inactive``).
        patch(
            "terok.cli.commands.sickbay.VaultManager.is_socket_active",
            return_value=False,
        ),
        # Pin services.mode to match the fixture's default ``transport=tcp``
        # so the running-branch mismatch check doesn't fire.  These
        # parametrised cases are about reachability / systemd state, not
        # transport-config consistency.
        patch(
            "terok.cli.commands.sickbay.get_services_mode",
            return_value="tcp",
        ),
    ):
        status, label, detail = _check_vault()

    assert status == expected_status
    assert label == "Vault"
    assert expected_detail in detail
