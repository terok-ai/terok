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


def test_project_init_runs_four_steps_without_interactive_pause() -> None:
    """``project_init`` runs all four steps and never blocks on stdin.

    The CLI's ``maybe_pause_for_ssh_key_registration`` is deliberately
    *not* called — a child process has no stdin, so an ``input()`` pause
    would raise ``EOFError``.  Completing here without one proves it.
    """
    fake_gate = mock.Mock()
    fake_gate.sync.return_value = {"success": True, "path": "/tmp/terok-testing/g", "errors": []}
    with (
        mock.patch("terok.lib.api.provision_ssh_key", return_value="R"),
        mock.patch("terok.lib.api.summarize_ssh_init") as m_sum,
        mock.patch("terok.lib.api.generate_dockerfiles") as m_gen,
        mock.patch("terok.lib.api.build_images") as m_build,
        mock.patch("terok.lib.api.load_project"),
        mock.patch("terok.lib.api.make_git_gate", return_value=fake_gate),
    ):
        worker_actions.project_init("proj")
    m_sum.assert_called_once_with("R")
    m_gen.assert_called_once_with("proj")
    m_build.assert_called_once_with("proj")
    fake_gate.sync.assert_called_once()


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
        mock.patch("terok.lib.integrations.sandbox._handle_vault_seal") as m_seal,
    ):
        worker_actions.vault_seal()
    m_seal.assert_called_once_with(cfg=cfg, key="auto")


# ── Task lifecycle ────────────────────────────────────────────────────


def test_task_restart_and_stop_delegate_to_facade() -> None:
    """``task_restart`` / ``task_stop`` forward ``(project_id, task_id)`` to the facade."""
    with mock.patch("terok.lib.api.task_restart") as m_restart:
        worker_actions.task_restart("proj", "tid")
    m_restart.assert_called_once_with("proj", "tid")
    with mock.patch("terok.lib.api.task_stop") as m_stop:
        worker_actions.task_stop("proj", "tid")
    m_stop.assert_called_once_with("proj", "tid")
