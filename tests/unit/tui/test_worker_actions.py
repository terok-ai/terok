# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for [`terok.tui.worker_actions`][terok.tui.worker_actions].

Each worker action is a thin child-process adapter over a facade /
sandbox call.  ``worker_actions`` uses function-local imports, so the
dependencies are patched at their *source* module
(``terok.lib.api`` / ``terok.lib.integrations.sandbox``), which the
in-function ``import`` then picks up.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from terok.tui import worker_actions

# ── Project infrastructure ────────────────────────────────────────────


def test_generate_calls_facade() -> None:
    """``generate`` delegates straight to ``generate_dockerfiles``."""
    with mock.patch("terok.lib.api.generate_dockerfiles") as m:
        worker_actions.generate("proj")
    m.assert_called_once_with("proj")


def test_build_variants_pass_the_right_flags() -> None:
    """The three build entrypoints map to the right ``build_images`` kwargs."""
    with mock.patch("terok.lib.api.build_images") as m:
        worker_actions.build("proj")
        worker_actions.build_agents("proj")
        worker_actions.build_full("proj")
    assert m.call_args_list == [
        mock.call("proj"),
        mock.call("proj", refresh_agents=True),
        mock.call("proj", full_rebuild=True),
    ]


def test_init_ssh_provisions_and_summarizes() -> None:
    """``init_ssh`` provisions a key then renders its summary."""
    with (
        mock.patch("terok.lib.api.provision_ssh_key", return_value="RESULT") as m_prov,
        mock.patch("terok.lib.api.summarize_ssh_init") as m_sum,
    ):
        worker_actions.init_ssh("proj")
    m_prov.assert_called_once_with("proj")
    m_sum.assert_called_once_with("RESULT")


# Full project setup has no worker_actions entrypoint — it reuses the
# wizard's InitProgressScreen (for the interactive deploy-key pause).
# Its coverage lives in test_detail_screens / test_wizard_screens.


# ── Authentication ────────────────────────────────────────────────────


def test_auth_passes_provider_and_scope() -> None:
    """``auth`` forwards the provider and the (possibly ``None``) project scope."""
    with mock.patch("terok.lib.api.authenticate") as m:
        worker_actions.auth("claude", "proj")
        worker_actions.auth("claude", None)
    assert m.call_args_list == [mock.call("claude", "proj"), mock.call("claude", None)]


# ── Gate sync ─────────────────────────────────────────────────────────


def test_sync_gate_success_does_not_raise() -> None:
    """A successful sync prints its status and returns cleanly."""
    fake_gate = mock.Mock()
    fake_gate.sync.return_value = {"success": True, "created": True, "errors": []}
    with (
        mock.patch("terok.lib.api.load_project"),
        mock.patch("terok.lib.api.make_git_gate", return_value=fake_gate),
    ):
        worker_actions.sync_gate("proj")
    fake_gate.sync.assert_called_once()


def test_sync_gate_failure_raises_systemexit() -> None:
    """A failed sync raises ``SystemExit`` so the child exits non-zero."""
    fake_gate = mock.Mock()
    fake_gate.sync.return_value = {"success": False, "created": False, "errors": ["boom"]}
    with (
        mock.patch("terok.lib.api.load_project"),
        mock.patch("terok.lib.api.make_git_gate", return_value=fake_gate),
        # _print_sync_gate_ssh_help short-circuits when the upstream is not SSH.
        mock.patch("terok.lib.integrations.sandbox.is_ssh_url", return_value=False),
    ):
        with pytest.raises(SystemExit, match="Gate sync failed"):
            worker_actions.sync_gate("proj")


# ── Gate server / shield ──────────────────────────────────────────────


def test_gate_install_uses_manager() -> None:
    """``gate_install`` builds a config and installs the manager's units."""
    with (
        mock.patch("terok.lib.api.make_sandbox_config", return_value="CFG"),
        mock.patch("terok.lib.integrations.sandbox.GateServerManager") as m_mgr,
    ):
        worker_actions.gate_install()
    m_mgr.assert_called_once_with("CFG")
    m_mgr.return_value.install_systemd_units.assert_called_once_with()


