# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The timed allow-all window in the TUI: the modal's answers and what each one does.

Driven unbound on a ``SimpleNamespace`` app double, the way
``test_shield_posture_actions`` drives the posture actions — no Textual app boots.
"""

from __future__ import annotations

from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import pytest

from terok.tui import task_actions as task_actions_mod
from terok.tui.task_actions import TaskActionsMixin
from tests.testfs import MOCK_BASE

_CONTAINER = "terok-proj-cli-task1"
_TASK_DIR = Path(MOCK_BASE) / "tasks" / "task1"


def _app_stub() -> SimpleNamespace:
    """App double recording ``_action_shield_toggle`` instead of running a worker."""
    stub = SimpleNamespace(notify=MagicMock(), _action_shield_toggle=MagicMock())
    stub._on_shield_bypass_result = MethodType(TaskActionsMixin._on_shield_bypass_result, stub)
    return stub


def test_a_duration_opens_the_window_for_it() -> None:
    """The modal's duration reaches ``ShieldManager.bypass`` through the toggle worker."""
    stub = _app_stub()

    stub._on_shield_bypass_result("30s")

    stub._action_shield_toggle.assert_called_once()
    action, shield_fn = stub._action_shield_toggle.call_args.args
    assert action == "bypass"
    with mock.patch.object(task_actions_mod, "ShieldManager") as manager:
        shield_fn(_CONTAINER, _TASK_DIR)
    manager.return_value.bypass.assert_called_once_with(_CONTAINER, "30s")


def test_an_empty_duration_closes_the_window() -> None:
    """Clearing the field is how the modal says "close it now"."""
    stub = _app_stub()

    stub._on_shield_bypass_result("")

    action, shield_fn = stub._action_shield_toggle.call_args.args
    assert action == "bypass-off"
    with mock.patch.object(task_actions_mod, "ShieldManager") as manager:
        shield_fn(_CONTAINER, _TASK_DIR)
    manager.return_value.bypass_off.assert_called_once_with(_CONTAINER)


def test_cancelling_touches_nothing() -> None:
    """Escape must leave the shield exactly as it was."""
    stub = _app_stub()

    stub._on_shield_bypass_result(None)

    stub._action_shield_toggle.assert_not_called()


@pytest.mark.parametrize(
    ("submitted", "expected"),
    [
        pytest.param("5m", "5m", id="duration"),
        pytest.param("  2h  ", "2h", id="stripped"),
        pytest.param("", "", id="empty-closes"),
    ],
)
def test_modal_returns_the_submitted_duration(submitted: str, expected: str) -> None:
    """``ShieldBypassScreen`` dismisses with the duration, or ``""`` to close."""
    from terok.tui.shield_bypass_screen import ShieldBypassScreen

    screen = ShieldBypassScreen("5m")
    with mock.patch.object(ShieldBypassScreen, "dismiss") as dismiss:
        screen.on_input_submitted(SimpleNamespace(value=submitted))  # type: ignore[arg-type]
    dismiss.assert_called_once_with(expected)


def test_modal_cancel_dismisses_with_none() -> None:
    """Cancel is distinguishable from "close the window": ``None`` versus ``""``."""
    from terok.tui.shield_bypass_screen import ShieldBypassScreen

    screen = ShieldBypassScreen("5m")
    with mock.patch.object(ShieldBypassScreen, "dismiss") as dismiss:
        screen.action_cancel()
    dismiss.assert_called_once_with(None)


def test_bypass_action_refuses_while_the_kill_switch_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the firewall disabled there is no window to open, and the TUI says so."""
    cfg = SimpleNamespace(
        shield_disable_firewall_no_protection=True,
        shield_security_hint="see the docs",
        shield_bypass_duration="5m",
    )
    monkeypatch.setattr(task_actions_mod, "get_config", lambda: cfg)
    stub = SimpleNamespace(
        notify=MagicMock(),
        push_screen=AsyncMock(),
        current_project_name="proj",
        current_task=SimpleNamespace(task_id="task1"),
    )
    stub._notify_shield_disabled = MethodType(TaskActionsMixin._notify_shield_disabled, stub)

    import asyncio

    asyncio.run(TaskActionsMixin._action_shield_bypass(stub))

    stub.push_screen.assert_not_called()
    stub.notify.assert_called_once()


def _live_app_stub(monkeypatch: pytest.MonkeyPatch, *, task: object | None) -> SimpleNamespace:
    """App double with the kill-switch off and *task* selected (or nothing selected)."""
    cfg = SimpleNamespace(
        shield_disable_firewall_no_protection=False,
        shield_security_hint="see the docs",
        shield_bypass_duration="45s",
    )
    monkeypatch.setattr(task_actions_mod, "get_config", lambda: cfg)
    stub = SimpleNamespace(
        notify=MagicMock(),
        push_screen=AsyncMock(),
        current_project_name="proj" if task is not None else None,
        current_task=task,
    )
    stub._notify_shield_disabled = MethodType(TaskActionsMixin._notify_shield_disabled, stub)
    stub._on_shield_bypass_result = MethodType(TaskActionsMixin._on_shield_bypass_result, stub)
    return stub


def test_bypass_action_asks_with_the_configured_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The modal opens prefilled from ``shield.bypass_duration``, wired to the handler."""
    import asyncio

    from terok.tui.shield_bypass_screen import ShieldBypassScreen

    stub = _live_app_stub(monkeypatch, task=SimpleNamespace(task_id="task1"))

    asyncio.run(TaskActionsMixin._action_shield_bypass(stub))

    screen, callback = stub.push_screen.call_args.args
    assert isinstance(screen, ShieldBypassScreen)
    assert screen._duration == "45s"
    assert callback == stub._on_shield_bypass_result


def test_bypass_action_needs_a_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """With nothing selected the action says so instead of opening a modal."""
    import asyncio

    stub = _live_app_stub(monkeypatch, task=None)

    asyncio.run(TaskActionsMixin._action_shield_bypass(stub))

    stub.push_screen.assert_not_called()
    stub.notify.assert_called_once_with("No task selected.")


@pytest.mark.asyncio
async def test_modal_shows_the_consequence_above_the_prefilled_duration() -> None:
    """Opening an allow-all is stated, not implied — and the field starts at the default."""
    from textual.app import App
    from textual.widgets import Input, Static

    from terok.tui.shield_bypass_screen import _CONSEQUENCE, ShieldBypassScreen

    class _Host(App):
        """Minimal host that pushes the screen so its widgets can mount."""

        def on_mount(self) -> None:
            self.push_screen(ShieldBypassScreen("2h"))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ShieldBypassScreen)
        screen.query_one("#bypass-consequence", Static)  # the consequence is on screen
        box = screen.query_one("#bypass-box", Input)

    assert "accepted and logged" in _CONSEQUENCE
    assert box.value == "2h"
    assert "close it now" in str(box.border_subtitle)
