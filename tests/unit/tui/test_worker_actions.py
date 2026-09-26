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


@pytest.mark.parametrize("comment", [None, "deploy"])
def test_init_ssh_provisions_and_summarizes(comment: str | None) -> None:
    """``init_ssh`` provisions a key then renders its summary."""
    project = mock.Mock()
    project.provision_ssh_key.return_value = "RESULT"
    with (
        mock.patch("terok.lib.api.get_project", return_value=project) as m_get,
        mock.patch("terok.lib.api.summarize_ssh_init") as m_sum,
    ):
        worker_actions.init_ssh("proj", comment)
    m_get.assert_called_once_with("proj")
    project.provision_ssh_key.assert_called_once_with(comment=comment)
    m_sum.assert_called_once_with("RESULT")


# Full project setup has no worker_actions entrypoint — it reuses the
# wizard's InitProgressScreen (for the interactive deploy-key pause).
# Its coverage lives in test_detail_screens / test_wizard_screens.


# ── Gate sync ─────────────────────────────────────────────────────────


def _gate_sync_result(**overrides: object) -> dict:
    """A complete, quiet GateSyncResult for worker-action tests."""
    result: dict = {
        "success": True,
        "path": "/tmp/terok-testing/gate/proj.git",
        "upstream_url": "https://example.com/repo.git",
        "created": False,
        "migrated": False,
        "errors": [],
        "notes": [],
        "applied": [],
        "pending": [],
        "gate_only_branches": [],
        "cache_refreshed": False,
        "cache_error": None,
    }
    result.update(overrides)
    return result


def test_sync_gate_success_does_not_raise() -> None:
    """A successful sync prints its status and returns cleanly."""
    fake_gate = mock.Mock()
    fake_gate.sync.return_value = _gate_sync_result(created=True)
    with (
        mock.patch("terok.lib.api.load_project"),
        mock.patch("terok.lib.api.make_git_gate", return_value=fake_gate),
    ):
        worker_actions.sync_gate("proj")
    fake_gate.sync.assert_called_once()


def test_sync_gate_prints_pending_ops(capsys: pytest.CaptureFixture[str]) -> None:
    """Pending destructive ops appear in the worker log, clearly not applied."""
    fake_gate = mock.Mock()
    fake_gate.sync.return_value = _gate_sync_result(
        pending=[
            {
                "branch": "feat/x",
                "kind": "delete",
                "reason": "upstream_delete",
                "gate_sha": "a" * 40,
                "upstream_sha": None,
                "old_snapshot_sha": "a" * 40,
                "lossless": True,
                "gate_only_commits": 0,
            }
        ]
    )
    with (
        mock.patch("terok.lib.api.load_project"),
        mock.patch("terok.lib.api.make_git_gate", return_value=fake_gate),
    ):
        worker_actions.sync_gate("proj")
    out = capsys.readouterr().out
    assert "pending destructive change(s)" in out
    assert "delete feat/x" in out and "no gate-local commits" in out


def test_apply_gate_ops_success_prints_backups(capsys: pytest.CaptureFixture[str]) -> None:
    """A clean apply prints each op with its backup ref and returns quietly."""
    fake_gate = mock.Mock()
    fake_gate.apply_pending_ops.return_value = {
        "success": True,
        "applied": [{"branch": "feat/x", "kind": "delete", "old_sha": "a" * 40, "new_sha": None}],
        "backups": {"feat/x": "refs/terok/backup/feat/x/20260720T000000Z-aaaaaaaaaaaa"},
        "errors": [],
    }
    with (
        mock.patch("terok.lib.api.load_project"),
        mock.patch("terok.lib.api.make_git_gate", return_value=fake_gate),
    ):
        worker_actions.apply_gate_ops("proj", [{"branch": "feat/x"}])
    out = capsys.readouterr().out
    assert "applied: delete feat/x" in out
    assert "refs/terok/backup/feat/x" in out


def test_apply_gate_ops_reports_backups_and_failures() -> None:
    """apply_gate_ops prints applied ops with backups; failures raise SystemExit."""
    fake_gate = mock.Mock()
    fake_gate.apply_pending_ops.return_value = {
        "success": False,
        "applied": [{"branch": "feat/x", "kind": "delete", "old_sha": "a" * 40, "new_sha": None}],
        "backups": {"feat/x": "refs/terok/backup/feat/x/20260720T000000Z-aaaaaaaaaaaa"},
        "errors": ["feat/y: branch moved since the op was proposed — not applied"],
    }
    with (
        mock.patch("terok.lib.api.load_project"),
        mock.patch("terok.lib.api.make_git_gate", return_value=fake_gate),
        pytest.raises(SystemExit),
    ):
        worker_actions.apply_gate_ops("proj", [{"branch": "feat/x"}, {"branch": "feat/y"}])
    fake_gate.apply_pending_ops.assert_called_once()


