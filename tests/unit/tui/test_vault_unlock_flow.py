# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the TUI's validated unlock flow and the live vault pill.

``_on_vault_unlock_result`` funnels through sandbox's
``provision_session_passphrase`` — the same validated writer the CLI
uses — so a wrong entry is rejected with an explanation instead of
written-and-reported-as-success (issue #1070's failure mode).  The
vault watcher keeps the pill live when ``vault unlock`` / ``lock``
runs in another terminal.  Both are driven unbound-method-on-stub,
same idiom as ``test_app_quit.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from terok.lib.integrations.sandbox import WrongPassphraseError
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
                locked=True,
                lock_reason="no passphrase in any tier",
                plaintext_passphrase_path=None,
                passphrase_source=None,
            )
        )
        assert "LOCKED" in message
        assert "no passphrase in any tier" in message

    def test_locked_pill_without_reason_keeps_generic_text(self) -> None:
        message = self._pill_message(
            SimpleNamespace(
                locked=True,
                lock_reason=None,
                plaintext_passphrase_path=None,
                passphrase_source=None,
            )
        )
        assert "LOCKED" in message
        assert "(" not in message  # no empty-reason parens


class TestVaultWatcher:
    """Session-dir inotify keeps the pill live across external unlock/lock."""

    async def test_start_arms_watch_on_session_dir(self, tmp_path, monkeypatch) -> None:
        """The watcher arms on the (created-if-missing) session-file directory."""
        session_file = tmp_path / "sandbox" / "vault.passphrase"
        cfg = MagicMock()
        cfg.vault_passphrase_file = session_file
        # Patch the method's own globals, not "terok.tui.app": other TUI
        # tests re-import the app module (import_app/import_fresh), so the
        # sys.modules entry can be a *different* module object than the one
        # our top-of-file ``TerokTUI`` resolves names from.
        monkeypatch.setitem(TerokTUI._start_vault_watcher.__globals__, "SandboxConfig", lambda: cfg)
        stub = SimpleNamespace(
            _vault_watcher=None,
            _on_vault_session_dir_changed=MagicMock(),
        )
        try:
            TerokTUI._start_vault_watcher(stub)
            assert stub._vault_watcher is not None
            assert stub._vault_watcher.fileno >= 0
            assert session_file.parent.is_dir()  # created so the watch could arm now
        finally:
            if stub._vault_watcher is not None:
                stub._vault_watcher.stop()

    def test_event_debounces_one_refresh(self) -> None:
        """inotify activity collapses into a single timer-scheduled refresh."""
        timer = MagicMock()
        stub = SimpleNamespace(
            _vault_watcher=MagicMock(drain=MagicMock(return_value=True)),
            _vault_watch_debounce=None,
            set_timer=MagicMock(return_value=timer),
            _on_vault_watch_fired=MagicMock(),
        )
        TerokTUI._on_vault_session_dir_changed(stub)
        stub.set_timer.assert_called_once()
        assert stub._vault_watch_debounce is timer
        # A second burst restarts the pending timer rather than stacking.
        TerokTUI._on_vault_session_dir_changed(stub)
        timer.stop.assert_called_once()

    def test_drained_empty_is_a_noop(self) -> None:
        stub = SimpleNamespace(
            _vault_watcher=MagicMock(drain=MagicMock(return_value=False)),
            _vault_watch_debounce=None,
            set_timer=MagicMock(),
        )
        TerokTUI._on_vault_session_dir_changed(stub)
        stub.set_timer.assert_not_called()

    async def test_fired_debounce_refreshes(self) -> None:
        stub = SimpleNamespace(
            _vault_watch_debounce=MagicMock(),
            _refresh_vault_status=AsyncMock(),
        )
        await TerokTUI._on_vault_watch_fired(stub)
        assert stub._vault_watch_debounce is None
        stub._refresh_vault_status.assert_awaited_once()

    def test_stop_detaches_and_closes(self) -> None:
        watcher = MagicMock(fileno=7)
        debounce = MagicMock()
        stub = SimpleNamespace(_vault_watcher=watcher, _vault_watch_debounce=debounce)
        TerokTUI._stop_vault_watcher(stub)
        debounce.stop.assert_called_once()
        watcher.stop.assert_called_once()
        assert stub._vault_watcher is None
        assert stub._vault_watch_debounce is None
