# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for sickbay health checks and hook reconciliation."""

from __future__ import annotations

import contextlib
import unittest.mock
from pathlib import Path

import pytest
from terok_sandbox import SelinuxCheckResult, SelinuxStatus

from terok.cli.commands.sickbay import (
    _check_default_agents,
    _check_gate_server,
    _check_recovery_acknowledged,
    _check_selinux_policy,
    _check_ssh_signer,
    _check_task_hook,
    _check_vault,
    _check_vault_migration,
    _reconcile_post_stop,
)
from terok.lib.util.yaml import dump as yaml_dump

MOCK_BASE = Path("/tmp/terok-testing")


@pytest.fixture()
def task_meta_dir(tmp_path: Path) -> Path:
    """Create a temporary task metadata directory."""
    meta_dir = tmp_path / "tasks"
    meta_dir.mkdir()
    return meta_dir


def _write_meta(meta_dir: Path, tid: str, meta: dict) -> Path:
    """Write task metadata to a YAML file and return the path."""
    p = meta_dir / f"{tid}_meta.yml"
    p.write_text(yaml_dump(meta))
    return p


class TestCheckSshSigner:
    """Verify ``_check_ssh_signer`` diagnostics against the DB-backed vault."""

    @staticmethod
    def _mock_project(pid: str) -> unittest.mock.MagicMock:
        p = unittest.mock.MagicMock()
        p.id = pid
        return p

    def _patch_vault(self, assigned_scopes: list[str]):
        """Patch ``cfg.open_credential_db`` to yield the given assigned scopes."""
        db = unittest.mock.MagicMock()
        db.list_scopes_with_ssh_keys.return_value = assigned_scopes
        return unittest.mock.patch(
            "terok.lib.core.config.make_sandbox_config",
            return_value=unittest.mock.MagicMock(
                open_credential_db=unittest.mock.MagicMock(return_value=db)
            ),
        )

    def test_no_projects(self) -> None:
        """No projects configured → ok (nothing to check)."""
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config"),
            unittest.mock.patch("terok.cli.commands.sickbay.list_projects", return_value=[]),
            self._patch_vault([]),
        ):
            sev, _, detail = _check_ssh_signer()
        assert sev == "ok"
        assert "no projects" in detail

    def test_all_projects_have_keys(self) -> None:
        """Every project has an assignment → ok, N/N."""
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config"),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.list_projects",
                return_value=[self._mock_project("proj")],
            ),
            self._patch_vault(["proj"]),
        ):
            sev, _, detail = _check_ssh_signer()
        assert sev == "ok"
        assert "1/1" in detail

    def test_unregistered_project(self) -> None:
        """Project with no assignment → warn, naming the scope."""
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config"),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.list_projects",
                return_value=[self._mock_project("myproj")],
            ),
            self._patch_vault([]),
        ):
            sev, _, detail = _check_ssh_signer()
        assert sev == "warn"
        assert "myproj" in detail
        assert "0/1" in detail

    def test_custom_scopes_ignored(self) -> None:
        """Non-project scopes with keys don't cover the project's absence."""
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config"),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.list_projects",
                return_value=[self._mock_project("proj")],
            ),
            self._patch_vault(["custom-scope"]),
        ):
            sev, _, detail = _check_ssh_signer()
        assert sev == "warn"
        assert "proj" in detail

    def test_vault_failure_degrades_to_warning(self) -> None:
        """A vault that refuses to open surfaces as a ``warn``, not a crash."""
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config"),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.list_projects",
                return_value=[self._mock_project("proj")],
            ),
            unittest.mock.patch(
                "terok.lib.core.config.make_sandbox_config",
                return_value=unittest.mock.MagicMock(
                    open_credential_db=unittest.mock.MagicMock(
                        side_effect=RuntimeError("db locked")
                    )
                ),
            ),
        ):
            sev, _, detail = _check_ssh_signer()
        assert sev == "warn"
        assert "unreachable" in detail
        assert "db locked" in detail