def test_gate_start_and_stop_delegate_to_daemon_controls() -> None:
    """``gate_start`` / ``gate_stop`` call the sandbox daemon controls."""
    with mock.patch("terok.lib.integrations.sandbox.start_daemon") as m_start:
        worker_actions.gate_start()
    m_start.assert_called_once_with()
    with mock.patch("terok.lib.integrations.sandbox.stop_daemon") as m_stop:
        worker_actions.gate_stop()
    m_stop.assert_called_once_with()


def test_shield_setup_passes_root_flag() -> None:
    """``shield_setup`` forwards the root-scope flag verbatim."""
    with mock.patch("terok.lib.integrations.sandbox.setup_hooks_direct") as m:
        worker_actions.shield_setup(True)
        worker_actions.shield_setup(False)
    assert m.call_args_list == [mock.call(root=True), mock.call(root=False)]


# ── Vault ─────────────────────────────────────────────────────────────


def test_vault_lock_unlinks_session_file_and_stops(tmp_path: Path) -> None:
    """``vault_lock`` removes the session-tier passphrase file and stops the daemon."""
    passphrase_file = tmp_path / "vault.passphrase"
    passphrase_file.write_text("not-a-real-passphrase\n", encoding="utf-8")
    cfg = mock.Mock()
    cfg.vault_passphrase_file = passphrase_file
    with (
        mock.patch("terok.lib.api.make_sandbox_config", return_value=cfg),
        mock.patch("terok.lib.integrations.sandbox.stop_vault") as m_stop,
    ):
        worker_actions.vault_lock()
    assert not passphrase_file.exists()
    m_stop.assert_called_once_with()


def test_vault_lock_tolerates_missing_session_file(tmp_path: Path) -> None:
    """No session-file on disk is the cold-start path — must not raise."""
    cfg = mock.Mock()
    cfg.vault_passphrase_file = tmp_path / "never-created.passphrase"
    with (
        mock.patch("terok.lib.api.make_sandbox_config", return_value=cfg),
        mock.patch("terok.lib.integrations.sandbox.stop_vault") as m_stop,
    ):
        worker_actions.vault_lock()
    m_stop.assert_called_once_with()


def test_vault_seal_calls_handle_with_key_auto() -> None:
    """``vault_seal`` defers to the sandbox helper with ``key='auto'``."""
    cfg = mock.Mock()
    with (
        mock.patch("terok.lib.api.make_sandbox_config", return_value=cfg),
        mock.patch("terok.lib.integrations.sandbox.handle_vault_seal") as m_seal,
    ):
        worker_actions.vault_seal()
    m_seal.assert_called_once_with(cfg=cfg, key="auto")


def test_vault_to_keyring_calls_handle_with_cfg() -> None:
    """``vault_to_keyring`` defers to the sandbox helper with terok's cfg."""
    cfg = mock.Mock()
    with (
        mock.patch("terok.lib.api.make_sandbox_config", return_value=cfg),
        mock.patch("terok.lib.integrations.sandbox.handle_vault_to_keyring") as m_to_keyring,
    ):
        worker_actions.vault_to_keyring()
    m_to_keyring.assert_called_once_with(cfg=cfg)


def test_selinux_install_policy_runs_sudo_bash() -> None:
    """``selinux_install_policy`` resolves sudo + bash via PATH and runs the bundled script."""
    from pathlib import Path

    def _which(name: str) -> str:
        return {"sudo": "/usr/bin/sudo", "bash": "/usr/bin/bash"}[name]

    with (
        mock.patch(
            "terok.lib.integrations.sandbox.selinux_install_script",
            return_value=Path("/bundled/install_policy.sh"),
        ),
        mock.patch("shutil.which", side_effect=_which),
        mock.patch("subprocess.run") as m_run,
    ):
        worker_actions.selinux_install_policy()
    # Absolute paths from ``shutil.which`` — no partial-path lookup at
    # exec time (bandit B607 / Sonar partial-path).
    m_run.assert_called_once_with(
        ["/usr/bin/sudo", "/usr/bin/bash", "/bundled/install_policy.sh"], check=True
    )


