# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the TUI review-lag polling handlers.

The compute path lives in [`terok.lib.domain.review_lag`][terok.lib.domain.review_lag]
(covered by ``test_review_lag``); here we exercise the TUI wiring — the
push-marker trigger's project guard and the state/notify handling — by
calling the unbound ``PollingMixin`` methods on a mock ``self``.
"""

from __future__ import annotations

from types import ModuleType, SimpleNamespace
from unittest import mock

import pytest
import textual.theme  # noqa: F401 — prime the real module before stubbing (see import_fresh)

from tests.unit.tui.tui_test_helpers import build_textual_stubs, import_app, import_fresh


def _app() -> ModuleType:
    """Fresh-import the app module against Textual stubs."""
    _, _, app_mod = import_fresh(build_textual_stubs())
    return app_mod


class TestPollReviewLagGuard:
    """The marker-triggered recheck only runs for the current project."""

    def test_fires_for_the_current_project(self) -> None:
        """A marker fire for the selected project dispatches a recheck worker."""
        app = _app()
        me = mock.Mock(_polling_project_name="proj1", current_project_name="proj1")
        app.TerokTUI._poll_review_lag(me)
        me.run_worker.assert_called_once()
        assert me.run_worker.call_args.kwargs["group"] == "review_lag"

    def test_skips_when_project_changed(self) -> None:
        """A marker fire for a different project is ignored."""
        app = _app()
        me = mock.Mock(_polling_project_name="proj1", current_project_name="proj2")
        app.TerokTUI._poll_review_lag(me)
        me.run_worker.assert_not_called()

    def test_skips_when_polling_idle(self) -> None:
        """No dispatch when upstream polling is not running."""
        app = _app()
        me = mock.Mock(_polling_project_name=None, current_project_name="proj1")
        app.TerokTUI._poll_review_lag(me)
        me.run_worker.assert_not_called()


class TestOnReviewLagUpdated:
    """Level-triggered: new warnings toast; clearing is silent; stale is ignored."""

    def test_new_warnings_toast_and_refresh(self) -> None:
        """First-seen warnings toast at warning severity and refresh the panel."""
        app = _app()
        me = mock.Mock(current_project_name="proj1", _review_lag_lines=None)
        app.TerokTUI._on_review_lag_updated(me, "proj1", ["!42 feat/x +3"])
        assert me._review_lag_lines == ["!42 feat/x +3"]
        me.notify.assert_called_once()
        assert me.notify.call_args.kwargs.get("severity") == "warning"
        me._refresh_project_state.assert_called_once()

    def test_unchanged_warnings_do_not_re_toast(self) -> None:
        """Identical warnings neither re-toast nor re-refresh."""
        app = _app()
        me = mock.Mock(current_project_name="proj1", _review_lag_lines=["!42 feat/x +3"])
        app.TerokTUI._on_review_lag_updated(me, "proj1", ["!42 feat/x +3"])
        me.notify.assert_not_called()
        me._refresh_project_state.assert_not_called()

    def test_clearing_is_silent_but_refreshes(self) -> None:
        """Clearing the warnings is silent but still refreshes the panel."""
        app = _app()
        me = mock.Mock(current_project_name="proj1", _review_lag_lines=["!42 feat/x +3"])
        app.TerokTUI._on_review_lag_updated(me, "proj1", [])
        assert me._review_lag_lines == []
        me.notify.assert_not_called()
        me._refresh_project_state.assert_called_once()

    def test_stale_project_update_ignored(self) -> None:
        """An update for a no-longer-current project is dropped."""
        app = _app()
        me = mock.Mock(current_project_name="proj2", _review_lag_lines=None)
        app.TerokTUI._on_review_lag_updated(me, "proj1", ["!42 feat/x +3"])
        me.notify.assert_not_called()
        me._refresh_project_state.assert_not_called()


class TestRefreshReviewLag:
    """The shared review-lag worker surfaces entries and honours query failure."""

    @pytest.mark.asyncio
    async def test_surfaces_computed_entries(self) -> None:
        """Non-None entries are stringified and handed to the update handler."""
        app = _app()
        me = mock.Mock(current_project_name="proj1")
        with (
            mock.patch("terok.lib.api.load_project"),
            mock.patch("terok.lib.api.refresh_review_lag", return_value=["!42 feat/x +3"]),
        ):
            await app.TerokTUI._refresh_review_lag(me, "proj1")
        me._on_review_lag_updated.assert_called_once_with("proj1", ["!42 feat/x +3"])

    @pytest.mark.asyncio
    async def test_query_failure_is_not_surfaced(self) -> None:
        """A None result (forge unreachable) keeps prior state — no handler call."""
        app = _app()
        me = mock.Mock(current_project_name="proj1")
        with (
            mock.patch("terok.lib.api.load_project"),
            mock.patch("terok.lib.api.refresh_review_lag", return_value=None),
        ):
            await app.TerokTUI._refresh_review_lag(me, "proj1")
        me._on_review_lag_updated.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_error_is_swallowed(self) -> None:
        """A failed project load logs and returns without crashing the worker."""
        app = _app()
        me = mock.Mock(current_project_name="proj1")
        with mock.patch("terok.lib.api.load_project", side_effect=SystemExit("boom")):
            await app.TerokTUI._refresh_review_lag(me, "proj1")
        me._on_review_lag_updated.assert_not_called()


class TestGateMarkerWatch:
    """The gate-dir inotify watch arms, fires, and tears down."""

    def _instance(self, app_class: type) -> object:
        instance = app_class()
        instance._gate_marker_watcher = None
        instance._gate_watch_debounce = None
        instance._poll_review_lag = mock.Mock()
        instance.set_timer = mock.Mock(return_value=mock.Mock())
        instance._log_debug = mock.Mock()
        return instance

    def test_disabled_review_lag_skips_the_watch(self) -> None:
        _app_mod, app_class = import_app()
        instance = self._instance(app_class)
        project = SimpleNamespace(review_lag_enabled=False, gate_path="/gate")
        with mock.patch("terok.tui.task_watcher.TaskWatcher") as watcher_cls:
            instance._start_gate_marker_watch(project)
        watcher_cls.assert_not_called()
        assert instance._gate_marker_watcher is None

    def test_watch_start_failure_leaves_no_watcher(self) -> None:
        _app_mod, app_class = import_app()
        instance = self._instance(app_class)
        project = SimpleNamespace(review_lag_enabled=True, gate_path="/gate")
        watcher = mock.Mock()
        watcher.start.return_value = False
        with mock.patch("terok.tui.task_watcher.TaskWatcher", return_value=watcher):
            instance._start_gate_marker_watch(project)
        assert instance._gate_marker_watcher is None

    def test_successful_arm_records_the_watcher(self) -> None:
        _app_mod, app_class = import_app()
        instance = self._instance(app_class)
        project = SimpleNamespace(review_lag_enabled=True, gate_path="/gate")
        watcher = mock.Mock()
        watcher.start.return_value = True
        watcher.fileno = 7
        loop = mock.Mock()
        with (
            mock.patch("terok.tui.task_watcher.TaskWatcher", return_value=watcher),
            mock.patch("asyncio.get_running_loop", return_value=loop),
        ):
            instance._start_gate_marker_watch(project)
        loop.add_reader.assert_called_once_with(7, instance._on_gate_dir_changed)
        assert instance._gate_marker_watcher is watcher

    def test_change_debounces_a_review_lag_recheck(self) -> None:
        _app_mod, app_class = import_app()
        instance = self._instance(app_class)
        instance._gate_marker_watcher = mock.Mock()
        instance._gate_marker_watcher.drain.return_value = True
        instance._on_gate_dir_changed()
        instance.set_timer.assert_called_once()
        assert instance.set_timer.call_args.args[1] is instance._poll_review_lag

    def test_empty_drain_is_a_noop(self) -> None:
        _app_mod, app_class = import_app()
        instance = self._instance(app_class)
        instance._gate_marker_watcher = mock.Mock()
        instance._gate_marker_watcher.drain.return_value = False
        instance._on_gate_dir_changed()
        instance.set_timer.assert_not_called()

    def test_stop_detaches_and_clears(self) -> None:
        _app_mod, app_class = import_app()
        instance = self._instance(app_class)
        watcher = mock.Mock()
        watcher.fileno = 7
        instance._gate_marker_watcher = watcher
        instance._gate_watch_debounce = mock.Mock()
        loop = mock.Mock()
        with mock.patch("asyncio.get_running_loop", return_value=loop):
            instance._stop_gate_marker_watch()
        loop.remove_reader.assert_called_once_with(7)
        watcher.stop.assert_called_once()
        assert instance._gate_marker_watcher is None

    def test_stop_without_watcher_is_safe(self) -> None:
        _app_mod, app_class = import_app()
        instance = self._instance(app_class)
        instance._stop_gate_marker_watch()  # no watcher, no debounce
        assert instance._gate_marker_watcher is None