class TestCheckTaskHook:
    def test_missing_meta_file_returns_none(self, tmp_path: Path) -> None:
        project = unittest.mock.MagicMock()
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=tmp_path
        ):
            assert _check_task_hook("proj", "g2xyz", project, fix=False) is None

    def test_no_mode_returns_none(self, task_meta_dir: Path) -> None:
        _write_meta(task_meta_dir, "g1abc", {"status": "created"})
        project = unittest.mock.MagicMock()
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
        ):
            assert _check_task_hook("proj", "g1abc", project, fix=False) is None

    def test_running_container_returns_none(self, task_meta_dir: Path, mock_runtime) -> None:
        _write_meta(task_meta_dir, "g1abc", {"mode": "cli"})
        project = unittest.mock.MagicMock()
        mock_runtime.container.return_value.state = "running"
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
        ):
            assert _check_task_hook("proj", "g1abc", project, fix=False) is None

    def test_already_fired_returns_none(self, task_meta_dir: Path, mock_runtime) -> None:
        _write_meta(task_meta_dir, "g1abc", {"mode": "cli", "hooks_fired": ["post_stop"]})
        project = unittest.mock.MagicMock()
        mock_runtime.container.return_value.state = "exited"
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
        ):
            assert _check_task_hook("proj", "g1abc", project, fix=False) is None

    def test_unfired_returns_warn(self, task_meta_dir: Path, mock_runtime) -> None:
        _write_meta(task_meta_dir, "g1abc", {"mode": "cli", "hooks_fired": ["post_start"]})
        project = unittest.mock.MagicMock()
        mock_runtime.container.return_value.state = "exited"
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
        ):
            result = _check_task_hook("proj", "g1abc", project, fix=False)
            assert result is not None
            assert result[0] == "warn"
            assert "post_stop" in result[2]

    def test_fix_calls_reconcile(self, task_meta_dir: Path, mock_runtime) -> None:
        _write_meta(task_meta_dir, "g1abc", {"mode": "cli"})
        project = unittest.mock.MagicMock()
        project.hook_post_stop = "echo cleanup"
        project.tasks_root = task_meta_dir.parent
        mock_runtime.container.return_value.state = None
        with (
            unittest.mock.patch(
                "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
            ),
            unittest.mock.patch("terok.cli.commands.sickbay.run_hook") as mock_hook,
        ):
            result = _check_task_hook("proj", "g1abc", project, fix=True)
            assert result is not None
            assert result[0] == "ok"
            assert "reconciled" in result[2]
            mock_hook.assert_called_once()

    def test_bad_metadata_returns_warn(self, task_meta_dir: Path) -> None:
        bad_path = task_meta_dir / "g1abc_meta.yml"
        bad_path.write_bytes(b"\x80\x81\x82")  # invalid UTF-8
        project = unittest.mock.MagicMock()
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
        ):
            result = _check_task_hook("proj", "g1abc", project, fix=False)
            assert result is not None
            assert result[0] == "warn"
            assert "bad metadata" in result[2]


class TestReconcilePostStop:
    def test_success(self, tmp_path: Path) -> None:
        meta_path = tmp_path / "g1abc_meta.yml"
        meta_path.write_text(yaml_dump({"mode": "cli"}))
        project = unittest.mock.MagicMock()
        project.hook_post_stop = "echo done"
        project.tasks_root = tmp_path
        with unittest.mock.patch("terok.cli.commands.sickbay.run_hook"):
            result = _reconcile_post_stop(
                "p", "g1abc", "cli", "c", project, meta_path, "Task p/g1abc"
            )
            assert result[0] == "ok"

    def test_failure(self, tmp_path: Path) -> None:
        meta_path = tmp_path / "g1abc_meta.yml"
        meta_path.write_text(yaml_dump({"mode": "cli"}))
        project = unittest.mock.MagicMock()
        project.hook_post_stop = "exit 1"
        project.tasks_root = tmp_path
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.run_hook", side_effect=RuntimeError("boom")
        ):
            result = _reconcile_post_stop(
                "p", "g1abc", "cli", "c", project, meta_path, "Task p/g1abc"
            )
            assert result[0] == "error"
            assert "boom" in result[2]


