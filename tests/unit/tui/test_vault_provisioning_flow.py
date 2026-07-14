# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the TUI's first-passphrase provisioning conversation.

``terok setup`` runs as a captured, TTY-less subprocess, so sandbox's
credentials phase can neither show its chooser nor announce a fresh
mint — the TUI collects the tier choice up front
(``_ensure_credentials_provisioned``) and provisions in-process before
dispatching.  These tests pin the conversation's branching, the
fresh-install guard that keeps the unlock modal from silently keying a
brand-new vault, and the two new modals' dismissal routing.  All driven
unbound-method-on-stub, same idiom as ``test_vault_unlock_flow.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from terok.tui.app import TerokTUI
from terok.tui.project_actions import ProjectActionsMixin


@pytest.fixture
def flow_stub() -> SimpleNamespace:
    """Duck for ``_ensure_credentials_provisioned`` — modal + notify seams."""
    return SimpleNamespace(
        push_screen_wait=AsyncMock(),
        notify=MagicMock(),
        _notify_provisioning_skipped=MagicMock(),
        _refresh_vault_status=AsyncMock(),
        _reveal_new_passphrase=AsyncMock(),
    )


def _provision_result(*, generated: bool) -> SimpleNamespace:
    """A ``TierProvisionResult`` stand-in (the flow reads three fields)."""
    return SimpleNamespace(passphrase="minted-or-typed", source="keyring", generated=generated)