def test_restore_gate_backup_reports_previous_tip(capsys: pytest.CaptureFixture[str]) -> None:
    """A successful restore names the branch and the safety backup it took."""
    fake_gate = mock.Mock()
    fake_gate.restore_backup.return_value = {
        "branch": "feat/x",
        "restored_sha": "b" * 40,
        "previous_backup_ref": "refs/terok/backup/feat/x/20260722T000000Z-cccccccccccc",
        "error": None,
    }
    ref = "refs/terok/backup/feat/x/20260720T000000Z-aaaaaaaaaaaa"
    with (
        mock.patch("terok.lib.api.load_project"),
        mock.patch("terok.lib.api.make_git_gate", return_value=fake_gate),
    ):
        worker_actions.restore_gate_backup("proj", ref)
    fake_gate.restore_backup.assert_called_once_with(ref)
    out = capsys.readouterr().out
    assert "Restored feat/x" in out
    assert "Previous tip saved as" in out


def test_restore_gate_backup_error_raises() -> None:
    """A failed restore raises SystemExit with the gate's reason."""
    fake_gate = mock.Mock()
    fake_gate.restore_backup.return_value = {
        "branch": "feat/x",
        "restored_sha": None,
        "previous_backup_ref": None,
        "error": "could not back up the current tip — restore refused",
    }
    with (
        mock.patch("terok.lib.api.load_project"),
        mock.patch("terok.lib.api.make_git_gate", return_value=fake_gate),
        pytest.raises(SystemExit, match="Restore failed"),
    ):
        worker_actions.restore_gate_backup("proj", "refs/terok/backup/feat/x/x-y")


def test_delete_gate_backup_reports_and_errors() -> None:
    """delete_gate_backup confirms on success and raises on the gate's error."""
    fake_gate = mock.Mock()
    fake_gate.delete_backup.return_value = None
    ref = "refs/terok/backup/feat/x/20260720T000000Z-aaaaaaaaaaaa"
    with (
        mock.patch("terok.lib.api.load_project"),
        mock.patch("terok.lib.api.make_git_gate", return_value=fake_gate),
    ):
        worker_actions.delete_gate_backup("proj", ref)
    fake_gate.delete_backup.assert_called_once_with(ref)

    fake_gate.delete_backup.return_value = "not a backup ref: refs/heads/master"
    with (
        mock.patch("terok.lib.api.load_project"),
        mock.patch("terok.lib.api.make_git_gate", return_value=fake_gate),
        pytest.raises(SystemExit, match="Delete failed"),
    ):
        worker_actions.delete_gate_backup("proj", "refs/heads/master")


def test_sync_gate_reports_cache_refresh_failure(capsys: pytest.CaptureFixture[str]) -> None:
    """A successful sync with a failed clone-cache refresh names the failure.

    The sync itself stays green (the cache is an optimization), but the
    summary must not read as an all-clear while the refresh silently
    failed.
    """
    fake_gate = mock.Mock()
    fake_gate.sync.return_value = _gate_sync_result(cache_error="exit status 128: fatal: bad ref")
    with (
        mock.patch("terok.lib.api.load_project"),
        mock.patch("terok.lib.api.make_git_gate", return_value=fake_gate),
    ):
        worker_actions.sync_gate("proj")
    out = capsys.readouterr().out
    assert "Gate synced from upstream." in out
    assert "clone cache refresh failed: exit status 128: fatal: bad ref" in out


def test_sync_gate_failure_raises_systemexit() -> None:
    """A failed sync raises ``SystemExit`` so the child exits non-zero."""
    fake_gate = mock.Mock()
    fake_gate.sync.return_value = {"success": False, "created": False, "errors": ["boom"]}
    with (
        mock.patch("terok.lib.api.load_project"),
        mock.patch("terok.lib.api.make_git_gate", return_value=fake_gate),
        # _print_sync_gate_ssh_help short-circuits when the upstream is not SSH.
        mock.patch("terok.lib.api.setup.is_ssh_url", return_value=False),
    ):
        with pytest.raises(SystemExit, match="Gate sync failed"):
            worker_actions.sync_gate("proj")


# ── Shield ─────────────────────────────────────────────────────────────


def test_shield_setup_installs() -> None:
    """``shield_setup`` delegates to ``ShieldHooks.install()`` — no scope flags."""
    with mock.patch("terok.lib.integrations.sandbox.ShieldHooks.install") as m:
        worker_actions.shield_setup()
    m.assert_called_once_with()


# ── Vault ─────────────────────────────────────────────────────────────


def test_vault_lock_purges_every_tier(tmp_path: Path) -> None:
    """``vault_lock`` clears every stored copy via ``purge_passphrase_tiers``."""
    cfg = mock.Mock()
    with (
        mock.patch("terok.lib.api.make_sandbox_config", return_value=cfg),
        mock.patch("terok.lib.api.vault.purge_passphrase_tiers") as purge,
    ):
        worker_actions.vault_lock()
    purge.assert_called_once_with(cfg)