class TestCheckVault:
    """Verify _check_vault three-state display."""

    def _make_status(self, **overrides: object) -> unittest.mock.MagicMock:
        """Build a mock VaultStatus with defaults."""
        defaults = {
            "mode": "none",
            "running": False,
            "healthy": False,
            "credentials_stored": (),
            "transport": None,
        }
        defaults.update(overrides)
        return unittest.mock.MagicMock(**defaults)

    def test_running_shows_ok(self) -> None:
        """Service active → ok with credential count and transport."""
        status = self._make_status(
            mode="systemd", running=True, credentials_stored=("claude",), transport="tcp"
        )
        with (
            unittest.mock.patch(
                "terok.cli.commands.sickbay.VaultManager.get_status", return_value=status
            ),
            unittest.mock.patch("terok.cli.commands.sickbay.get_services_mode", return_value="tcp"),
        ):
            sev, _, detail = _check_vault()
        assert sev == "ok"
        assert "1 credential(s)" in detail
        assert "tcp" in detail

    def test_transport_mismatch_warns(self) -> None:
        """Running on TCP when config says socket → warn."""
        status = self._make_status(
            mode="systemd", running=True, credentials_stored=("claude",), transport="tcp"
        )
        with (
            unittest.mock.patch(
                "terok.cli.commands.sickbay.VaultManager.get_status", return_value=status
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.get_services_mode", return_value="socket"
            ),
        ):
            sev, _, detail = _check_vault()
        assert sev == "warn"
        assert "services.mode: socket" in detail

    def test_systemd_socket_active_service_idle(self) -> None:
        """Socket active but service idle → ok with standby message."""
        status = self._make_status(mode="systemd", running=False)
        with (
            unittest.mock.patch(
                "terok.cli.commands.sickbay.VaultManager.get_status", return_value=status
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.VaultManager.is_socket_active", return_value=True
            ),
        ):
            sev, _, detail = _check_vault()
        assert sev == "ok"
        assert "starts on first connection" in detail

    def test_systemd_socket_inactive(self) -> None:
        """Socket installed but inactive → error."""
        status = self._make_status(mode="systemd", running=False)
        with (
            unittest.mock.patch(
                "terok.cli.commands.sickbay.VaultManager.get_status", return_value=status
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.VaultManager.is_socket_active", return_value=False
            ),
        ):
            sev, _, detail = _check_vault()
        assert sev == "error"
        assert "not active" in detail

    def test_not_installed_systemd_available(self) -> None:
        """No proxy, systemd available → warn with install hint."""
        status = self._make_status(mode="none", running=False)
        with (
            unittest.mock.patch(
                "terok.cli.commands.sickbay.VaultManager.get_status", return_value=status
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.VaultManager.is_systemd_available", return_value=True
            ),
        ):
            sev, _, detail = _check_vault()
        assert sev == "warn"
        assert "install" in detail

    def test_not_installed_no_systemd(self) -> None:
        """No proxy, no systemd → warn with start hint."""
        status = self._make_status(mode="none", running=False)
        with (
            unittest.mock.patch(
                "terok.cli.commands.sickbay.VaultManager.get_status", return_value=status
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.VaultManager.is_systemd_available", return_value=False
            ),
        ):
            sev, _, detail = _check_vault()
        assert sev == "warn"
        assert "start" in detail

    def test_exception_returns_warn(self) -> None:
        """Exception during status check → warn."""
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.VaultManager.get_status",
            side_effect=RuntimeError("oops"),
        ):
            sev, _, detail = _check_vault()
        assert sev == "warn"
        assert "oops" in detail


class TestCheckGateServerTransport:
    """Verify gate server transport mismatch detection."""

    def test_tcp_mode_tcp_transport_ok(self) -> None:
        """TCP mode with TCP transport → ok."""
        status = unittest.mock.MagicMock(mode="systemd", running=True, port=9418, transport="tcp")
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config"),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.GateServerManager.get_status", return_value=status
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.GateServerManager.check_units_outdated",
                return_value=None,
            ),
            unittest.mock.patch("terok.cli.commands.sickbay.get_services_mode", return_value="tcp"),
        ):
            sev, _, detail = _check_gate_server()
        assert sev == "ok"
        assert "tcp" in detail

    def test_socket_mode_tcp_transport_warns(self) -> None:
        """Socket mode configured but gate running on TCP → warn."""
        status = unittest.mock.MagicMock(mode="systemd", running=True, port=9418, transport="tcp")
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config"),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.GateServerManager.get_status", return_value=status
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.GateServerManager.check_units_outdated",
                return_value=None,
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.get_services_mode", return_value="socket"
            ),
        ):
            sev, _, detail = _check_gate_server()
        assert sev == "warn"
        assert "services.mode: socket" in detail


