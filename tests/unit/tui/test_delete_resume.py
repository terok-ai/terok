# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Recovery from interrupted task deletes.

A task is flagged ``deleting`` on disk the instant a delete starts, before the
background teardown runs.  A crash in between strands the task ``deleting``
forever.  These tests cover the two halves of the fix: the startup sweep that
re-queues such orphans, and the delete guard keying on a live in-flight set
(so a stale flag stays retriable while an active worker is not double-queued).
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


def _task(task_id: str, *, name: str = "", deleting: bool = False) -> types.SimpleNamespace:
    """Build a minimal TaskMeta stand-in for the sweep/guard logic."""
    return types.SimpleNamespace(task_id=task_id, name=name, deleting=deleting)


def _instance(app_class: type) -> Any:
    """Fresh TerokTUI instance with the noisy helpers stubbed out.

    Built from a class the caller already imported, so the instance and any
    module the caller patches share one ``import_app`` generation — the helper
    re-imports fresh on every call, so a second import would desync them.
    """
    instance = app_class()
    instance.notify = mock.Mock()
    instance._queue_task_delete = mock.Mock()
    return instance


class TestResumeInterruptedDeletes:
    """The startup sweep re-queues exactly the stranded deletes."""

    def test_requeues_only_deleting_tasks_across_projects(self) -> None:
        app_mod, app_class = import_app()
        instance = _instance(app_class)
        instance._projects_by_id = {"p1": object(), "p2": object()}
        tasks = {
            "p1": [_task("1", name="a", deleting=True), _task("2", name="b")],
            "p2": [_task("3", name="c", deleting=True)],
        }
        with mock.patch.object(app_mod, "get_tasks", lambda pid: tasks[pid]):
            instance._resume_interrupted_deletes()

        assert instance._queue_task_delete.call_args_list == [
            mock.call("p1", "1", "a"),
            mock.call("p2", "3", "c"),
        ]
        # One summary toast naming the count of resumed deletions.
        instance.notify.assert_called_once()
        assert "2" in instance.notify.call_args[0][0]

    def test_skips_tasks_already_in_flight(self) -> None:
        app_mod, app_class = import_app()
        instance = _instance(app_class)
        instance._projects_by_id = {"p1": object()}
        instance._deleting_tasks.add(("p1", "1"))
        tasks = {"p1": [_task("1", deleting=True), _task("2", name="b", deleting=True)]}
        with mock.patch.object(app_mod, "get_tasks", lambda pid: tasks[pid]):
            instance._resume_interrupted_deletes()

        instance._queue_task_delete.assert_called_once_with("p1", "2", "b")

    def test_no_orphans_is_silent(self) -> None:
        app_mod, app_class = import_app()
        instance = _instance(app_class)
        instance._projects_by_id = {"p1": object()}
        with mock.patch.object(app_mod, "get_tasks", lambda pid: [_task("1"), _task("2")]):
            instance._resume_interrupted_deletes()

        instance._queue_task_delete.assert_not_called()
        instance.notify.assert_not_called()


class TestDeleteGuard:
    """The guard keys on the live in-flight set, not the on-disk flag."""

    def _ready_instance(self, app_class: type) -> Any:
        """Instance wired for a single ``action_delete_task`` call."""
        instance = _instance(app_class)
        instance.current_project_id = "p1"
        instance._log_debug = mock.Mock()
        instance._update_task_details = mock.Mock()
        instance.query_one = mock.Mock(return_value=mock.Mock())
        return instance

    def test_blocks_a_delete_already_in_flight(self) -> None:
        _, app_class = import_app()
        instance = self._ready_instance(app_class)
        instance.current_task = _task("5", name="x")
        instance._deleting_tasks.add(("p1", "5"))

        globals_ = app_class.action_delete_task.__globals__
        with mock.patch.dict(globals_, {"mark_task_deleting": mock.Mock()}):
            run(instance.action_delete_task())
            globals_["mark_task_deleting"].assert_not_called()

        instance._queue_task_delete.assert_not_called()
        assert "already being deleted" in instance.notify.call_args[0][0]

    def test_stale_flag_stays_retriable(self) -> None:
        _, app_class = import_app()
        instance = self._ready_instance(app_class)
        # ``deleting`` set on disk/in-memory by a prior crashed session, but the
        # task is *not* in the live set — the delete must proceed, not refuse.
        instance.current_task = _task("5", name="x", deleting=True)

        globals_ = app_class.action_delete_task.__globals__
        with mock.patch.dict(globals_, {"mark_task_deleting": mock.Mock()}):
            run(instance.action_delete_task())
            globals_["mark_task_deleting"].assert_called_once_with("p1", "5")

        instance._queue_task_delete.assert_called_once_with("p1", "5", "x")


class TestInFlightBookkeeping:
    """Queue registers the in-flight key; the worker handler clears it."""

    def test_queue_registers_in_flight(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.run_worker = mock.Mock()
        instance._queue_task_delete("p1", "7", "name")

        assert ("p1", "7") in instance._deleting_tasks
        instance.run_worker.assert_called_once()

    def test_worker_completion_clears_in_flight(self) -> None:
        app_mod, app_class = import_app()
        instance = app_class()
        instance.notify = mock.Mock()
        # A different current project so the handler returns before refresh_tasks.
        instance.current_project_id = "other"
        instance._deleting_tasks.add(("p1", "7"))

        worker = mock.Mock()
        worker.group = "task-delete"
        worker.result = ("p1", "7", "name", None, [])
        event = mock.Mock()
        event.worker = worker
        event.state = app_mod.WorkerState.SUCCESS

        run(instance.handle_worker_state_changed(event))

        assert ("p1", "7") not in instance._deleting_tasks
