# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the TUI's validated unlock flow and the live vault pill.

``_on_vault_unlock_result`` funnels through sandbox's
``provision_session_passphrase`` — the same validated writer the CLI
uses — so a wrong entry is rejected with an explanation instead of
written-and-reported-as-success (issue #1070's failure mode).  The
focus-driven adaptive poll keeps the pill live when ``vault unlock`` /
``lock`` runs in another terminal.  Both are driven
unbound-method-on-stub, same idiom as ``test_app_quit.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from terok.lib.integrations.sandbox import VaultState, WrongPassphraseError
from terok.tui.app import TerokTUI


@pytest.fixture
def unlock_stub() -> SimpleNamespace:
    """Duck for ``_on_vault_unlock_result`` — notify + refresh seams."""
    return SimpleNamespace(
        notify=MagicMock(),
        _refresh_vault_status=AsyncMock(),
    )


class TestOnVaultUnlockResult:
    """The modal's result lands via the validated writer."""

    @staticmethod
    def _result(*, written: bool = True, shadowed_durable: str | None = None) -> SimpleNamespace:
        """A ``SessionProvisionResult`` stand-in (duck-typed: handler reads two fields)."""
        return SimpleNamespace(written=written, validated=True, shadowed_durable=shadowed_durable)

    async def test_valid_passphrase_provisions_and_refreshes(
        self, unlock_stub: SimpleNamespace
    ) -> None:
        with patch(
            "terok.lib.api.vault.provision_session_passphrase", return_value=self._result()
        ) as provision:
            await TerokTUI._on_vault_unlock_result(unlock_stub, "correct-horse")
        provision.assert_called_once()
        assert provision.call_args[0][1] == "correct-horse"
        # Success notify + pill re-probe.
        assert any("unlocked" in str(c.args[0]) for c in unlock_stub.notify.call_args_list)
        unlock_stub._refresh_vault_status.assert_awaited_once()

    async def test_durable_shadow_refused_informs_no_write(
        self, unlock_stub: SimpleNamespace
    ) -> None:
        """When a durable tier already resolves, the writer refuses → info notify, no refresh.

        This is the exact TUI vector that used to create the #1070 shadow:
        the modal calling the writer directly on an already-unlocked box.
        """
        with patch(
            "terok.lib.api.vault.provision_session_passphrase",
            return_value=self._result(written=False, shadowed_durable="systemd-creds"),
        ):
            await TerokTUI._on_vault_unlock_result(unlock_stub, "redundant")
        messages = [str(c.args[0]) for c in unlock_stub.notify.call_args_list]
        assert any("already auto-unlocks via systemd-creds" in m for m in messages)
        assert not any("unlocked for this session" in m for m in messages)
        unlock_stub._refresh_vault_status.assert_not_awaited()

    async def test_wrong_passphrase_notifies_and_writes_nothing(
        self, unlock_stub: SimpleNamespace
    ) -> None:
        """A rejected value surfaces as an error — no success toast, no refresh."""
        with patch(
            "terok.lib.api.vault.provision_session_passphrase",
            side_effect=WrongPassphraseError("could not decrypt"),
        ):
            await TerokTUI._on_vault_unlock_result(unlock_stub, "wrong-guess")
        messages = [str(c.args[0]) for c in unlock_stub.notify.call_args_list]
        assert any("does not open the credentials DB" in m for m in messages)
        assert not any("unlocked" in m for m in messages)
        unlock_stub._refresh_vault_status.assert_not_awaited()

    async def test_empty_result_is_a_noop(self, unlock_stub: SimpleNamespace) -> None:
        """Dismissing the modal (None / empty) must not touch the vault."""
        with patch("terok.lib.api.vault.provision_session_passphrase") as provision:
            await TerokTUI._on_vault_unlock_result(unlock_stub, None)
            await TerokTUI._on_vault_unlock_result(unlock_stub, "")
        provision.assert_not_called()
        unlock_stub.notify.assert_not_called()


class TestStatusPillLockReason:
    """The pill names the lock state instead of a bare LOCKED."""

    @staticmethod
    def _pill_message(status: SimpleNamespace) -> str:
        bar = MagicMock()
        stub = SimpleNamespace(query_one=MagicMock(return_value=bar))
        TerokTUI._render_status_pill(stub, status)
        return str(bar.set_message.call_args[0][0])

    def test_locked_pill_carries_reason(self) -> None:
        message = self._pill_message(
            SimpleNamespace(
                state=VaultState.LOCKED,
                lock_reason="no passphrase in any tier",
                source=None,
            )
        )
        assert "LOCKED" in message
        assert "no passphrase in any tier" in message

    def test_locked_pill_without_reason_keeps_generic_text(self) -> None:
        message = self._pill_message(
            SimpleNamespace(
                state=VaultState.LOCKED,
                lock_reason=None,
                source=None,
            )
        )
        assert "LOCKED" in message
        assert "(" not in message  # no empty-reason parens


class TestVaultPoll:
    """Focus-gated adaptive poll keeps the pill live across external unlock/lock.

    The inotify session-dir watcher was removed with the volatile
    session-file tier — the kernel-keyring cache has no watchable file.
    A focus-driven interval poll replaces it: blur pauses the backstop
    timer, focus resumes it and fires an immediate re-probe, and each
    tick runs an exclusive ``vault-poll`` worker over
    ``_refresh_vault_status``.
    """

    def test_blur_pauses_the_poll_timer(self) -> None:
        """Losing focus pauses the backstop interval — no vault work while unseen."""
        timer = MagicMock()
        stub = SimpleNamespace(_vault_poll_timer=timer)
        TerokTUI.on_app_blur(stub)
        timer.pause.assert_called_once()

    def test_blur_without_timer_is_a_noop(self) -> None:
        """Terminals that never armed the timer must not crash on blur."""
        stub = SimpleNamespace(_vault_poll_timer=None)
        TerokTUI.on_app_blur(stub)  # no AttributeError, nothing to pause

    def test_focus_resumes_timer_and_schedules_refresh(self) -> None:
        """Regaining focus resumes the timer and fires an immediate re-probe."""
        timer = MagicMock()
        stub = SimpleNamespace(
            is_web=False,
            _vault_poll_timer=timer,
            _check_for_update=MagicMock(),
            _schedule_vault_refresh=MagicMock(),
        )
        TerokTUI.on_app_focus(stub)
        timer.resume.assert_called_once()
        stub._schedule_vault_refresh.assert_called_once()
        # Focus-in still drives the existing update probe too.
        stub._check_for_update.assert_called_once()

    def test_focus_without_timer_still_refreshes(self) -> None:
        """A missing timer doesn't block the immediate focus-in re-probe."""
        stub = SimpleNamespace(
            is_web=False,
            _vault_poll_timer=None,
            _check_for_update=MagicMock(),
            _schedule_vault_refresh=MagicMock(),
        )
        TerokTUI.on_app_focus(stub)
        stub._schedule_vault_refresh.assert_called_once()

    def test_refresh_pill_now_runs_exclusive_vault_worker(self) -> None:
        """The one-shot re-probe hands ``_refresh_vault_status`` to an exclusive worker."""
        sentinel = object()
        stub = SimpleNamespace(
            run_worker=MagicMock(),
            _refresh_vault_status=MagicMock(return_value=sentinel),
        )
        TerokTUI._schedule_vault_refresh(stub)
        stub._refresh_vault_status.assert_called_once_with()
        stub.run_worker.assert_called_once()
        args, kwargs = stub.run_worker.call_args
        assert args[0] is sentinel
        assert kwargs["group"] == "vault-poll"
        assert kwargs["exclusive"] is True