class TestCheckGateServerNotRunning:
    """Three distinct "why" branches for a not-running gate."""

    def _stack(self, *, git_path: str | None, systemd_available: bool) -> contextlib.ExitStack:
        """Build a shared patch stack for the not-running-gate branches.

        ``ExitStack`` here so each test reads as one block instead of a
        repeated five-line ``with`` chain.  The returned stack must be
        used as a context manager by the caller.
        """
        status = unittest.mock.MagicMock(mode="none", running=False, port=None, transport=None)
        stack = contextlib.ExitStack()
        stack.enter_context(unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config"))
        stack.enter_context(
            unittest.mock.patch(
                "terok.cli.commands.sickbay.GateServerManager.get_status", return_value=status
            )
        )
        stack.enter_context(
            unittest.mock.patch(
                "terok.cli.commands.sickbay.GateServerManager.check_units_outdated",
                return_value=None,
            )
        )
        stack.enter_context(
            unittest.mock.patch(
                "terok.cli.commands.sickbay.get_services_mode", return_value="socket"
            )
        )
        stack.enter_context(unittest.mock.patch("shutil.which", return_value=git_path))
        stack.enter_context(
            unittest.mock.patch(
                "terok.cli.commands.sickbay.GateServerManager.is_systemd_available",
                return_value=systemd_available,
            )
        )
        return stack

    def test_missing_git_named_as_consequence(self) -> None:
        """git absent on PATH → gate disabled, message names the consequence."""
        with self._stack(git_path=None, systemd_available=True):
            sev, _, detail = _check_gate_server()
        assert sev == "warn"
        assert "git not on PATH" in detail
        # Does not point at ``terok gate start`` — installing it wouldn't help.
        assert "terok gate start" not in detail

    def test_no_systemd_named_as_gap(self) -> None:
        """No user systemd → gate disabled, message names the missing fallback."""
        with self._stack(git_path="/usr/bin/git", systemd_available=False):
            sev, _, detail = _check_gate_server()
        assert sev == "warn"
        assert "no user systemd" in detail
        assert "terok gate start" not in detail

    def test_installable_path_points_at_start(self) -> None:
        """Operator-pending install (git + systemd present, gate down) → start hint."""
        with self._stack(git_path="/usr/bin/git", systemd_available=True):
            sev, _, detail = _check_gate_server()
        assert sev == "warn"
        assert "terok gate start" in detail


class TestCheckShieldDnsTier:
    """Shield row carries an install hint when not on the top DNS tier."""

    def _patch(self, dns_tier: str) -> unittest.mock._patch[unittest.mock.MagicMock]:
        """Patch ``check_environment`` so it returns a healthy shield on *dns_tier*."""
        ec = unittest.mock.MagicMock(
            health="ok", hooks="user", dns_tier=dns_tier, issues=[], setup_hint=""
        )
        return unittest.mock.patch("terok.cli.commands.sickbay.check_environment", return_value=ec)

    def test_dnsmasq_tier_no_hint(self) -> None:
        """Top tier → clean ok line, no install hint."""
        from terok.cli.commands.sickbay import _check_shield

        with self._patch("dnsmasq"):
            sev, _, detail = _check_shield()
        assert sev == "ok"
        assert "install dnsmasq" not in detail

    def test_dig_tier_carries_hint(self) -> None:
        """dig tier works but loses IP rotation — hint surfaces."""
        from terok.cli.commands.sickbay import _check_shield

        with self._patch("dig"):
            sev, _, detail = _check_shield()
        assert sev == "ok"
        assert "install dnsmasq" in detail

    def test_getent_tier_carries_hint(self) -> None:
        """getent tier (last resort) also gets the hint."""
        from terok.cli.commands.sickbay import _check_shield

        with self._patch("getent"):
            sev, _, detail = _check_shield()
        assert sev == "ok"
        assert "install dnsmasq" in detail


class TestCheckSelinuxPolicy:
    """Verify the five branches of the SELinux policy sickbay check.

    The decision tree itself lives in
    [`terok_sandbox.check_selinux_status`][terok_sandbox.check_selinux_status] (exercised separately in
    terok-sandbox's ``test_selinux.py``).  Here we patch that helper
    with pre-built [`SelinuxCheckResult`][terok_sandbox.SelinuxCheckResult] values and verify the
    sickbay-side *rendering* — tuple severity, label, detail text.
    """

    @staticmethod
    def _run(result: SelinuxCheckResult) -> tuple[str, str, str]:
        """Execute ``_check_selinux_policy`` with ``check_selinux_status`` mocked."""
        with unittest.mock.patch("terok.lib.api.setup.check_selinux_status", return_value=result):
            return _check_selinux_policy()

    def test_not_needed_in_tcp_mode(self) -> None:
        """``services.mode: tcp`` renders as ok."""

        sev, _, detail = self._run(SelinuxCheckResult(SelinuxStatus.NOT_APPLICABLE_TCP_MODE))
        assert sev == "ok"
        assert "services.mode: tcp" in detail

    def test_not_needed_when_selinux_permissive(self) -> None:
        """Socket mode on a permissive host renders as ok."""

        sev, _, detail = self._run(SelinuxCheckResult(SelinuxStatus.NOT_APPLICABLE_PERMISSIVE))
        assert sev == "ok"
        assert "not enforcing" in detail

    def test_warn_when_policy_missing(self) -> None:
        """Policy-missing renders both remedies (install-or-opt-out)."""

        sev, _, detail = self._run(SelinuxCheckResult(SelinuxStatus.POLICY_MISSING))
        assert sev == "warn"
        assert "terok_socket_t NOT installed" in detail
        assert "sudo bash" in detail
        assert "install_policy.sh" in detail
        # Opt-out must be surfaced — the user may not have root.
        assert "services: {mode: tcp}" in detail

    def test_warn_also_names_missing_tools(self) -> None:
        """Policy-missing + missing tools renders the dnf prerequisite plus both remedies."""

        sev, _, detail = self._run(
            SelinuxCheckResult(
                SelinuxStatus.POLICY_MISSING,
                missing_policy_tools=("checkmodule", "semodule_package"),
            )
        )
        assert sev == "warn"
        assert "policy tools missing" in detail
        assert "checkmodule" in detail
        assert "selinux-policy-devel" in detail
        assert "services: {mode: tcp}" in detail

    def test_warn_when_libselinux_unloadable(self) -> None:
        """Libselinux-missing renders as warn naming the silent-fail vector."""

        sev, _, detail = self._run(SelinuxCheckResult(SelinuxStatus.LIBSELINUX_MISSING))
        assert sev == "warn"
        assert "libselinux.so.1" in detail
        assert "unconfined_t" in detail

    def test_ok_when_everything_ready(self) -> None:
        """OK renders with the installer path for future reinstall/debug."""

        sev, _, detail = self._run(SelinuxCheckResult(SelinuxStatus.OK))
        assert sev == "ok"
        assert "terok_socket_t installed" in detail
        assert "install_policy.sh" in detail


class TestCheckVaultMigration:
    """``_check_vault_migration`` detects a lingering pre-vault ``credentials/`` dir."""

    def test_ok_when_no_legacy_dir(self, tmp_path: Path) -> None:
        """No legacy ``credentials/`` dir → ok."""

        def fake_ns(name: str) -> Path:
            return tmp_path / name  # neither exists

        with unittest.mock.patch("terok.lib.api.setup.namespace_state_dir", side_effect=fake_ns):
            sev, label, detail = _check_vault_migration()
        assert sev == "ok"
        assert label == "Vault migration"
        assert "no legacy" in detail

    def test_warn_when_only_legacy_exists(self, tmp_path: Path) -> None:
        """Legacy dir without new vault dir → warn pointing at the migration script."""
        legacy = tmp_path / "credentials"
        legacy.mkdir()  # exists
        vault = tmp_path / "vault"  # does not exist

        def fake_ns(name: str) -> Path:
            return legacy if name == "credentials" else vault

        with unittest.mock.patch("terok.lib.api.setup.namespace_state_dir", side_effect=fake_ns):
            sev, _, detail = _check_vault_migration()
        assert sev == "warn"
        assert str(legacy) in detail
        assert "terok-migrate-vault.py" in detail

    def test_info_when_both_exist(self, tmp_path: Path) -> None:
        """Both dirs present → info (migration ran but old dir survived for safety)."""
        legacy = tmp_path / "credentials"
        legacy.mkdir()
        vault = tmp_path / "vault"
        vault.mkdir()

        def fake_ns(name: str) -> Path:
            return legacy if name == "credentials" else vault

        with unittest.mock.patch("terok.lib.api.setup.namespace_state_dir", side_effect=fake_ns):
            sev, _, detail = _check_vault_migration()
        assert sev == "info"
        assert "still present" in detail
        assert "safe to remove" in detail

    def test_warn_when_probe_raises(self) -> None:
        """An exception inside the probe is surfaced as a warn result."""
        with unittest.mock.patch(
            "terok.lib.api.setup.namespace_state_dir",
            side_effect=RuntimeError("boom"),
        ):
            sev, label, detail = _check_vault_migration()
        assert sev == "warn"
        assert label == "Vault migration"
        assert "boom" in detail


class TestCheckTaskShieldAnnotation:
    """``_check_task_shield_annotation`` cross-checks task_dir ↔ container annotation."""

    def _project(self, tasks_root: Path) -> unittest.mock.MagicMock:
        project = unittest.mock.MagicMock()
        project.tasks_root = tasks_root
        return project

    def test_missing_meta_file_returns_none(self, tmp_path: Path) -> None:
        """No metadata YAML → skipped (not a sickbay concern)."""
        from terok.cli.commands.sickbay import _check_task_shield_annotation

        project = self._project(tmp_path)
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=tmp_path
        ):
            assert _check_task_shield_annotation("p", "g1abc", project) is None

    def test_malformed_yaml_returns_none(self, tmp_path: Path) -> None:
        """Bad metadata → skipped silently (_check_task_hook owns the warn)."""
        from terok.cli.commands.sickbay import _check_task_shield_annotation

        (tmp_path / "g1abc_meta.yml").write_bytes(b"\x80\x81")  # invalid UTF-8
        project = self._project(tmp_path)
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=tmp_path
        ):
            assert _check_task_shield_annotation("p", "g1abc", project) is None

    def test_no_mode_returns_none(self, tmp_path: Path) -> None:
        """Meta without ``mode`` → nothing to check."""
        from terok.cli.commands.sickbay import _check_task_shield_annotation

        _write_meta(tmp_path, "g1abc", {"status": "created"})
        project = self._project(tmp_path.parent)
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=tmp_path
        ):
            assert _check_task_shield_annotation("p", "g1abc", project) is None

    def test_non_running_container_returns_none(self, tmp_path: Path, mock_runtime) -> None:
        """Stopped container is post_stop's territory, not annotation-drift territory."""
        from terok.cli.commands.sickbay import _check_task_shield_annotation

        _write_meta(tmp_path, "g1abc", {"mode": "cli"})
        project = self._project(tmp_path.parent)
        mock_runtime.container.return_value.state = "exited"
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=tmp_path
        ):
            assert _check_task_shield_annotation("p", "g1abc", project) is None

    def test_shield_dir_absent_returns_none(self, tmp_path: Path, mock_runtime) -> None:
        """Unshielded task → no expectation to compare against."""
        from terok.cli.commands.sickbay import _check_task_shield_annotation

        _write_meta(tmp_path, "g1abc", {"mode": "cli"})
        project = self._project(tmp_path.parent)
        mock_runtime.container.return_value.state = "running"
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=tmp_path
        ):
            assert _check_task_shield_annotation("p", "g1abc", project) is None

    def test_missing_annotation_warns(self, tmp_path: Path, mock_runtime) -> None:
        """Shield dir present but container has no annotation → WARN."""
        from terok.cli.commands.sickbay import _check_task_shield_annotation

        tasks_root = tmp_path / "tasks"
        task_dir = tasks_root / "g1abc"
        (task_dir / "shield").mkdir(parents=True)
        meta_dir = tmp_path / "meta"
        meta_dir.mkdir()
        _write_meta(meta_dir, "g1abc", {"mode": "cli"})
        project = self._project(tasks_root)
        mock_runtime.container.return_value.state = "running"
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.tasks_meta_dir", return_value=meta_dir),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.resolve_container_state_dir",
                return_value=None,
            ),
        ):
            result = _check_task_shield_annotation("p", "g1abc", project)
        assert result is not None
        assert result[0] == "warn"
        assert "no terok.shield.state_dir" in result[2] or "annotation" in result[2]

    def test_annotation_mismatch_warns(self, tmp_path: Path, mock_runtime) -> None:
        """Annotation pointing elsewhere → WARN with both paths named."""
        from terok.cli.commands.sickbay import _check_task_shield_annotation

        tasks_root = tmp_path / "tasks"
        task_dir = tasks_root / "g1abc"
        expected_sd = task_dir / "shield"
        expected_sd.mkdir(parents=True)
        actual_sd = tmp_path / "elsewhere"
        actual_sd.mkdir()
        meta_dir = tmp_path / "meta"
        meta_dir.mkdir()
        _write_meta(meta_dir, "g1abc", {"mode": "cli"})
        project = self._project(tasks_root)
        mock_runtime.container.return_value.state = "running"
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.tasks_meta_dir", return_value=meta_dir),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.resolve_container_state_dir",
                return_value=actual_sd,
            ),
        ):
            result = _check_task_shield_annotation("p", "g1abc", project)
        assert result is not None
        assert result[0] == "warn"
        assert str(actual_sd) in result[2]
        assert str(expected_sd) in result[2]

    def test_annotation_matches_returns_none(self, tmp_path: Path, mock_runtime) -> None:
        """Annotation resolves to the same path → consistent, no result."""
        from terok.cli.commands.sickbay import _check_task_shield_annotation

        tasks_root = tmp_path / "tasks"
        task_dir = tasks_root / "g1abc"
        sd = task_dir / "shield"
        sd.mkdir(parents=True)
        meta_dir = tmp_path / "meta"
        meta_dir.mkdir()
        _write_meta(meta_dir, "g1abc", {"mode": "cli"})
        project = self._project(tasks_root)
        mock_runtime.container.return_value.state = "running"
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.tasks_meta_dir", return_value=meta_dir),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.resolve_container_state_dir",
                return_value=sd,
            ),
        ):
            assert _check_task_shield_annotation("p", "g1abc", project) is None


