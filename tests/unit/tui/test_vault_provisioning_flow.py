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

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from terok.lib.integrations.sandbox import VaultState
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


def _plan(
    *,
    provisioned: bool = False,
    auto_tier: str | None = None,
    keyring_available: bool = True,
) -> SimpleNamespace:
    """A ``ProvisioningPlan`` stand-in (the flow reads all four fields)."""
    return SimpleNamespace(
        provisioned=provisioned,
        auto_tier=auto_tier,
        choices=("keyring", "kernel-keyring"),
        keyring_available=keyring_available,
    )


class TestEnsureCredentialsProvisioned:
    """The pre-flight conversation that runs before the setup subprocess."""

    async def test_already_provisioned_short_circuits(self, flow_stub: SimpleNamespace) -> None:
        """A resolving tier / encrypted DB → no modals, setup may proceed."""
        with patch("terok.lib.api.vault.plan_provisioning", return_value=_plan(provisioned=True)):
            assert await TerokTUI._ensure_credentials_provisioned(flow_stub) is True
        flow_stub.push_screen_wait.assert_not_awaited()

    async def test_auto_tier_skips_chooser_and_reveals_mint(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """With systemd-creds available the strongest tier picks itself, CLI-style."""
        with (
            patch(
                "terok.lib.api.vault.plan_provisioning",
                return_value=_plan(auto_tier="systemd-creds"),
            ),
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
            patch("terok.lib.api.vault.plan_provisioning", return_value=_plan()),
            patch("terok.lib.api.vault.provision_passphrase_tier") as provision,
        ):
            assert await TerokTUI._ensure_credentials_provisioned(flow_stub) is False
        provision.assert_not_called()
        flow_stub._notify_provisioning_skipped.assert_called_once()

    async def test_create_cancel_skips_setup(self, flow_stub: SimpleNamespace) -> None:
        """Tier chosen but Esc in the create modal → False, nothing provisioned."""
        flow_stub.push_screen_wait.side_effect = ["keyring", None]
        with (
            patch("terok.lib.api.vault.plan_provisioning", return_value=_plan()),
            patch("terok.lib.api.vault.provision_passphrase_tier") as provision,
        ):
            assert await TerokTUI._ensure_credentials_provisioned(flow_stub) is False
        provision.assert_not_called()
        flow_stub._notify_provisioning_skipped.assert_called_once()

    async def test_generate_choice_mints_and_reveals(self, flow_stub: SimpleNamespace) -> None:
        """The recommended path: empty-string sentinel → mint → reveal + ack."""
        flow_stub.push_screen_wait.side_effect = ["keyring", ""]
        with (
            patch("terok.lib.api.vault.plan_provisioning", return_value=_plan()),
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
        flow_stub.push_screen_wait.side_effect = ["kernel-keyring", "hunter2-hunter2"]
        with (
            patch("terok.lib.api.vault.plan_provisioning", return_value=_plan()),
            patch(
                "terok.lib.api.vault.provision_passphrase_tier",
                return_value=_provision_result(generated=False),
            ) as provision,
        ):
            assert await TerokTUI._ensure_credentials_provisioned(flow_stub) is True
        assert provision.call_args.kwargs == {
            "tier": "kernel-keyring",
            "passphrase": "hunter2-hunter2",
        }
        flow_stub._reveal_new_passphrase.assert_not_awaited()

    async def test_provisioning_failure_notifies_and_blocks(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """A dead keyring backend surfaces as an error notify, setup does not run."""
        flow_stub.push_screen_wait.side_effect = ["keyring", ""]
        with (
            patch("terok.lib.api.vault.plan_provisioning", return_value=_plan()),
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
    """UNPROVISIONED → the unlock surfaces route to provisioning instead of a free-text key."""

    async def test_startup_probe_suppresses_modal_when_unprovisioned(self) -> None:
        """UNPROVISIONED on startup → no unlock modal (setup flow owns provisioning)."""
        status = SimpleNamespace(state=VaultState.UNPROVISIONED)
        stub = SimpleNamespace(
            _render_status_pill=MagicMock(),
            push_screen=AsyncMock(),
            _on_vault_unlock_result=MagicMock(),
        )
        with patch("terok.lib.api.vault.load_vault_status", return_value=status):
            await TerokTUI._refresh_vault_status(stub, push_modal_if_locked=True)
        stub.push_screen.assert_not_awaited()

    async def test_startup_probe_pushes_modal_when_locked(self) -> None:
        """A genuinely LOCKED vault → the validated unlock modal appears as before."""
        status = SimpleNamespace(state=VaultState.LOCKED)
        stub = SimpleNamespace(
            _render_status_pill=MagicMock(),
            push_screen=AsyncMock(),
            _on_vault_unlock_result=MagicMock(),
        )
        with patch("terok.lib.api.vault.load_vault_status", return_value=status):
            await TerokTUI._refresh_vault_status(stub, push_modal_if_locked=True)
        stub.push_screen.assert_awaited_once()

    async def test_palette_unlock_routes_to_provision_flow(self) -> None:
        """The palette action detects the UNPROVISIONED state and starts the create-flow."""
        status = SimpleNamespace(state=VaultState.UNPROVISIONED)
        stub = SimpleNamespace(
            _run_vault_provision_flow=MagicMock(),
            push_screen=AsyncMock(),
            _on_vault_unlock_result=MagicMock(),
        )
        with patch("terok.lib.api.vault.load_vault_status", return_value=status):
            await ProjectActionsMixin._action_vault_unlock(stub)
        stub._run_vault_provision_flow.assert_called_once()
        stub.push_screen.assert_not_awaited()

    async def test_palette_unlock_keeps_modal_when_locked(self) -> None:
        """On a LOCKED vault the palette action still opens the validated unlock modal."""
        status = SimpleNamespace(state=VaultState.LOCKED)
        stub = SimpleNamespace(
            _run_vault_provision_flow=MagicMock(),
            push_screen=AsyncMock(),
            _on_vault_unlock_result=MagicMock(),
        )
        with patch("terok.lib.api.vault.load_vault_status", return_value=status):
            await ProjectActionsMixin._action_vault_unlock(stub)
        stub._run_vault_provision_flow.assert_not_called()
        stub.push_screen.assert_awaited_once()

    async def test_palette_unlock_probe_failure_falls_back_to_modal(self) -> None:
        """A broken probe must not lock the operator out of the unlock prompt."""
        stub = SimpleNamespace(
            _run_vault_provision_flow=MagicMock(),
            push_screen=AsyncMock(),
            _on_vault_unlock_result=MagicMock(),
        )
        with patch(
            "terok.lib.api.vault.load_vault_status", side_effect=RuntimeError("probe broke")
        ):
            await ProjectActionsMixin._action_vault_unlock(stub)
        stub._run_vault_provision_flow.assert_not_called()
        stub.push_screen.assert_awaited_once()


class TestTierChooserModalRouting:
    """Button → dismissal value routing for the tier chooser."""

    @pytest.mark.parametrize(
        ("button_id", "expected"),
        [
            ("vault-tier-keyring", "keyring"),
            ("vault-tier-kernel", "kernel-keyring"),
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


class TestRevealAndSkipHelpers:
    """The reveal + ack helper and the shared cancel warning."""

    async def test_reveal_ack_writes_marker_and_refreshes(self) -> None:
        """'Mark as saved' → recovery marker + pill refresh."""
        stub = SimpleNamespace(
            push_screen_wait=AsyncMock(return_value=True),
            _refresh_vault_status=AsyncMock(),
        )
        with (
            patch("terok.tui.app.RecoveryStatus") as recovery,
            patch("terok.lib.core.config.make_sandbox_config"),
        ):
            await TerokTUI._reveal_new_passphrase(stub, "minted", "keyring")
        recovery.acknowledge.assert_called_once()
        stub._refresh_vault_status.assert_awaited_once()

    async def test_reveal_close_leaves_marker_alone(self) -> None:
        """Closing without the ack keeps the UNSAVED pill warning as the nudge."""
        stub = SimpleNamespace(
            push_screen_wait=AsyncMock(return_value=False),
            _refresh_vault_status=AsyncMock(),
        )
        with patch("terok.tui.app.RecoveryStatus") as recovery:
            await TerokTUI._reveal_new_passphrase(stub, "minted", "keyring")
        recovery.acknowledge.assert_not_called()
        stub._refresh_vault_status.assert_not_awaited()

    def test_skip_notice_is_a_warning(self) -> None:
        stub = SimpleNamespace(notify=MagicMock())
        TerokTUI._notify_provisioning_skipped(stub)
        assert stub.notify.call_args.kwargs.get("severity") == "warning"


# ── Pilot-driven modal tests (real Textual app) ─────────────────────────

_SENTINEL_PENDING = object()


def _modal_host(modal):  # noqa: ANN001, ANN202 — Textual App subclass built per test
    """Minimal test host that pushes *modal* and stashes its dismissal result.

    Uses the callback form of ``push_screen`` because ``push_screen_wait``
    requires a running worker — Textual's ``run_test`` does not provide
    one out of the box.  Same idiom as ``test_wizard_screens.py``.
    """
    from textual.app import App

    class _Host(App):
        def __init__(self) -> None:
            super().__init__()
            self.result: object = _SENTINEL_PENDING

        def on_mount(self) -> None:
            self.push_screen(modal, self._capture)

        def _capture(self, result: object) -> None:
            self.result = result

    return _Host()


class TestTierChooserModalPilot:
    """The chooser rendered in a real Textual app."""

    async def test_keyring_button_dismisses_with_tier(self) -> None:
        from terok.tui.screens import VaultTierChooserModal

        app = _modal_host(VaultTierChooserModal(keyring_available=True))
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click("#vault-tier-keyring")
            await pilot.pause()
        assert app.result == "keyring"

    async def test_unreachable_keyring_disables_the_recommended_button(self) -> None:
        from textual.widgets import Button

        from terok.tui.screens import VaultTierChooserModal

        modal = VaultTierChooserModal(keyring_available=False)
        app = _modal_host(modal)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert modal.query_one("#vault-tier-keyring", Button).disabled
            await pilot.click("#vault-tier-kernel")
            await pilot.pause()
        assert app.result == "kernel-keyring"


class TestCreatePassphraseModalPilot:
    """The create modal's live must-match gate, driven through real Inputs."""

    async def test_generate_is_default_and_dismisses_with_sentinel(self) -> None:
        from terok.tui.screens import VaultCreatePassphraseModal

        app = _modal_host(VaultCreatePassphraseModal())
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click("#vault-create-generate")
            await pilot.pause()
        assert app.result == ""

    async def test_typed_button_enables_only_on_match(self) -> None:
        from textual.widgets import Button, Input, Static

        from terok.tui.screens import VaultCreatePassphraseModal

        modal = VaultCreatePassphraseModal()
        app = _modal_host(modal)
        async with app.run_test() as pilot:
            await pilot.pause()
            typed_button = modal.query_one("#vault-create-typed", Button)
            assert typed_button.disabled  # nothing entered yet

            modal.query_one("#vault-create-input", Input).value = "s3cret-one"
            modal.query_one("#vault-create-repeat", Input).value = "s3cret-two"
            await pilot.pause()
            assert typed_button.disabled
            hint = modal.query_one("#vault-create-mismatch", Static)
            assert "do not match" in str(hint.render())

            modal.query_one("#vault-create-repeat", Input).value = "s3cret-one"
            await pilot.pause()
            assert not typed_button.disabled

            await pilot.click("#vault-create-typed")
            await pilot.pause()
        assert app.result == "s3cret-one"


class TestProbeFailureSurfaces:
    """A broken durable tier fails the pre-flight loudly, not as a silent worker error."""

    async def test_probe_exception_notifies_and_blocks(self, flow_stub: SimpleNamespace) -> None:
        with patch(
            "terok.lib.api.vault.plan_provisioning",
            side_effect=RuntimeError("sealed credential present but could not be unsealed"),
        ):
            assert await TerokTUI._ensure_credentials_provisioned(flow_stub) is False
        flow_stub.push_screen_wait.assert_not_awaited()
        messages = [str(c.args[0]) for c in flow_stub.notify.call_args_list]
        assert any("could not be unsealed" in m for m in messages)
