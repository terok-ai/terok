# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for [`TerokTUI.action_quit`][terok.tui.app] background-worker reporting.

Pins the user-visible bit of the quit flow: if real-work workers (task
delete, image build, etc.) are still in flight after the polling loops
are torn down, surface a Textual exit message so the user knows the
terminal isn't hung — it's just waiting for threads to drain before
returning the prompt.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.worker import WorkerState

from terok.tui import tmux_session
from terok.tui.app import _RESTART_EXIT_RESULT, TerokTUI
from terok.tui.screens import QuitConfirmScreen, TmuxQuitScreen


def _worker(state: WorkerState, group: str = "") -> SimpleNamespace:
    """Build a fake [`Worker`][textual.worker.Worker] just enough for the quit check."""
    return SimpleNamespace(state=state, group=group, name="fake")


@pytest.fixture
def quit_stub() -> SimpleNamespace:
    """Tiny duck for ``TerokTUI.action_quit`` — just the attrs the method touches."""
    return SimpleNamespace(
        workers=[],
        _askpass_service=None,
        _stop_upstream_polling=MagicMock(),
        _stop_container_status_polling=MagicMock(),
        _stop_gate_server_polling=MagicMock(),
        _stop_vault_watcher=MagicMock(),
        exit=MagicMock(),
    )


class TestActionQuit:
    """``action_quit`` tears down polling, then exits — with a message iff work is pending."""

    @pytest.mark.asyncio
    async def test_silent_exit_when_no_workers_pending(self, quit_stub: SimpleNamespace) -> None:
        """No in-flight workers ⇒ plain ``exit()`` with no message."""
        await TerokTUI.action_quit(quit_stub)
        quit_stub.exit.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_finished_workers_dont_trigger_message(self, quit_stub: SimpleNamespace) -> None:
        """Workers in terminal states (success / error / cancelled) are not in-flight."""
        quit_stub.workers = [
            _worker(WorkerState.SUCCESS, "task-delete"),
            _worker(WorkerState.ERROR, "image-build"),
            _worker(WorkerState.CANCELLED, "container-state"),
        ]
        await TerokTUI.action_quit(quit_stub)
        quit_stub.exit.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_pending_work_surfaces_in_exit_message(self, quit_stub: SimpleNamespace) -> None:
        """One ``RUNNING`` delete ⇒ a count-and-group exit message Textual prints after cleanup."""
        quit_stub.workers = [_worker(WorkerState.RUNNING, "task-delete")]
        await TerokTUI.action_quit(quit_stub)
        message = quit_stub.exit.call_args.kwargs["message"]
        assert "1 background task" in message
        assert "task-delete" in message

    @pytest.mark.asyncio
    async def test_multiple_groups_listed_sorted(self, quit_stub: SimpleNamespace) -> None:
        """Mixed in-flight groups appear together, sorted, in a single ``(group, group)`` suffix."""
        quit_stub.workers = [
            _worker(WorkerState.RUNNING, "task-delete"),
            _worker(WorkerState.PENDING, "image-build"),
            _worker(WorkerState.RUNNING, "task-delete"),
        ]
        await TerokTUI.action_quit(quit_stub)
        message = quit_stub.exit.call_args.kwargs["message"]
        assert "3 background task" in message
        assert "(image-build, task-delete)" in message

    @pytest.mark.asyncio
    async def test_askpass_service_stopped_before_exit(self, quit_stub: SimpleNamespace) -> None:
        """A bound askpass service must be torn down before exit (no orphaned socket)."""
        quit_stub._askpass_service = SimpleNamespace(stop=AsyncMock())
        await TerokTUI.action_quit(quit_stub)
        quit_stub._askpass_service.stop.assert_awaited_once()
        quit_stub.exit.assert_called_once()

    @pytest.mark.asyncio
    async def test_plain_quit_flashes_the_tmux_exit_hint(
        self, quit_stub: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A normal quit leaves the status-line breadcrumb (no-op outside terok tmux)."""
        flash = MagicMock()
        monkeypatch.setattr(tmux_session, "flash_exit_hint", flash)
        await TerokTUI.action_quit(quit_stub)
        flash.assert_called_once()

    @pytest.mark.asyncio
    async def test_restart_exits_with_sentinel_and_no_hint(
        self, quit_stub: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``restart=True`` skips the hint and exits with the re-exec sentinel."""
        flash = MagicMock()
        monkeypatch.setattr(tmux_session, "flash_exit_hint", flash)
        await TerokTUI.action_quit(quit_stub, restart=True)
        flash.assert_not_called()
        quit_stub.exit.assert_called_once_with(result=_RESTART_EXIT_RESULT)


class TestConfirmQuit:
    """The main-screen ``q`` asks for a second ``q`` before tearing down the TUI."""

    def test_confirm_quit_opens_the_guard_modal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``q`` pushes a QuitConfirmScreen rather than quitting outright."""
        monkeypatch.setattr(tmux_session, "quit_lands_in_other_window", lambda: 0)
        stub = SimpleNamespace(push_screen=MagicMock(), _on_quit_confirmed=object())
        TerokTUI.action_confirm_quit(stub)
        screen, callback = stub.push_screen.call_args[0]
        assert isinstance(screen, QuitConfirmScreen)
        assert callback is stub._on_quit_confirmed

    def test_confirm_quit_uses_tmux_guard_when_landing_in_other_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Quitting into a sibling tmux window swaps in the tmux-aware guard."""
        monkeypatch.setattr(tmux_session, "quit_lands_in_other_window", lambda: 2)
        stub = SimpleNamespace(
            push_screen=MagicMock(),
            _on_quit_confirmed=object(),
            _on_tmux_quit_choice=object(),
        )
        TerokTUI.action_confirm_quit(stub)
        screen, callback = stub.push_screen.call_args[0]
        assert isinstance(screen, TmuxQuitScreen)
        assert callback is stub._on_tmux_quit_choice

    @pytest.mark.asyncio
    async def test_second_q_quits(self) -> None:
        """A confirmed guard (``True``) runs the real teardown."""
        stub = SimpleNamespace(action_quit=AsyncMock())
        await TerokTUI._on_quit_confirmed(stub, True)
        stub.action_quit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_any_other_key_does_not_quit(self) -> None:
        """A dismissed guard (``False``/``None``) leaves the TUI running."""
        stub = SimpleNamespace(action_quit=AsyncMock())
        await TerokTUI._on_quit_confirmed(stub, False)
        await TerokTUI._on_quit_confirmed(stub, None)
        stub.action_quit.assert_not_awaited()


class TestQuitConfirmScreen:
    """The guard modal quits only on a second ``q``."""

    def test_q_dismisses_true(self) -> None:
        """Pressing ``q`` confirms the quit and stops the event."""
        screen = QuitConfirmScreen.__new__(QuitConfirmScreen)
        screen.dismiss = MagicMock()
        event = SimpleNamespace(key="q", stop=MagicMock())
        QuitConfirmScreen.on_key(screen, event)
        event.stop.assert_called_once()
        screen.dismiss.assert_called_once_with(True)

    def test_other_key_dismisses_false(self) -> None:
        """Any other key returns to terok without quitting."""
        screen = QuitConfirmScreen.__new__(QuitConfirmScreen)
        screen.dismiss = MagicMock()
        QuitConfirmScreen.on_key(screen, SimpleNamespace(key="x", stop=MagicMock()))
        screen.dismiss.assert_called_once_with(False)


class TestTmuxQuitScreen:
    """The tmux-aware guard maps q→detach, n→next window, anything else→cancel."""

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            pytest.param("q", "detach", id="qq-detaches"),
            pytest.param("n", "next", id="qn-hops-windows"),
            pytest.param("escape", None, id="escape-cancels"),
            pytest.param("x", None, id="other-key-cancels"),
        ],
    )
    def test_key_mapping(self, key: str, expected: str | None) -> None:
        """Each quit flavour dismisses with its choice; the event never propagates."""
        screen = TmuxQuitScreen.__new__(TmuxQuitScreen)
        screen.dismiss = MagicMock()
        event = SimpleNamespace(key=key, stop=MagicMock())
        TmuxQuitScreen.on_key(screen, event)
        event.stop.assert_called_once()
        screen.dismiss.assert_called_once_with(expected)