class TestEnsureCredentialsProvisioned:
    """The pre-flight conversation that runs before the setup subprocess."""

    async def test_already_provisioned_short_circuits(self, flow_stub: SimpleNamespace) -> None:
        """A resolving tier / encrypted DB → no modals, setup may proceed."""
        with patch("terok.lib.api.vault.credentials_provisioned", return_value=True):
            assert await TerokTUI._ensure_credentials_provisioned(flow_stub) is True
        flow_stub.push_screen_wait.assert_not_awaited()

    async def test_systemd_creds_skips_chooser_and_reveals_mint(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """With systemd-creds available the strongest tier picks itself, CLI-style."""
        with (
            patch("terok.lib.api.vault.credentials_provisioned", return_value=False),
            patch("terok.lib.api.vault.systemd_creds_available", return_value=True),
            patch(
                "terok.lib.api.vault.provision_passphrase_tier",
                return_value=_provision_result(generated=True),
            ) as provision,
        ):
            assert await TerokTUI._ensure_credentials_provisioned(flow_stub) is True
        flow_stub.push_screen_wait.assert_not_awaited()
        assert provision.call_args.kwargs == {"tier": "systemd-creds", "passphrase": None}
        flow_stub._reveal_new_passphrase.assert_awaited_once_with("minted-or-typed", "keyring")

    async def test_chooser_cancel_skips_setup(self, flow_stub: SimpleNamespace) -> None:
        """Esc in the tier chooser → False, nothing provisioned, one warning."""
        flow_stub.push_screen_wait.return_value = None
        with (
            patch("terok.lib.api.vault.credentials_provisioned", return_value=False),
            patch("terok.lib.api.vault.systemd_creds_available", return_value=False),
            patch("terok.lib.api.vault.keyring_backend_available", return_value=True),
            patch("terok.lib.api.vault.provision_passphrase_tier") as provision,
        ):
            assert await TerokTUI._ensure_credentials_provisioned(flow_stub) is False
        provision.assert_not_called()
        flow_stub._notify_provisioning_skipped.assert_called_once()

    async def test_create_cancel_skips_setup(self, flow_stub: SimpleNamespace) -> None:
        """Tier chosen but Esc in the create modal → False, nothing provisioned."""
        flow_stub.push_screen_wait.side_effect = ["keyring", None]
        with (
            patch("terok.lib.api.vault.credentials_provisioned", return_value=False),
            patch("terok.lib.api.vault.systemd_creds_available", return_value=False),
            patch("terok.lib.api.vault.keyring_backend_available", return_value=True),
            patch("terok.lib.api.vault.provision_passphrase_tier") as provision,
        ):
            assert await TerokTUI._ensure_credentials_provisioned(flow_stub) is False
        provision.assert_not_called()
        flow_stub._notify_provisioning_skipped.assert_called_once()

    async def test_generate_choice_mints_and_reveals(self, flow_stub: SimpleNamespace) -> None:
        """The recommended path: empty-string sentinel → mint → reveal + ack."""
        flow_stub.push_screen_wait.side_effect = ["keyring", ""]
        with (
            patch("terok.lib.api.vault.credentials_provisioned", return_value=False),
            patch("terok.lib.api.vault.systemd_creds_available", return_value=False),
            patch("terok.lib.api.vault.keyring_backend_available", return_value=True),
            patch(
                "terok.lib.api.vault.provision_passphrase_tier",
                return_value=_provision_result(generated=True),
            ) as provision,
        ):
            assert await TerokTUI._ensure_credentials_provisioned(flow_stub) is True
        assert provision.call_args.kwargs == {"tier": "keyring", "passphrase": None}
        flow_stub._reveal_new_passphrase.assert_awaited_once()
        flow_stub._refresh_vault_status.assert_awaited()

    async def test_typed_choice_lands_verbatim_without_reveal(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """A twice-confirmed typed value is something the operator knows — no reveal."""
        flow_stub.push_screen_wait.side_effect = ["session-file", "hunter2-hunter2"]
        with (
            patch("terok.lib.api.vault.credentials_provisioned", return_value=False),
            patch("terok.lib.api.vault.systemd_creds_available", return_value=False),
            patch("terok.lib.api.vault.keyring_backend_available", return_value=True),
            patch(
                "terok.lib.api.vault.provision_passphrase_tier",
                return_value=_provision_result(generated=False),
            ) as provision,
        ):
            assert await TerokTUI._ensure_credentials_provisioned(flow_stub) is True
        assert provision.call_args.kwargs == {
            "tier": "session-file",
            "passphrase": "hunter2-hunter2",
        }
        flow_stub._reveal_new_passphrase.assert_not_awaited()

    async def test_provisioning_failure_notifies_and_blocks(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """A dead keyring backend surfaces as an error notify, setup does not run."""
        flow_stub.push_screen_wait.side_effect = ["keyring", ""]
        with (
            patch("terok.lib.api.vault.credentials_provisioned", return_value=False),
            patch("terok.lib.api.vault.systemd_creds_available", return_value=False),
            patch("terok.lib.api.vault.keyring_backend_available", return_value=True),
            patch(
                "terok.lib.api.vault.provision_passphrase_tier",
                side_effect=RuntimeError("OS keyring is unreachable"),
            ),
        ):
            assert await TerokTUI._ensure_credentials_provisioned(flow_stub) is False
        messages = [str(c.args[0]) for c in flow_stub.notify.call_args_list]
        assert any("OS keyring is unreachable" in m for m in messages)


class TestSetupSubprocessGating:
    """The pre-flight gates the subprocess dispatch."""

    async def test_declined_provisioning_blocks_dispatch(self) -> None:
        """False from the pre-flight → no console command, flow reports failure."""
        stub = SimpleNamespace(
            _ensure_credentials_provisioned=AsyncMock(return_value=False),
            dispatch_console_command=MagicMock(),
        )
        assert await TerokTUI._run_setup_subprocess(stub) is False
        stub.dispatch_console_command.assert_not_called()


class TestFreshInstallUnlockGuard:
    """No DB → the unlock surfaces route to provisioning instead of a free-text key."""

    async def test_startup_probe_suppresses_modal_without_db(self, tmp_path: Path) -> None:
        """Locked + no DB on startup → no unlock modal (setup flow owns provisioning)."""
        status = SimpleNamespace(locked=True, db_path=str(tmp_path / "absent.db"))
        stub = SimpleNamespace(
            _render_status_pill=MagicMock(),
            push_screen=AsyncMock(),
            _on_vault_unlock_result=MagicMock(),
        )
        with patch("terok.tui.app.VaultStatusSnapshot") as snapshot_cls:
            snapshot_cls.load.return_value = status
            await TerokTUI._refresh_vault_status(stub, push_modal_if_locked=True)
        stub.push_screen.assert_not_awaited()

    async def test_startup_probe_pushes_modal_with_db(self, tmp_path: Path) -> None:
        """Locked + a real DB file → the validated unlock modal appears as before."""
        db = tmp_path / "credentials.db"
        db.write_bytes(b"x")
        status = SimpleNamespace(locked=True, db_path=str(db))
        stub = SimpleNamespace(
            _render_status_pill=MagicMock(),
            push_screen=AsyncMock(),
            _on_vault_unlock_result=MagicMock(),
        )
        with patch("terok.tui.app.VaultStatusSnapshot") as snapshot_cls:
            snapshot_cls.load.return_value = status
            await TerokTUI._refresh_vault_status(stub, push_modal_if_locked=True)
        stub.push_screen.assert_awaited_once()

    async def test_palette_unlock_routes_to_provision_flow(self, tmp_path: Path) -> None:
        """The palette action detects the missing DB and starts the create-flow."""
        cfg = SimpleNamespace(db_path=tmp_path / "absent.db")
        stub = SimpleNamespace(
            _run_vault_provision_flow=MagicMock(),
            push_screen=AsyncMock(),
            _on_vault_unlock_result=MagicMock(),
        )
        with patch("terok.lib.api.SandboxConfig", return_value=cfg):
            await ProjectActionsMixin._action_vault_unlock(stub)
        stub._run_vault_provision_flow.assert_called_once()
        stub.push_screen.assert_not_awaited()

    async def test_palette_unlock_keeps_modal_with_db(self, tmp_path: Path) -> None:
        """With a DB present the palette action still opens the validated unlock modal."""
        db = tmp_path / "credentials.db"
        db.write_bytes(b"x")
        cfg = SimpleNamespace(db_path=db)
        stub = SimpleNamespace(
            _run_vault_provision_flow=MagicMock(),
            push_screen=AsyncMock(),
            _on_vault_unlock_result=MagicMock(),
        )
        with patch("terok.lib.api.SandboxConfig", return_value=cfg):
            await ProjectActionsMixin._action_vault_unlock(stub)
        stub._run_vault_provision_flow.assert_not_called()
        stub.push_screen.assert_awaited_once()


class TestTierChooserModalRouting:
    """Button → dismissal value routing for the tier chooser."""

    @pytest.mark.parametrize(
        ("button_id", "expected"),
        [
            ("vault-tier-keyring", "keyring"),
            ("vault-tier-session", "session-file"),
            ("vault-tier-cancel", None),
        ],
    )
    def test_buttons_dismiss_with_tier(self, button_id: str, expected: str | None) -> None:
        from terok.tui.screens import VaultTierChooserModal

        modal = VaultTierChooserModal(keyring_available=True)
        modal.dismiss = MagicMock()
        event = MagicMock()
        event.button.id = button_id
        modal.on_button_pressed(event)
        modal.dismiss.assert_called_once_with(expected)

    def test_escape_cancels(self) -> None:
        from terok.tui.screens import VaultTierChooserModal

        modal = VaultTierChooserModal(keyring_available=False)
        modal.dismiss = MagicMock()
        modal.action_cancel()
        modal.dismiss.assert_called_once_with(None)


class TestCreatePassphraseModalRouting:
    """Generate / typed / cancel routing, including the must-match gate."""

    @staticmethod
    def _modal_with_fields(first: str, second: str):
        from terok.tui.screens import VaultCreatePassphraseModal

        modal = VaultCreatePassphraseModal()
        modal.dismiss = MagicMock()
        modal._typed_values = MagicMock(return_value=(first, second))
        return modal

    def test_generate_dismisses_with_sentinel(self) -> None:
        """The recommended button returns the empty-string 'mint one for me' sentinel."""
        modal = self._modal_with_fields("", "")
        event = MagicMock()
        event.button.id = "vault-create-generate"
        modal.on_button_pressed(event)
        modal.dismiss.assert_called_once_with("")

    def test_typed_requires_matching_fields(self) -> None:
        """Mismatched fields never dismiss — the modal stays up for a correction."""
        modal = self._modal_with_fields("one", "two")
        event = MagicMock()
        event.button.id = "vault-create-typed"
        modal.on_button_pressed(event)
        modal.dismiss.assert_not_called()

    def test_typed_matching_fields_dismiss_with_value(self) -> None:
        modal = self._modal_with_fields("s3cret-s3cret", "s3cret-s3cret")
        event = MagicMock()
        event.button.id = "vault-create-typed"
        modal.on_button_pressed(event)
        modal.dismiss.assert_called_once_with("s3cret-s3cret")

    def test_empty_match_never_dismisses(self) -> None:
        """Two empty fields 'match' but an empty passphrase means no encryption — refuse."""
        modal = self._modal_with_fields("", "")
        event = MagicMock()
        event.button.id = "vault-create-typed"
        modal.on_button_pressed(event)
        modal.dismiss.assert_not_called()

    def test_cancel_dismisses_none(self) -> None:
        modal = self._modal_with_fields("", "")
        event = MagicMock()
        event.button.id = "vault-create-cancel"
        modal.on_button_pressed(event)
        modal.dismiss.assert_called_once_with(None)
