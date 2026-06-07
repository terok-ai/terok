# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The polling mixin debounces inotify activity into a single reconcile.

Covers the reaction logic on the app side — draining, debounce coalescing, and
the empty-drain no-op — without a real event loop.  The watcher mechanism itself
is covered against real inotify in ``test_task_watcher``.
"""

from __future__ import annotations

import types
from typing import Any
from unittest import mock

from tests.unit.tui.tui_test_helpers import import_app


def types_ns(**kw: Any) -> types.SimpleNamespace:
    """A lightweight stand-in for a TaskMeta row (carries ``task_id``)."""
    return types.SimpleNamespace(**kw)


def _instance(app_class: type, *, drains: bool) -> Any:
    """App wired with a fake watcher and spies for the debounce timer."""
    instance = app_class()
    instance._task_watcher = mock.Mock()
    instance._task_watcher.drain.return_value = drains
    instance._watch_debounce = None
    instance._poll_container_status = mock.Mock()
    instance.set_timer = mock.Mock(return_value=mock.Mock())
    return instance


def test_change_schedules_a_debounced_reconcile() -> None:
    _app_mod, app_class = import_app()
    instance = _instance(app_class, drains=True)
    instance._on_task_dir_changed()
    instance.set_timer.assert_called_once()
    # The debounce fires the same reconcile the timer would, not an eager one.
    assert instance.set_timer.call_args.args[1] is instance._poll_container_status
    instance._poll_container_status.assert_not_called()


def test_empty_drain_is_a_noop() -> None:
    _app_mod, app_class = import_app()
    instance = _instance(app_class, drains=False)
    instance._on_task_dir_changed()
    instance.set_timer.assert_not_called()


def test_burst_collapses_restarting_the_window() -> None:
    _app_mod, app_class = import_app()
    instance = _instance(app_class, drains=True)
    instance._on_task_dir_changed()
    first = instance._watch_debounce
    instance._on_task_dir_changed()
    # The pending window is cancelled and replaced rather than stacking timers.
    first.stop.assert_called_once()
    assert instance.set_timer.call_count == 2


def test_stop_cancels_pending_debounce() -> None:
    _app_mod, app_class = import_app()
    instance = _instance(app_class, drains=True)
    instance._on_task_dir_changed()
    pending = instance._watch_debounce
    instance._stop_task_watcher()
    pending.stop.assert_called_once()
    assert instance._watch_debounce is None
    assert instance._task_watcher is None


def _event_instance(app_class: type) -> Any:
    """App wired with debounce spies for the podman-event reaction."""
    instance = app_class()
    instance.current_project_id = "p1"
    instance._watch_debounce = None
    instance._poll_container_status = mock.Mock()
    instance.set_timer = mock.Mock(return_value=mock.Mock())
    return instance


def test_container_event_for_current_project_debounces() -> None:
    _app_mod, app_class = import_app()
    instance = _event_instance(app_class)
    instance._on_container_event("p1")
    instance.set_timer.assert_called_once()
    assert instance.set_timer.call_args.args[1] is instance._poll_container_status


def test_container_event_for_other_project_ignored() -> None:
    _app_mod, app_class = import_app()
    instance = _event_instance(app_class)
    instance._on_container_event("p2")  # user already switched away
    instance.set_timer.assert_not_called()


def test_inotify_and_event_share_one_debounce_window() -> None:
    """An inotify hit and a podman event in a burst collapse to one reconcile."""
    _app_mod, app_class = import_app()
    instance = _event_instance(app_class)
    instance._task_watcher = mock.Mock()
    instance._task_watcher.drain.return_value = True
    instance._on_task_dir_changed()
    first = instance._watch_debounce
    instance._on_container_event("p1")
    first.stop.assert_called_once()  # window restarted, not stacked
    assert instance.set_timer.call_count == 2


def test_drain_events_reconciles_then_stops_when_stream_closes() -> None:
    """The worker reconciles per event and exits when iteration ends."""
    _app_mod, app_class = import_app()
    instance = app_class()
    instance.call_from_thread = mock.Mock()
    instance._on_container_event = mock.Mock()
    stream = iter([object(), object()])  # two events, then StopIteration
    instance._drain_container_events(stream, "p1")
    assert instance.call_from_thread.call_count == 2
    instance.call_from_thread.assert_called_with(instance._on_container_event, "p1")


def test_stop_event_stream_closes_and_clears() -> None:
    _app_mod, app_class = import_app()
    instance = app_class()
    stream = mock.Mock()
    instance._container_event_stream = stream
    instance._stop_container_event_stream()
    stream.close.assert_called_once()
    assert instance._container_event_stream is None


class TestLifecycleWiring:
    """Start/stop orchestration: sources armed, resync honoured, paths computed."""

    def _app(self, app_class: type) -> Any:
        instance = app_class()
        instance.current_project_id = "p1"
        instance._container_status_timer = None
        instance._task_watcher = None
        instance._container_event_stream = None
        instance._watch_debounce = None
        instance._stop_container_status_polling = mock.Mock()
        instance._poll_container_status = mock.Mock()
        instance._start_task_watcher = mock.Mock()
        instance._start_container_event_worker = mock.Mock()
        instance.set_interval = mock.Mock(return_value=mock.Mock())
        return instance

    def test_resync_enabled_schedules_the_timer(self) -> None:
        _app_mod, app_class = import_app()
        instance = self._app(app_class)
        with mock.patch("terok.lib.core.config.get_tui_container_resync_seconds", return_value=60):
            instance._start_container_status_polling()
        instance._poll_container_status.assert_called_once()  # seed
        instance._start_task_watcher.assert_called_once_with("p1")
        instance._start_container_event_worker.assert_called_once_with("p1")
        instance.set_interval.assert_called_once()
        assert instance.set_interval.call_args.args[0] == 60

    def test_resync_zero_runs_purely_event_driven(self) -> None:
        _app_mod, app_class = import_app()
        instance = self._app(app_class)
        with mock.patch("terok.lib.core.config.get_tui_container_resync_seconds", return_value=0):
            instance._start_container_status_polling()
        instance.set_interval.assert_not_called()  # no timer at all

    def test_seed_runs_after_both_push_sources_are_armed(self) -> None:
        """No startup blind spot: arm inotify + the event stream, then seed."""
        _app_mod, app_class = import_app()
        instance = self._app(app_class)
        manager = mock.Mock()
        instance._start_task_watcher = manager.watcher
        instance._start_container_event_worker = manager.events
        instance._poll_container_status = manager.seed
        with mock.patch("terok.lib.core.config.get_tui_container_resync_seconds", return_value=0):
            instance._start_container_status_polling()
        # A change between snapshot and first event would be lost if seed ran
        # first; both push sources must be live before the seed reads state.
        assert [call[0] for call in manager.mock_calls] == ["watcher", "events", "seed"]

    def test_event_worker_skipped_when_stream_unavailable(self) -> None:
        _app_mod, app_class = import_app()
        instance = app_class()
        instance._container_event_stream = None
        instance.run_worker = mock.Mock()
        with mock.patch("terok.lib.api.container_event_stream", return_value=None):
            instance._start_container_event_worker("p1")
        instance.run_worker.assert_not_called()
        assert instance._container_event_stream is None

    def test_event_worker_starts_thread_when_stream_available(self) -> None:
        _app_mod, app_class = import_app()
        instance = app_class()
        instance._container_event_stream = None
        instance.run_worker = mock.Mock()
        stream = mock.Mock()
        with mock.patch("terok.lib.api.container_event_stream", return_value=stream):
            instance._start_container_event_worker("p1")
        instance.run_worker.assert_called_once()
        assert instance.run_worker.call_args.kwargs.get("thread") is True
        assert instance._container_event_stream is stream

    def test_watch_paths_are_meta_dir_plus_each_agent_config(self) -> None:
        _app_mod, app_class = import_app()
        instance = app_class()
        tasks = [types_ns(task_id="1"), types_ns(task_id="2")]
        with (
            mock.patch("terok.lib.api.tasks_meta_dir", return_value="/meta"),
            mock.patch("terok.lib.api.get_tasks", return_value=tasks),
            mock.patch("terok.lib.api.agent_config_dir", side_effect=lambda p, t: f"/cfg/{t}"),
        ):
            paths = instance._task_watch_paths("p1")
        assert paths == ["/meta", "/cfg/1", "/cfg/2"]

    def test_resync_task_watches_syncs_to_current_paths(self) -> None:
        _app_mod, app_class = import_app()
        instance = app_class()
        instance.current_project_id = "p1"
        instance._task_watcher = mock.Mock()
        instance._task_watch_paths = mock.Mock(return_value=["/meta", "/cfg/1"])
        instance._resync_task_watches()
        instance._task_watcher.sync.assert_called_once_with(["/meta", "/cfg/1"])

    def test_resync_task_watches_is_a_noop_without_a_watcher(self) -> None:
        _app_mod, app_class = import_app()
        instance = app_class()
        instance.current_project_id = "p1"
        instance._task_watcher = None
        instance._resync_task_watches()  # must not raise


class TestStartTaskWatcher:
    """`_start_task_watcher` arms inotify, or degrades to the resync on failure.

    Every failure path returns ``False`` (the caller then leans on the periodic
    resync) and leaves no half-armed state — an opened fd is always closed.
    """

    def _app(self, app_class: type) -> Any:
        instance = app_class()
        instance._task_watcher = None
        instance._task_watch_paths = mock.Mock(return_value=["/meta"])
        instance._log_debug = mock.Mock()
        return instance

    def test_success_registers_the_fd_and_keeps_the_watcher(self) -> None:
        _app_mod, app_class = import_app()
        instance = self._app(app_class)
        watcher = mock.Mock(fileno=7)
        watcher.start.return_value = True
        loop = mock.Mock()
        with (
            mock.patch("terok.tui.task_watcher.TaskWatcher", return_value=watcher),
            mock.patch("asyncio.get_running_loop", return_value=loop),
        ):
            assert instance._start_task_watcher("p1") is True
        loop.add_reader.assert_called_once_with(7, instance._on_task_dir_changed)
        assert instance._task_watcher is watcher

    def test_unstartable_watcher_never_attaches(self) -> None:
        """No inotify (start() False) → no add_reader, no retained watcher."""
        _app_mod, app_class = import_app()
        instance = self._app(app_class)
        watcher = mock.Mock()
        watcher.start.return_value = False
        loop = mock.Mock()
        with (
            mock.patch("terok.tui.task_watcher.TaskWatcher", return_value=watcher),
            mock.patch("asyncio.get_running_loop", return_value=loop),
        ):
            assert instance._start_task_watcher("p1") is False
        loop.add_reader.assert_not_called()
        assert instance._task_watcher is None

    def test_attach_failure_closes_the_fd_and_degrades(self) -> None:
        """A loop without add_reader support (NotImplementedError) is survived."""
        _app_mod, app_class = import_app()
        instance = self._app(app_class)
        watcher = mock.Mock(fileno=7)
        watcher.start.return_value = True
        loop = mock.Mock()
        loop.add_reader.side_effect = NotImplementedError("loop has no reader support")
        with (
            mock.patch("terok.tui.task_watcher.TaskWatcher", return_value=watcher),
            mock.patch("asyncio.get_running_loop", return_value=loop),
        ):
            assert instance._start_task_watcher("p1") is False
        watcher.stop.assert_called_once()  # the opened fd is closed, not leaked
        assert instance._task_watcher is None

    def test_watcher_init_failure_degrades(self) -> None:
        """Even constructing the watcher failing is non-fatal (resync covers it)."""
        _app_mod, app_class = import_app()
        instance = self._app(app_class)
        with mock.patch("terok.tui.task_watcher.TaskWatcher", side_effect=OSError("no libc")):
            assert instance._start_task_watcher("p1") is False
        assert instance._task_watcher is None