class TestCheckShieldAnnotations:
    """``_check_shield_annotations`` iterates task metadata + single-task paths."""

    def test_single_project_no_meta_dir(self, tmp_path: Path) -> None:
        """Missing metadata dir → empty result, no iteration crash."""
        from terok.cli.commands.sickbay import _check_shield_annotations

        project = unittest.mock.MagicMock()
        project.id = "p"
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.load_project", return_value=project),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.tasks_meta_dir",
                return_value=tmp_path / "nonexistent",
            ),
        ):
            assert _check_shield_annotations("p", None) == []

    def test_global_scope_iterates_projects(self, tmp_path: Path) -> None:
        """No project scope → iterate list_projects, aggregate WARN results."""
        from terok.cli.commands.sickbay import _check_shield_annotations

        meta_dir = tmp_path / "meta"
        meta_dir.mkdir()
        _write_meta(meta_dir, "g1abc", {"mode": "cli"})
        project = unittest.mock.MagicMock()
        project.id = "proj"
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.list_projects", return_value=[project]),
            unittest.mock.patch("terok.cli.commands.sickbay.tasks_meta_dir", return_value=meta_dir),
            unittest.mock.patch(
                "terok.cli.commands.sickbay._check_task_shield_annotation",
                return_value=("warn", "Task proj/1 shield", "drift"),
            ),
        ):
            results = _check_shield_annotations(None, None)
        assert len(results) == 1
        assert results[0][0] == "warn"

    def test_single_task_scope_does_not_glob(self, tmp_path: Path) -> None:
        """When task_id is provided, only that task is examined."""
        from terok.cli.commands.sickbay import _check_shield_annotations

        meta_dir = tmp_path / "meta"
        meta_dir.mkdir()
        _write_meta(meta_dir, "g1abc", {"mode": "cli"})
        _write_meta(meta_dir, "g2xyz", {"mode": "cli"})
        project = unittest.mock.MagicMock()
        project.id = "p"
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.load_project", return_value=project),
            unittest.mock.patch("terok.cli.commands.sickbay.tasks_meta_dir", return_value=meta_dir),
            unittest.mock.patch(
                "terok.cli.commands.sickbay._check_task_shield_annotation",
                return_value=None,
            ) as mock_check,
        ):
            _check_shield_annotations("p", "g1abc")
        # Only the named task, not the globbed pair
        assert mock_check.call_count == 1
        assert mock_check.call_args.args[1] == "g1abc"


