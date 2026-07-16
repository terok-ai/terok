# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the TUI's change-passphrase conversation.

``_run_vault_change_flow`` is the TUI rendering of ``vault passphrase
change``: probe → (current-passphrase modal only when locked) → create
modal → sandbox ``change_passphrase`` → reveal + re-ack.  These tests
pin the branching and the failure notifications, driven
unbound-method-on-stub (via the worker decorator's ``__wrapped__``),
same idiom as ``test_vault_provisioning_flow.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from terok.lib.integrations.sandbox import VaultState, WrongPassphraseError
from terok.tui.app import TerokTUI

_change_flow = TerokTUI._run_vault_change_flow.__wrapped__


@pytest.fixture
def flow_stub() -> SimpleNamespace:
    """Duck for ``_run_vault_change_flow`` — modal + notify + refresh seams."""
    return SimpleNamespace(
        push_screen_wait=AsyncMock(),
        notify=MagicMock(),
        _refresh_vault_status=AsyncMock(),
        _reveal_new_passphrase=AsyncMock(),
        _last_vault_status=None,
    )


def _status(state: VaultState) -> SimpleNamespace:
    """A ``VaultStatus`` stand-in (the flow reads only ``state``)."""
    return SimpleNamespace(state=state)


def _change_result(*, generated: bool = False, problems: tuple = ()) -> SimpleNamespace:
    """A ``PassphraseChangeResult`` stand-in."""
    return SimpleNamespace(
        passphrase="the-new-passphrase",
        generated=generated,
        rekeyed=True,
        rewrites=(),
        problems=problems,
    )


class TestRunVaultChangeFlow:
    """Branching of the change conversation."""

    async def test_unprovisioned_refuses_without_modals(self, flow_stub: SimpleNamespace) -> None:
        """Nothing to change on a fresh install — points at setup instead."""
        with patch(
            "terok.lib.api.vault.load_vault_status",
            return_value=_status(VaultState.UNPROVISIONED),
        ):
            await _change_flow(flow_stub)
        flow_stub.push_screen_wait.assert_not_awaited()
        assert flow_stub.notify.call_args.kwargs["severity"] == "warning"

    async def test_unlocked_skips_the_current_passphrase_modal(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """A resolvable tier means retyping the old value is theatre — one modal only."""
        flow_stub.push_screen_wait.return_value = "typed-new"
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch(
                "terok.lib.api.vault.change_passphrase",
                return_value=_change_result(),
            ) as change,
        ):
            await _change_flow(flow_stub)
        assert flow_stub.push_screen_wait.await_count == 1  # create modal only
        assert change.call_args.kwargs == {"old": None, "new": "typed-new"}
        flow_stub._refresh_vault_status.assert_awaited_once()
        flow_stub._reveal_new_passphrase.assert_awaited_once()

    async def test_locked_asks_for_the_current_passphrase_first(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """A locked vault needs the old key — cryptographic necessity, not ceremony."""
        flow_stub.push_screen_wait.side_effect = ["old-pass", "typed-new"]
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.LOCKED),
            ),
            patch(
                "terok.lib.api.vault.change_passphrase",
                return_value=_change_result(),
            ) as change,
        ):
            await _change_flow(flow_stub)
        assert flow_stub.push_screen_wait.await_count == 2
        assert change.call_args.kwargs == {"old": "old-pass", "new": "typed-new"}

    async def test_cancel_on_create_modal_changes_nothing(self, flow_stub: SimpleNamespace) -> None:
        """Backing out of the create modal must leave the vault untouched."""
        flow_stub.push_screen_wait.return_value = None
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch("terok.lib.api.vault.change_passphrase") as change,
        ):
            await _change_flow(flow_stub)
        change.assert_not_called()
        flow_stub._reveal_new_passphrase.assert_not_awaited()

    async def test_empty_entry_mints_and_reveals(self, flow_stub: SimpleNamespace) -> None:
        """The create modal's generate sentinel ('') becomes ``new=None`` → sandbox mints."""
        flow_stub.push_screen_wait.return_value = ""
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch(
                "terok.lib.api.vault.change_passphrase",
                return_value=_change_result(generated=True),
            ) as change,
        ):
            await _change_flow(flow_stub)
        assert change.call_args.kwargs["new"] is None
        reveal_args = flow_stub._reveal_new_passphrase.await_args.args
        assert reveal_args[0] == "the-new-passphrase"

    async def test_wrong_passphrase_notifies_and_stops(self, flow_stub: SimpleNamespace) -> None:
        """A wrong current passphrase surfaces as an error and skips the reveal."""
        flow_stub.push_screen_wait.side_effect = ["bad-old", "typed-new"]
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.LOCKED),
            ),
            patch(
                "terok.lib.api.vault.change_passphrase",
                side_effect=WrongPassphraseError("nope"),
            ),
        ):
            await _change_flow(flow_stub)
        assert flow_stub.notify.call_args.kwargs["severity"] == "error"
        flow_stub._reveal_new_passphrase.assert_not_awaited()

    async def test_locked_db_names_the_running_task_remedy(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """A live supervisor holding the DB aborts cleanly with the stop-tasks hint."""
        flow_stub.push_screen_wait.return_value = "typed-new"
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch(
                "terok.lib.api.vault.change_passphrase",
                side_effect=RuntimeError("database is locked"),
            ),
        ):
            await _change_flow(flow_stub)
        message = flow_stub.notify.call_args.args[0]
        assert "running task" in message
        flow_stub._reveal_new_passphrase.assert_not_awaited()

    async def test_tier_problems_surface_loudly_but_still_reveal(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """A failed tier rewrite must not scroll past — and the re-ack still runs."""
        flow_stub.push_screen_wait.return_value = "typed-new"
        problem = SimpleNamespace(tier="keyring", ok=False, detail="keyring write failed")
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch(
                "terok.lib.api.vault.change_passphrase",
                return_value=_change_result(problems=(problem,)),
            ),
        ):
            await _change_flow(flow_stub)
        assert flow_stub.notify.call_args.kwargs["severity"] == "error"
        assert "keyring write failed" in flow_stub.notify.call_args.args[0]
        flow_stub._reveal_new_passphrase.assert_awaited_once()