def test_vault_seal_calls_handle_with_key_auto() -> None:
    """``vault_seal`` defers to the sandbox helper with ``key='auto'``."""
    cfg = mock.Mock()
    with (
        mock.patch("terok.lib.api.make_sandbox_config", return_value=cfg),
        mock.patch("terok.lib.api.vault.handle_vault_seal") as m_seal,
    ):
        worker_actions.vault_seal()
    m_seal.assert_called_once_with(cfg=cfg, key="auto")


def test_vault_to_keyring_calls_handle_with_cfg() -> None:
    """``vault_to_keyring`` defers to the sandbox helper with terok's cfg."""
    cfg = mock.Mock()
    with (
        mock.patch("terok.lib.api.make_sandbox_config", return_value=cfg),
        mock.patch("terok.lib.api.vault.handle_vault_to_keyring") as m_to_keyring,
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
            "terok.lib.api.setup.selinux_install_script",
            return_value=Path("/bundled/install_policy.sh"),
        ),
        mock.patch("terok_util.find_host_tool", side_effect=_which),
        mock.patch("subprocess.run") as m_run,
    ):
        worker_actions.selinux_install_policy()
    # Absolute paths from ``find_host_tool`` — no partial-path lookup at
    # exec time (bandit B607 / Sonar partial-path).
    m_run.assert_called_once_with(
        ["/usr/bin/sudo", "/usr/bin/bash", "/bundled/install_policy.sh"], check=True
    )


def test_selinux_install_policy_aborts_when_sudo_missing() -> None:
    """A missing ``sudo`` surfaces as SystemExit with the binary name."""
    import pytest as _pytest

    with mock.patch("terok_util.find_host_tool", return_value=None):
        with _pytest.raises(SystemExit, match="sudo not on PATH"):
            worker_actions.selinux_install_policy()


def test_selinux_install_policy_aborts_when_bash_missing() -> None:
    """A missing ``bash`` surfaces as SystemExit with the binary name."""
    import pytest as _pytest

    def _which(name: str) -> str | None:
        return "/usr/bin/sudo" if name == "sudo" else None

    with mock.patch("terok_util.find_host_tool", side_effect=_which):
        with _pytest.raises(SystemExit, match="bash not on PATH"):
            worker_actions.selinux_install_policy()


def test_selinux_switch_to_tcp_writes_services_mode(tmp_path) -> None:
    """``selinux_switch_to_tcp`` writes ``services.mode: tcp`` to the user config.yml."""
    user_config = tmp_path / "config.yml"
    with (
        mock.patch("terok.lib.core.config.global_config_path", return_value=user_config),
        mock.patch("terok.lib.api.setup.yaml_update_section") as m_update,
    ):
        worker_actions.selinux_switch_to_tcp()
    m_update.assert_called_once_with(user_config, "services", {"mode": "tcp"})


# ── Task lifecycle ────────────────────────────────────────────────────


def test_task_restart_and_stop_delegate_to_facade() -> None:
    """``task_restart`` / ``task_stop`` forward ``(project_name, task_id)`` to the facade."""
    with mock.patch("terok.lib.api.task_restart") as m_restart:
        worker_actions.task_restart("proj", "tid")
    m_restart.assert_called_once_with("proj", "tid")
    with mock.patch("terok.lib.api.task_stop") as m_stop:
        worker_actions.task_stop("proj", "tid")
    m_stop.assert_called_once_with("proj", "tid")


def test_task_recreate_delegates_with_fresh() -> None:
    """``task_recreate`` forwards to the facade with ``fresh=True`` (recreate rung)."""
    with mock.patch("terok.lib.api.task_restart") as m_restart:
        worker_actions.task_recreate("proj", "tid")
    m_restart.assert_called_once_with("proj", "tid", fresh=True)


def test_start_cli_container_delegates_to_task_run_cli() -> None:
    """``start_cli_container`` forwards ``(project_name, task_id)`` to ``task_run_cli``."""
    with mock.patch("terok.lib.api.task_run_cli") as m:
        worker_actions.start_cli_container("proj", "tid")
    m.assert_called_once_with("proj", "tid")


def test_start_toad_container_delegates_to_task_run_toad() -> None:
    """``start_toad_container`` forwards ``(project_name, task_id)`` to ``task_run_toad``."""
    with mock.patch("terok.lib.api.task_run_toad") as m:
        worker_actions.start_toad_container("proj", "tid")
    m.assert_called_once_with("proj", "tid")


# ── _print_sync_gate_ssh_help ─────────────────────────────────────────


def test_sync_gate_ssh_help_non_ssh_upstream_is_silent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A non-SSH upstream gets no hint — the helper returns early."""
    with (
        mock.patch("terok.lib.api.load_project", return_value=mock.Mock()),
        mock.patch("terok.lib.api.setup.is_ssh_url", return_value=False),
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
        mock.patch("terok.lib.api.setup.is_ssh_url", return_value=True),
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
        mock.patch("terok.lib.api.setup.is_ssh_url", return_value=True),
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
        mock.patch("terok.lib.api.setup.is_ssh_url", return_value=False),
    ):
        with pytest.raises(SystemExit, match="Gate sync failed: auth denied"):
            worker_actions.sync_gate("proj")