class TestCheckClearanceStack:
    """Unified drift probe over the clearance hub + verdict + notifier triple.

    ``terok_clearance.check_units_outdated`` returns a single warning
    string when any of the three units are stale or half-installed,
    so sickbay collapses the previous per-unit checks into one stage.
    """

    def test_current_version_is_ok(self) -> None:
        """Both install targets surface in the detail line, side-by-side."""
        from terok.cli.commands.sickbay import _check_clearance_stack

        with (
            unittest.mock.patch(
                "terok.cli.commands.sickbay._clearance_check_units_outdated", return_value=None
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay._clearance_hub_unit_version", return_value=1
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay._clearance_notifier_unit_version", return_value=3
            ),
        ):
            sev, label, detail = _check_clearance_stack()
        assert sev == "ok"
        assert label == "Clearance stack"
        assert "terok-clearance-hub.service v1" in detail
        assert "terok-clearance-notifier.service v3" in detail

    def test_outdated_is_warn(self) -> None:
        from terok.cli.commands.sickbay import _check_clearance_stack

        with unittest.mock.patch(
            "terok.cli.commands.sickbay._clearance_check_units_outdated",
            return_value="is outdated (installed unversioned, expected v1) — rerun setup.",
        ):
            sev, _, detail = _check_clearance_stack()
        assert sev == "warn"
        assert "outdated" in detail

    def test_absent_unit_is_ok_not_installed(self) -> None:
        """No hub unit on disk = headless host (or pre-setup) → silent ok."""
        from terok.cli.commands.sickbay import _check_clearance_stack

        with (
            unittest.mock.patch(
                "terok.cli.commands.sickbay._clearance_check_units_outdated", return_value=None
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay._clearance_hub_unit_version", return_value=None
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay._clearance_notifier_unit_version", return_value=None
            ),
        ):
            sev, _, detail = _check_clearance_stack()
        assert sev == "ok"
        assert "not installed" in detail

    def test_notifier_only_install_renders_just_the_notifier_line(self) -> None:
        """Headless hosts that ship only the notifier (or vice versa) render one stamp.

        Sickbay must not assume both stamps are always present — a
        partial-install host (e.g. someone disabled the hub) should
        still produce a sensible detail string rather than hiding the
        one install target that does exist behind a misleading "not
        installed" branch.
        """
        from terok.cli.commands.sickbay import _check_clearance_stack

        with (
            unittest.mock.patch(
                "terok.cli.commands.sickbay._clearance_check_units_outdated", return_value=None
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay._clearance_hub_unit_version", return_value=None
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay._clearance_notifier_unit_version", return_value=3
            ),
        ):
            sev, _, detail = _check_clearance_stack()
        assert sev == "ok"
        assert "terok-clearance-hub" not in detail
        assert "terok-clearance-notifier.service v3" in detail