def test_selinux_install_policy_aborts_when_sudo_missing() -> None:
    """A missing ``sudo`` surfaces as SystemExit with the binary name."""
    import pytest as _pytest

    with mock.patch("shutil.which", return_value=None):
        with _pytest.raises(SystemExit, match="sudo not on PATH"):
            worker_actions.selinux_install_policy()


def test_selinux_install_policy_aborts_when_bash_missing() -> None:
    """A missing ``bash`` surfaces as SystemExit with the binary name."""
    import pytest as _pytest

    def _which(name: str) -> str | None:
        return "/usr/bin/sudo" if name == "sudo" else None

    with mock.patch("shutil.which", side_effect=_which):
        with _pytest.raises(SystemExit, match="bash not on PATH"):
            worker_actions.selinux_install_policy()


def test_selinux_switch_to_tcp_writes_services_mode(tmp_path) -> None:
    """``selinux_switch_to_tcp`` writes ``services.mode: tcp`` to the user config.yml."""
    user_config = tmp_path / "config.yml"
    with (
        mock.patch("terok.lib.core.config.global_config_path", return_value=user_config),
        mock.patch("terok.lib.integrations.sandbox.yaml_update_section") as m_update,
    ):
        worker_actions.selinux_switch_to_tcp()
    m_update.assert_called_once_with(user_config, "services", {"mode": "tcp"})


# ── Task lifecycle ────────────────────────────────────────────────────


def test_task_restart_and_stop_delegate_to_facade() -> None:
    """``task_restart`` / ``task_stop`` forward ``(project_id, task_id)`` to the facade."""
    with mock.patch("terok.lib.api.task_restart") as m_restart:
        worker_actions.task_restart("proj", "tid")
    m_restart.assert_called_once_with("proj", "tid")
    with mock.patch("terok.lib.api.task_stop") as m_stop:
        worker_actions.task_stop("proj", "tid")
    m_stop.assert_called_once_with("proj", "tid")


def test_start_cli_container_delegates_to_task_run_cli() -> None:
    """``start_cli_container`` forwards ``(project_id, task_id)`` to ``task_run_cli``."""
    with mock.patch("terok.lib.api.task_run_cli") as m:
        worker_actions.start_cli_container("proj", "tid")
    m.assert_called_once_with("proj", "tid")


def test_start_toad_container_delegates_to_task_run_toad() -> None:
    """``start_toad_container`` forwards ``(project_id, task_id)`` to ``task_run_toad``."""
    with mock.patch("terok.lib.api.task_run_toad") as m:
        worker_actions.start_toad_container("proj", "tid")
    m.assert_called_once_with("proj", "tid")


# ── Gate server / vault (remaining entrypoints) ───────────────────────


def test_gate_uninstall_uses_manager() -> None:
    """``gate_uninstall`` removes the gate server's systemd units."""
    with (
        mock.patch("terok.lib.api.make_sandbox_config", return_value="CFG"),
        mock.patch("terok.lib.integrations.sandbox.GateServerManager") as m_mgr,
    ):
        worker_actions.gate_uninstall()
    m_mgr.assert_called_once_with("CFG")
    m_mgr.return_value.uninstall_systemd_units.assert_called_once_with()


def test_vault_install_generates_routes_then_installs_units() -> None:
    """``vault_install`` generates routes, then installs the vault's systemd units."""
    cfg = mock.Mock()
    with (
        mock.patch("terok.lib.api.make_sandbox_config", return_value=cfg),
        mock.patch("terok.lib.integrations.executor.ensure_vault_routes") as m_routes,
        mock.patch("terok.lib.integrations.sandbox.VaultManager") as m_mgr,
    ):
        worker_actions.vault_install()
    m_routes.assert_called_once_with(cfg=cfg)
    m_mgr.assert_called_once_with(cfg)
    m_mgr.return_value.install_systemd_units.assert_called_once_with()


