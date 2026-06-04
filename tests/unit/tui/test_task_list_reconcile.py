# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The container-state poll reconciles the task set with what's on disk.

The 2-second batch query already re-reads every task from disk, so its result
keys reveal tasks created or deleted *outside* the TUI.  The handler diffs that
membership against the displayed rows (level-triggered, so it cannot miss a
change) and reloads the list only when it actually differs — otherwise it just
maps fresh container states onto the existing rows, as before.
"""

from __future__ import annotations

import asyncio
import types
from typing import Any
from unittest import mock

from tests.unit.tui.tui_test_helpers import import_app


def run(coro: object) -> object:
    """Run an async test coroutine."""
    return asyncio.run(coro)


def _task(task_id: str, state: str | None = None) -> types.SimpleNamespace:
    """Minimal displayed-row stand-in carrying an id and a container state."""
    return types.SimpleNamespace(task_id=task_id, container_state=state)


def _instance(app_class: type, displayed: list[Any]) -> Any:
    """App wired with a fake task list and an async ``refresh_tasks`` spy."""
    instance = app_class()
    instance.current_project_id = "p1"
    instance.current_task = None
    instance.refresh_tasks = mock.AsyncMock()
    task_list = mock.Mock()
    task_list.tasks = displayed
    instance.query_one = mock.Mock(return_value=task_list)
    instance._task_list = task_list  # handle for assertions
    return instance


def _poll(instance: Any, app_mod: Any, states: dict[str, str | None], pid: str = "p1") -> None:
    """Drive a completed container-state poll through the worker handler."""
    worker = mock.Mock()
    worker.group = "container-state"
    worker.result = (pid, states)
    event = mock.Mock()
    event.worker = worker
    event.state = app_mod.WorkerState.SUCCESS
    run(instance.handle_worker_state_changed(event))


class TestMembershipReconcile:
    """Membership drift between disk and the displayed rows forces a reload."""

    def test_external_create_triggers_refresh(self) -> None:
        app_mod, app_class = import_app()
        instance = _instance(app_class, [_task("1")])
        _poll(instance, app_mod, {"1": None, "2": None})  # task 2 appeared on disk
        instance.refresh_tasks.assert_awaited_once()

    def test_external_delete_triggers_refresh(self) -> None:
        app_mod, app_class = import_app()
        instance = _instance(app_class, [_task("1"), _task("2")])
        _poll(instance, app_mod, {"1": None})  # task 2 vanished from disk
        instance.refresh_tasks.assert_awaited_once()


class TestStateOnlyUpdate:
    """Stable membership keeps the in-place state update — no reload."""

    def test_state_change_updates_in_place(self) -> None:
        app_mod, app_class = import_app()
        instance = _instance(app_class, [_task("1", None)])
        _poll(instance, app_mod, {"1": "running"})
        instance.refresh_tasks.assert_not_awaited()
        assert instance._task_list.tasks[0].container_state == "running"
        instance._task_list.refresh_labels.assert_called_once()

    def test_no_change_is_a_noop(self) -> None:
        app_mod, app_class = import_app()
        instance = _instance(app_class, [_task("1", None)])
        _poll(instance, app_mod, {"1": None})
        instance.refresh_tasks.assert_not_awaited()
        instance._task_list.refresh_labels.assert_not_called()


class TestProjectGuard:
    """A result for a project the user already switched away from is dropped."""

    def test_stale_project_result_ignored(self) -> None:
        app_mod, app_class = import_app()
        instance = _instance(app_class, [_task("1")])
        _poll(instance, app_mod, {"1": None, "2": None}, pid="other")
        instance.refresh_tasks.assert_not_awaited()
        instance.query_one.assert_not_called()
