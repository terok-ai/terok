# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the sickbay CLI command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from terok_sandbox import GateServerStatus

from terok.cli.commands.sickbay import (
    _check_credential_proxy,
    _check_keyring,
    _check_shield,
    _cmd_sickbay,
    _find_containers_conf,
)
from tests.testfs import MOCK_BASE
from tests.testgate import OUTDATED_UNITS_MESSAGE, make_gate_server_status

MOCK_PROXY_SOCKET = MOCK_BASE / "run" / "credential-proxy.sock"
MOCK_PROXY_DB = MOCK_BASE / "proxy" / "credentials.db"


def _make_proxy_status(
    *, running: bool = True, mode: str = "systemd", transport: str | None = "tcp"
) -> MagicMock:
    """Return a mock CredentialProxyStatus."""
    s = MagicMock()
    s.running = running
    s.mode = mode
    s.transport = transport
    s.socket_path = MOCK_PROXY_SOCKET
    s.db_path = MOCK_PROXY_DB
    s.credentials_stored = ["claude", "gh"]
    return s


@pytest.mark.parametrize(
    ("status", "outdated", "systemd_available", "exit_code", "expected"),
    [
        pytest.param(
            make_gate_server_status("systemd", running=True, transport="tcp"),
            None,
            False,
            None,
            ["Gate server", "ok", "systemd"],
            id="all-ok",
        ),
        pytest.param(
            make_gate_server_status("systemd", running=True),
            OUTDATED_UNITS_MESSAGE,
            False,
            1,
            ["WARN", "outdated", "terok gate start"],
            id="outdated-units",
        ),
        pytest.param(
            make_gate_server_status("none"),
            None,
            True,
            1,
            ["WARN", "gate start"],
            id="not-running-with-systemd",
        ),
        pytest.param(
            make_gate_server_status("none"),
            None,
            False,
            1,
            ["WARN", "gate start"],
            id="not-running-without-systemd",
        ),
        pytest.param(
            make_gate_server_status("systemd"),
            None,
            False,
            2,
            ["ERROR", "not active"],
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

    # Keyring check needs a containers.conf with keyring = false
    keyring_conf = tmp_path / "containers.conf"
    keyring_conf.write_text("[containers]\nkeyring = false\n")

    mock_ec = MagicMock(health="ok", hooks="per-container", dns_tier="dnsmasq")
    mock_cfg = MagicMock()
    mock_cfg.return_value.ssh_keys_json_path = ssh_keys
    with (
        patch("terok.cli.commands.sickbay.get_server_status", return_value=status),
        patch("terok.cli.commands.sickbay.check_units_outdated", return_value=outdated),
        patch("terok.cli.commands.sickbay.is_systemd_available", return_value=systemd_available),
        patch("terok.cli.commands.sickbay.check_environment", return_value=mock_ec),
        patch("terok.cli.commands.sickbay.get_proxy_status", return_value=_make_proxy_status()),
        patch("terok.cli.commands.sickbay.is_proxy_systemd_available", return_value=False),
        patch("terok.cli.commands.sickbay.get_services_mode", return_value="tcp"),
        patch("terok.cli.commands.sickbay.make_sandbox_config", mock_cfg),
        patch("terok.cli.commands.sickbay._find_containers_conf", return_value=keyring_conf),
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
            "run 'terok shield setup --user'",
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
def test_check_credential_proxy_states(
    running: bool,
    mode: str,
    systemd_avail: bool,
    side_effect: Exception | None,
    expected_status: str,
    expected_detail: str,
) -> None:
    """_check_credential_proxy maps proxy states to the correct severity and message."""
    with (
        patch(
            "terok.cli.commands.sickbay.get_proxy_status",
            return_value=_make_proxy_status(running=running, mode=mode),
            side_effect=side_effect,
        ),
        patch(
            "terok.cli.commands.sickbay.is_proxy_systemd_available",
            return_value=systemd_avail,
        ),
    ):
        status, label, detail = _check_credential_proxy()

    assert status == expected_status
    assert label == "Credential proxy"
    assert expected_detail in detail


class TestCheckKeyring:
    """Verify _check_keyring diagnostics."""

    def test_disabled(self, tmp_path: Path) -> None:
        """keyring = false → ok."""
        conf = tmp_path / "containers.conf"
        conf.write_text("[containers]\nkeyring = false\n")
        with patch("terok.cli.commands.sickbay._find_containers_conf", return_value=conf):
            sev, label, detail = _check_keyring()
        assert sev == "ok"
        assert label == "Keyring"
        assert "disabled" in detail

    def test_enabled_explicitly(self, tmp_path: Path) -> None:
        """keyring = true → warn with docs link."""
        conf = tmp_path / "containers.conf"
        conf.write_text("[containers]\nkeyring = true\n")
        with patch("terok.cli.commands.sickbay._find_containers_conf", return_value=conf):
            sev, label, detail = _check_keyring()
        assert sev == "warn"
        assert "kernel-keyring" in detail

    def test_absent_defaults_to_warn(self, tmp_path: Path) -> None:
        """No [containers] section → warn (default is true)."""
        conf = tmp_path / "containers.conf"
        conf.write_text("[engine]\nevents_logger = 'file'\n")
        with patch("terok.cli.commands.sickbay._find_containers_conf", return_value=conf):
            sev, _, detail = _check_keyring()
        assert sev == "warn"
        assert "not disabled" in detail

    def test_no_conf_file(self) -> None:
        """No containers.conf found → warn."""
        with patch("terok.cli.commands.sickbay._find_containers_conf", return_value=None):
            sev, _, detail = _check_keyring()
        assert sev == "warn"
        assert "no containers.conf" in detail

    def test_corrupt_toml(self, tmp_path: Path) -> None:
        """Corrupt TOML → warn with parse error."""
        conf = tmp_path / "containers.conf"
        conf.write_text("[bad toml\n")
        with patch("terok.cli.commands.sickbay._find_containers_conf", return_value=conf):
            sev, _, detail = _check_keyring()
        assert sev == "warn"
        assert "cannot parse" in detail

    def test_non_dict_containers_section(self, tmp_path: Path) -> None:
        """Non-table [containers] value → warn (treated as keyring enabled)."""
        conf = tmp_path / "containers.conf"
        conf.write_text('containers = "not a table"\n')
        with patch("terok.cli.commands.sickbay._find_containers_conf", return_value=conf):
            sev, _, detail = _check_keyring()
        assert sev == "warn"
        assert "not disabled" in detail


class TestFindContainersConf:
    """Verify _find_containers_conf lookup logic."""

    def test_env_var_valid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """$CONTAINERS_CONF pointing to a real file → returns that path."""
        conf = tmp_path / "custom.conf"
        conf.write_text("[containers]\n")
        monkeypatch.setenv("CONTAINERS_CONF", str(conf))
        assert _find_containers_conf() == conf

    def test_env_var_missing_falls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """$CONTAINERS_CONF pointing to non-existent file → falls back to standard paths."""
        monkeypatch.setenv("CONTAINERS_CONF", str(tmp_path / "ghost.conf"))
        fallback = tmp_path / "containers.conf"
        fallback.write_text("[containers]\nkeyring = false\n")
        with patch("terok.cli.commands.sickbay._CONTAINERS_CONF_PATHS", (fallback,)):
            result = _find_containers_conf()
        assert result == fallback

    def test_no_env_no_files(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """No env var, no standard files → None."""
        monkeypatch.delenv("CONTAINERS_CONF", raising=False)
        with patch(
            "terok.cli.commands.sickbay._CONTAINERS_CONF_PATHS",
            (tmp_path / "missing1.conf", tmp_path / "missing2.conf"),
        ):
            assert _find_containers_conf() is None