class TestCheckDefaultAgents:
    """``_check_default_agents`` warns when ``image.agents`` has no global default."""

    def test_warn_when_unset(self) -> None:
        """No global default → warn with a pointer at the new setter."""
        with unittest.mock.patch(
            "terok.lib.api.agents.get_global_image_agents",
            return_value=None,
        ):
            sev, label, detail = _check_default_agents()
        assert sev == "warn"
        assert label == "Default agents"
        assert "terok agents set" in detail

    def test_ok_when_set(self) -> None:
        """Any non-empty value → ok with the configured selection echoed back."""
        with unittest.mock.patch(
            "terok.lib.api.agents.get_global_image_agents",
            return_value="all,-vibe",
        ):
            sev, label, detail = _check_default_agents()
        assert sev == "ok"
        assert label == "Default agents"
        assert "all,-vibe" in detail

    def test_warn_when_probe_raises(self) -> None:
        """A failing probe is surfaced as a warn — never crashes sickbay."""
        with unittest.mock.patch(
            "terok.lib.api.agents.get_global_image_agents",
            side_effect=RuntimeError("boom"),
        ):
            sev, label, detail = _check_default_agents()
        assert sev == "warn"
        assert label == "Default agents"
        assert "boom" in detail


class TestCheckRecoveryAcknowledged:
    """``_check_recovery_acknowledged`` produces the host-level row.

    Pre-fix the recovery check was bundled into
    ``sandbox_doctor_checks`` and rendered per-task; terok's host-level
    sickbay now owns its own row instead so the warning appears
    exactly once.  Severity escalates from ``warn`` to ``error`` when
    the resolver lands on the session-unlock tmpfs tier and the marker
    is missing — one reboot away from losing the vault.
    """

    @staticmethod
    def _status(*, acknowledged: bool, source: str | None):
        """Build a real ``RecoveryStatus`` so the ``urgent`` property derives correctly."""
        from terok.lib.integrations.sandbox import RecoveryStatus

        return RecoveryStatus(acknowledged=acknowledged, source=source)

    def test_ok_when_marker_present(self) -> None:
        """Acknowledged → ``ok`` with a brief detail."""
        with unittest.mock.patch(
            "terok.lib.api.shield.RecoveryStatus.load",
            return_value=self._status(acknowledged=True, source="keyring"),
        ):
            sev, label, detail = _check_recovery_acknowledged()
        assert sev == "ok"
        assert label == "Recovery key acknowledged"
        assert "acknowledged" in detail

    def test_warn_when_marker_missing_durable_tier(self) -> None:
        """Unacked + durable tier → ``warn`` naming both remediation verbs."""
        with unittest.mock.patch(
            "terok.lib.api.shield.RecoveryStatus.load",
            return_value=self._status(acknowledged=False, source="keyring"),
        ):
            sev, label, detail = _check_recovery_acknowledged()
        assert sev == "warn"
        assert label == "Recovery key acknowledged"
        assert "unconfirmed" in detail
        assert "terok vault passphrase reveal" in detail
        assert "terok vault passphrase acknowledge" in detail
        # Escalated wording must not bleed into the durable branch.
        assert "UNRECOVERABLE" not in detail

    def test_error_when_marker_missing_session_only(self) -> None:
        """Unacked + session-file source → ``error`` with the reboot-loss wording."""
        with unittest.mock.patch(
            "terok.lib.api.shield.RecoveryStatus.load",
            return_value=self._status(acknowledged=False, source="session-file"),
        ):
            sev, label, detail = _check_recovery_acknowledged()
        assert sev == "error"
        assert label == "Recovery key acknowledged"
        # Explicit operator-facing breadcrumbs of the asymmetry.
        assert "session-unlock" in detail
        assert "reboot" in detail.lower()
        assert "UNRECOVERABLE" in detail
        # Both remediation verbs still surface.
        assert "terok vault passphrase reveal" in detail
        assert "terok vault passphrase acknowledge" in detail

    def test_warn_when_probe_raises(self) -> None:
        """A failing probe degrades to a warn — never crashes sickbay."""
        with unittest.mock.patch(
            "terok.lib.api.shield.RecoveryStatus.load",
            side_effect=RuntimeError("boom"),
        ):
            sev, label, detail = _check_recovery_acknowledged()
        assert sev == "warn"
        assert "boom" in detail
