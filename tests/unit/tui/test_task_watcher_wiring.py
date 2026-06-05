# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The polling mixin debounces inotify activity into a single reconcile.

Covers the reaction logic on the app side — draining, debounce coalescing, and
the empty-drain no-op — without a real event loop.  The watcher mechanism itself
is covered against real inotify in ``test_task_watcher``.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

from tests.unit.tui.tui_test_helpers import import_app


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
