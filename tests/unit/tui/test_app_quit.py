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

from terok.tui.app import TerokTUI
from terok.tui.screens import QuitConfirmScreen


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


class TestConfirmQuit:
    """The main-screen ``q`` asks for a second ``q`` before tearing down the TUI."""

    def test_confirm_quit_opens_the_guard_modal(self) -> None:
        """``q`` pushes a QuitConfirmScreen rather than quitting outright."""
        stub = SimpleNamespace(push_screen=MagicMock(), _on_quit_confirmed=object())
        TerokTUI.action_confirm_quit(stub)
        screen, callback = stub.push_screen.call_args[0]
        assert isinstance(screen, QuitConfirmScreen)
        assert callback is stub._on_quit_confirmed

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