def test_vault_uninstall_uses_manager() -> None:
    """``vault_uninstall`` removes the vault's systemd units."""
    with (
        mock.patch("terok.lib.api.make_sandbox_config", return_value="CFG"),
        mock.patch("terok.lib.integrations.sandbox.VaultManager") as m_mgr,
    ):
        worker_actions.vault_uninstall()
    m_mgr.assert_called_once_with("CFG")
    m_mgr.return_value.uninstall_systemd_units.assert_called_once_with()


def test_vault_start_generates_routes_then_starts_daemon() -> None:
    """``vault_start`` generates routes, then starts the vault daemon."""
    cfg = mock.Mock()
    with (
        mock.patch("terok.lib.api.make_sandbox_config", return_value=cfg),
        mock.patch("terok.lib.integrations.executor.ensure_vault_routes") as m_routes,
        mock.patch("terok.lib.integrations.sandbox.start_vault") as m_start,
    ):
        worker_actions.vault_start()
    m_routes.assert_called_once_with(cfg=cfg)
    m_start.assert_called_once_with(cfg=cfg)


def test_vault_stop_delegates_to_sandbox() -> None:
    """``vault_stop`` stops the vault daemon."""
    with mock.patch("terok.lib.integrations.sandbox.stop_vault") as m_stop:
        worker_actions.vault_stop()
    m_stop.assert_called_once_with()


# ── _print_sync_gate_ssh_help ─────────────────────────────────────────


def test_sync_gate_ssh_help_non_ssh_upstream_is_silent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A non-SSH upstream gets no hint — the helper returns early."""
    with (
        mock.patch("terok.lib.api.load_project", return_value=mock.Mock()),
        mock.patch("terok.lib.integrations.sandbox.is_ssh_url", return_value=False),
    ):
        worker_actions._print_sync_gate_ssh_help("proj")
    assert capsys.readouterr().out == ""


def test_sync_gate_ssh_help_unloadable_project_is_silent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A project that cannot be loaded just yields no hint (best-effort)."""
    with mock.patch("terok.lib.api.load_project", side_effect=Exception("boom")):
        worker_actions._print_sync_gate_ssh_help("proj")
    assert capsys.readouterr().out == ""


def test_sync_gate_ssh_help_ssh_with_key_prints_pubkey(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An SSH upstream with a vault key prints the public line to register."""
    with (
        mock.patch("terok.lib.api.load_project", return_value=mock.Mock()),
        mock.patch("terok.lib.integrations.sandbox.is_ssh_url", return_value=True),
        mock.patch(
            "terok.tui.worker_actions._lookup_vault_pub_line",
            return_value="ssh-ed25519 AAAA tk-main:proj",
        ),
    ):
        worker_actions._print_sync_gate_ssh_help("proj")
    out = capsys.readouterr().out
    assert "ssh-ed25519 AAAA tk-main:proj" in out
    assert "register" in out.lower()


def test_sync_gate_ssh_help_ssh_without_key_points_at_ssh_init(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An SSH upstream with no vault key points the operator at ``ssh-init``."""
    with (
        mock.patch("terok.lib.api.load_project", return_value=mock.Mock()),
        mock.patch("terok.lib.integrations.sandbox.is_ssh_url", return_value=True),
        mock.patch("terok.tui.worker_actions._lookup_vault_pub_line", return_value=None),
    ):
        worker_actions._print_sync_gate_ssh_help("proj")
    assert "ssh-init" in capsys.readouterr().out


def test_sync_gate_systemexit_from_sync_is_reraised_with_context() -> None:
    """A ``SystemExit`` from ``gate.sync()`` is re-raised under a 'Gate sync failed' prefix."""
    fake_gate = mock.Mock()
    fake_gate.sync.side_effect = SystemExit("auth denied")
    with (
        mock.patch("terok.lib.api.load_project"),
        mock.patch("terok.lib.api.make_git_gate", return_value=fake_gate),
        mock.patch("terok.lib.integrations.sandbox.is_ssh_url", return_value=False),
    ):
        with pytest.raises(SystemExit, match="Gate sync failed: auth denied"):
            worker_actions.sync_gate("proj")
