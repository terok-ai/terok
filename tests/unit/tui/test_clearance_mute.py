# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Muting a task's clearance prompts, from the clearance screen and the task menu.

Mute silences the question, never the shield: these tests pin that terok asks
the hub for an explicit state and reports honestly when it could not.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import pytest

from terok.tui import task_actions as task_actions_mod
from terok.tui.clearance_screen import ClearanceScreen, _PendingRequest
from terok.tui.task_actions import TaskActionsMixin
from tests.testfs import MOCK_BASE

_CONTAINER_UUID = "aabbccddeeff00112233445566778899"
_SHORT_ID = _CONTAINER_UUID[:12]


# ── the clearance screen's one-click mute ────────────────────────────────


@pytest.fixture
def app_double() -> Iterator[SimpleNamespace]:
    """Stand in for ``Screen.app``, which is a read-only property on the class."""
    double = SimpleNamespace(notify=MagicMock())
    with mock.patch.object(ClearanceScreen, "app", double):
        yield double


def _screen_stub(request: _PendingRequest | None, subscriber: object) -> ClearanceScreen:
    """A screen with one highlighted request and a stub subscriber."""
    screen = ClearanceScreen.__new__(ClearanceScreen)
    screen._subscriber = subscriber
    screen._pending = {request.nid: request} if request else {}
    screen._highlighted_request = MethodType(lambda _self: request, screen)  # type: ignore[assignment]
    screen.run_worker = MagicMock()  # type: ignore[method-assign]
    return screen


def test_mute_asks_the_hub_for_the_highlighted_task(app_double: SimpleNamespace) -> None:
    """``m`` routes to the container the highlighted request came from."""
    request = _PendingRequest(
        nid=1, summary="s", body="b", container_id=_SHORT_ID, container_name="terok-proj-cli-1"
    )
    screen = _screen_stub(request, subscriber=MagicMock())

    screen.action_mute_selected()

    screen.run_worker.assert_called_once()
    assert screen.run_worker.call_args.kwargs["group"] == "clearance-mute"


def test_mute_without_a_subscriber_says_so(app_double: SimpleNamespace) -> None:
    """No hub connection means no mute, and the operator is told rather than ignored."""
    request = _PendingRequest(nid=1, summary="s", body="b", container_id=_SHORT_ID)
    screen = _screen_stub(request, subscriber=None)

    screen.action_mute_selected()

    screen.run_worker.assert_not_called()
    app_double.notify.assert_called_once()


def test_mute_without_a_selection_does_nothing(app_double: SimpleNamespace) -> None:
    """Nothing highlighted, nothing muted."""
    screen = _screen_stub(None, subscriber=MagicMock())

    screen.action_mute_selected()

    screen.run_worker.assert_not_called()


@pytest.mark.asyncio
async def test_mute_reports_what_the_hub_answered(app_double: SimpleNamespace) -> None:
    """The notice reflects the hub's answer, not the request."""
    screen = _screen_stub(None, subscriber=SimpleNamespace(set_mute=AsyncMock(return_value=False)))

    await ClearanceScreen._mute(screen, _SHORT_ID, "terok-proj-cli-1")

    message = app_double.notify.call_args.args[0]
    assert "Could not mute" in message


@pytest.mark.asyncio
async def test_mute_notice_states_that_refusals_continue(app_double: SimpleNamespace) -> None:
    """A muted task is silent, not unprotected — the notice has to say so."""
    screen = _screen_stub(None, subscriber=SimpleNamespace(set_mute=AsyncMock(return_value=True)))

    await ClearanceScreen._mute(screen, _SHORT_ID, "terok-proj-cli-1")

    message = app_double.notify.call_args.args[0]
    assert "still enforced and logged" in message


# ── the task action list ─────────────────────────────────────────────────


def test_clearance_socket_uses_the_short_container_id() -> None:
    """``sun_path`` is 108 bytes, so the supervisor names the directory with 12 chars."""
    cfg = SimpleNamespace(runtime_dir=Path(MOCK_BASE) / "run")
    with mock.patch.object(task_actions_mod, "get_config", lambda: cfg):
        socket = task_actions_mod._clearance_socket(_CONTAINER_UUID)

    assert socket == Path(MOCK_BASE) / "run" / "clearance" / _SHORT_ID / "hub.sock"


@pytest.mark.parametrize(
    ("action", "muted"),
    [
        pytest.param("_action_clearance_mute", True, id="mute"),
        pytest.param("_action_clearance_unmute", False, id="unmute"),
    ],
)
def test_menu_actions_send_an_explicit_state(action: str, muted: bool) -> None:
    """Mute and unmute are separate verbs — a toggle would drift from the hub's memory."""
    stub = SimpleNamespace(notify=MagicMock(), run_worker=MagicMock())
    stub._set_clearance_mute = MethodType(TaskActionsMixin._set_clearance_mute, stub)
    stub.current_project_name = "proj"
    stub.current_task = SimpleNamespace(task_id="task1", mode="cli")

    getattr(TaskActionsMixin, action)(stub)

    stub.run_worker.assert_called_once()
    work = stub.run_worker.call_args.args[0]
    with (
        mock.patch("terok.lib.api.clearance.set_container_mute", new=AsyncMock()) as set_mute,
        mock.patch(
            "terok.lib.orchestration.task_runners.resolve_container_uuid",
            return_value=_CONTAINER_UUID,
        ),
        mock.patch.object(
            task_actions_mod, "get_config", lambda: SimpleNamespace(runtime_dir=Path(MOCK_BASE))
        ),
    ):
        work()
    assert set_mute.call_args.args[2] is muted


def test_menu_action_needs_a_task() -> None:
    """With nothing selected the action says so instead of guessing."""
    stub = SimpleNamespace(notify=MagicMock(), run_worker=MagicMock())
    stub._set_clearance_mute = MethodType(TaskActionsMixin._set_clearance_mute, stub)
    stub.current_project_name = None
    stub.current_task = None

    TaskActionsMixin._action_clearance_mute(stub)

    stub.run_worker.assert_not_called()
    stub.notify.assert_called_once_with("No task selected.")