class TestTmuxQuitChoice:
    """``_on_tmux_quit_choice`` wires each modal answer to the right teardown."""

    @pytest.mark.asyncio
    async def test_detach_detaches_then_quits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``detach`` returns the user to their terminal before the TUI exits."""
        detach = MagicMock()
        monkeypatch.setattr(tmux_session, "detach_client", detach)
        stub = SimpleNamespace(action_quit=AsyncMock())
        await TerokTUI._on_tmux_quit_choice(stub, "detach")
        detach.assert_called_once()
        stub.action_quit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_next_quits_without_detaching(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``next`` quits in place; the client falls through to the next window."""
        detach = MagicMock()
        monkeypatch.setattr(tmux_session, "detach_client", detach)
        stub = SimpleNamespace(action_quit=AsyncMock())
        await TerokTUI._on_tmux_quit_choice(stub, "next")
        detach.assert_not_called()
        stub.action_quit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_keeps_the_tui_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``None`` (any other key) neither detaches nor quits."""
        detach = MagicMock()
        monkeypatch.setattr(tmux_session, "detach_client", detach)
        stub = SimpleNamespace(action_quit=AsyncMock())
        await TerokTUI._on_tmux_quit_choice(stub, None)
        detach.assert_not_called()
        stub.action_quit.assert_not_awaited()
