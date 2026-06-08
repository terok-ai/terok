#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Polling mixin for the TerokTUI app.

Extracts upstream polling, container status polling, and auto-sync logic
from the main app module into a reusable mixin class.
"""

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.app import App
    from textual.timer import Timer

    from terok.lib.api.gate import GateStalenessInfo

    from ..lib.api import ContainerEventStream, TaskMeta
    from .task_watcher import TaskWatcher

    # At type-check time only, inherit from textual.App so all of its methods
    # (run_worker, set_interval, notify, …) resolve naturally on `self` with
    # the *real* signatures — no risk of MRO conflicts on TerokTUI. At
    # runtime the mixin still inherits from `object`.
    _MixinBase = App
else:
    _MixinBase = object

# Container-state tracking is event-driven.  Two push sources cover what used to
# be a 2-second poll: an inotify watch on the task-metadata and agent-config
# directories (every host-side change — create, delete, ``ready_at``,
# ``exit_code``, ``work-status.yml``) and a ``podman events`` stream (container
# up/down with no host write).  The periodic resync is then only insurance
# against a missed event — slow by default (configurable; see
# ``tui.container_resync_seconds``), in the spirit of a Kubernetes informer
# resync.  A monitor that can't trust inotify dials it back down to seconds.
#
# Coalesce a burst of events (a metadata file rewritten field-by-field, or a
# start+health pair) into one reconcile rather than firing per event.
_WATCH_DEBOUNCE_S = 0.2


class PollingMixin(_MixinBase):
    """Mixin providing upstream and container status polling for the TUI app."""

    if TYPE_CHECKING:
        # State the host (TerokTUI) initialises — the mixin owns the polling
        # lifecycle but stores its bookkeeping on the host instance.
        current_task: "TaskMeta | None"
        _staleness_info: "GateStalenessInfo | None"
        _polling_timer: "Timer | None"
        _polling_project_name: str | None
        _last_notified_stale: bool
        _auto_sync_cooldown: dict[str, float]
        _container_status_timer: "Timer | None"
        _task_watcher: "TaskWatcher | None"
        _container_event_stream: "ContainerEventStream | None"
        _watch_debounce: "Timer | None"

        # TerokTUI helpers (not on textual.App).
        current_project_name: str | None

        def _log_debug(self, message: str) -> None: ...
        def _refresh_project_state(self) -> None: ...

    # ---------- Upstream polling ----------

    def _start_upstream_polling(self) -> None:
        """Start background polling for upstream changes.

        Only polls for gatekeeping projects with polling enabled and a gate initialized.
        """
        from ..lib.api import load_project

        self._stop_upstream_polling()  # Stop any existing timer
        self._staleness_info = None
        self._last_notified_stale = False

        if not self.current_project_name:
            return

        try:
            project = load_project(self.current_project_name)
        except SystemExit:
            return

        # Only poll for gatekeeping projects with polling enabled
        if project.security_class != "gatekeeping":
            return
        if not project.upstream_polling_enabled:
            return
        if not project.gate_path.exists():
            return

        interval_seconds = project.upstream_polling_interval_minutes * 60
        self._polling_project_name = self.current_project_name

        # Perform initial poll immediately (in background worker)
        self._poll_upstream()

        # Schedule recurring polls
        self._polling_timer = self.set_interval(
            interval_seconds, self._poll_upstream, name="upstream_polling"
        )

    def _stop_upstream_polling(self) -> None:
        """Stop the upstream polling timer."""
        if self._polling_timer is not None:
            self._polling_timer.stop()
            self._polling_timer = None
        self._polling_project_name = None

    def _start_container_status_polling(self) -> None:
        """Track container status from events, with a slow resync as insurance.

        Seeds the initial state once, then wires two push sources for the
        current project — an inotify watch on its task-metadata and agent-config
        directories, and a ``podman events`` stream — so changes reconcile the
        instant they happen.  A periodic full resync runs only as insurance
        against a missed event, at the configured (slow by default) interval;
        ``0`` disables it for a purely event-driven session.
        """
        from ..lib.core.config import get_tui_container_resync_seconds

        self._stop_container_status_polling()
        if not self.current_project_name:
            return
        self._start_task_watcher(self.current_project_name)
        self._start_container_event_worker(self.current_project_name)
        # Seed after both push sources are armed, so a change in the startup
        # window can't slip past unseen between snapshot and first event.
        self._poll_container_status()
        resync_seconds = get_tui_container_resync_seconds()
        if resync_seconds > 0:
            self._container_status_timer = self.set_interval(
                resync_seconds, self._poll_container_status, name="container_status_resync"
            )

    def _stop_container_status_polling(self) -> None:
        """Stop the resync timer and tear down both push sources."""
        if self._container_status_timer is not None:
            self._container_status_timer.stop()
            self._container_status_timer = None
        self._stop_task_watcher()
        self._stop_container_event_stream()

    # ---------- inotify watch (host-side task files) ----------

    def _start_task_watcher(self, project_name: str) -> bool:
        """Arm an inotify watch on *project_name*'s task + agent-config dirs.

        Returns ``True`` once the watch fd is registered on the event loop;
        ``False`` (the resync carries the project alone) if inotify is
        unavailable or there's no running loop to attach the fd to.
        """
        import asyncio

        from .task_watcher import TaskWatcher

        try:
            watcher = TaskWatcher()
        except Exception as e:  # noqa: BLE001 — watch is best-effort; resync covers it
            self._log_debug(f"task watcher init error: {e}")
            return False
        if not watcher.start(self._task_watch_paths(project_name)):
            return False
        try:
            asyncio.get_running_loop().add_reader(watcher.fileno, self._on_task_dir_changed)
        except (RuntimeError, ValueError, OSError, NotImplementedError) as e:
            self._log_debug(f"task watcher attach error: {e}")
            watcher.stop()
            return False
        self._task_watcher = watcher
        return True

    def _task_watch_paths(self, project_name: str) -> list[Path]:
        """The directories to watch: the metadata dir plus each task's config dir.

        The metadata dir reveals membership and lifecycle (``ready_at``,
        ``exit_code``); each task's ``agent-config`` dir reveals its
        ``work-status.yml`` updates.  Reads the task set from disk, so it's
        called only on start and on a membership change — never on the event
        hot path.  Best-effort: a path that doesn't resolve is omitted.
        """
        from ..lib.api import agent_config_dir, get_tasks, tasks_meta_dir

        paths: list[Path] = []
        try:
            paths.append(tasks_meta_dir(project_name))
            tasks = get_tasks(project_name)
        except Exception as e:  # noqa: BLE001 — a bad project name just means no metadata watch
            self._log_debug(f"task watch path error: {e}")
            return paths
        for task in tasks:
            try:
                paths.append(agent_config_dir(project_name, task.task_id))
            except Exception:  # noqa: BLE001 # nosec B112 — skip tasks whose config dir won't resolve
                continue
        return paths

    def _resync_task_watches(self) -> None:
        """Re-point the inotify watch at the current task set (new/removed dirs)."""
        if self._task_watcher is None or not self.current_project_name:
            return
        self._task_watcher.sync(self._task_watch_paths(self.current_project_name))

    def _stop_task_watcher(self) -> None:
        """Detach and close the inotify watch and any pending debounce."""
        if self._watch_debounce is not None:
            self._watch_debounce.stop()
            self._watch_debounce = None
        if self._task_watcher is None:
            return
        import asyncio

        try:
            asyncio.get_running_loop().remove_reader(self._task_watcher.fileno)
        except (RuntimeError, ValueError, OSError):
            pass
        self._task_watcher.stop()
        self._task_watcher = None

    def _on_task_dir_changed(self) -> None:
        """React to inotify activity: drain events, then debounce a reconcile."""
        if self._task_watcher is None or not self._task_watcher.drain():
            return
        self._schedule_reconcile()

    # ---------- podman event stream (container up/down) ----------

    def _start_container_event_worker(self, project_name: str) -> None:
        """Subscribe to podman container events and reconcile on each.

        Opens the stream on the UI thread (so the handle is held synchronously
        for teardown) and iterates it on a worker thread, since reading blocks
        between events.  No-op when the runtime can't stream events — the
        inotify watch and resync still cover the project.
        """
        import functools

        from ..lib.api import container_event_stream

        stream = container_event_stream(project_name)
        if stream is None:
            return
        self._container_event_stream = stream
        self.run_worker(
            functools.partial(self._drain_container_events, stream, project_name),
            name=f"container-events:{project_name}",
            group="container-events",
            thread=True,
            exclusive=True,
        )

    def _drain_container_events(self, stream: "ContainerEventStream", project_name: str) -> None:
        """Worker thread: reconcile on each container event until the stream closes.

        Teardown closes the stream, which unblocks the parked ``readline`` and
        ends the iteration; any error (podman gone) likewise ends it, leaving
        the resync in charge.
        """
        try:
            for _event in stream:
                self.call_from_thread(self._on_container_event, project_name)
        except Exception as e:  # noqa: BLE001 — stream died; resync covers the gap
            self._log_debug(f"container event stream ended: {e}")

    def _on_container_event(self, project_name: str) -> None:
        """UI thread: debounce a reconcile for a container event (current project)."""
        if project_name == self.current_project_name:
            self._schedule_reconcile()

    def _stop_container_event_stream(self) -> None:
        """Close the podman event subscription, unblocking its worker thread."""
        if self._container_event_stream is None:
            return
        try:
            self._container_event_stream.close()
        except Exception:  # noqa: BLE001 # nosec B110 — best-effort teardown
            pass
        self._container_event_stream = None

    def _schedule_reconcile(self) -> None:
        """Debounce a reconcile so a burst of events collapses into one."""
        if self._watch_debounce is not None:
            self._watch_debounce.stop()
        self._watch_debounce = self.set_timer(_WATCH_DEBOUNCE_S, self._poll_container_status)

    def _poll_container_status(self) -> None:
        """Check container status for all visible tasks via a single batch query."""
        if not self.current_project_name:
            return
        self._queue_container_state_check(self.current_project_name)

    def _queue_container_state_check(self, project_name: str) -> None:
        """Queue a background batch check for all task container states."""
        self.run_worker(
            self._load_container_state_worker(project_name),
            name=f"container-state:{project_name}",
            group="container-state",
            exclusive=True,
        )

    async def _load_container_state_worker(self, project_name: str) -> tuple[str, list["TaskMeta"]]:
        """Batch-snapshot every task for a project with live container state.

        Returns fresh ``TaskMeta`` instances — the task set on disk plus each
        one's live container state — so the handler can both detect tasks
        created or deleted outside the TUI *and* refresh the lifecycle fields
        (init marker, work status, exit code) that drift on rows already shown.
        """
        import asyncio

        from ..lib.api import get_all_task_states, get_tasks

        def _snapshot() -> list["TaskMeta"]:
            tasks = get_tasks(project_name)
            states = get_all_task_states(project_name, tasks)
            for task in tasks:
                task.container_state = states.get(task.task_id)
            return tasks

        try:
            tasks = await asyncio.get_event_loop().run_in_executor(None, _snapshot)
            return (project_name, tasks)
        except (Exception, SystemExit) as e:  # noqa: BLE001 — background worker; must not crash TUI
            self._log_debug(f"container state batch check error: {e}")
            return (project_name, [])

    def _poll_upstream(self) -> None:
        """Check upstream for changes and update staleness info.

        Runs the actual comparison in a background worker to avoid blocking the UI.
        """
        project_name = self._polling_project_name
        if not project_name or project_name != self.current_project_name:
            # Project changed since timer was started, skip this poll
            return

        self._log_debug(f"polling upstream for {project_name}")
        # Run blocking git operation in background worker
        self.run_worker(
            self._poll_upstream_worker(project_name),
            name="poll_upstream",
            exclusive=True,  # Cancel any previous poll still running
        )

    async def _poll_upstream_worker(self, project_name: str) -> None:
        """Background worker to check upstream (runs in thread pool)."""
        import asyncio

        from ..lib.api import load_project, make_git_gate

        try:
            # Run blocking call in thread pool
            staleness = await asyncio.get_event_loop().run_in_executor(
                None, lambda: make_git_gate(load_project(project_name)).compare_vs_upstream()
            )

            # Validate project hasn't changed while we were polling
            if project_name != self.current_project_name:
                return

            self._on_staleness_updated(project_name, staleness)

        except (Exception, SystemExit) as e:  # noqa: BLE001 — background worker; must not crash TUI
            self._log_debug(f"upstream poll error: {e}")

    def _on_staleness_updated(self, project_name: str, staleness: "GateStalenessInfo") -> None:
        """Handle updated staleness info."""
        # Double-check project hasn't changed
        if project_name != self.current_project_name:
            return

        self._staleness_info = staleness

        # Only update notification state for valid (non-error) comparisons
        if staleness.error:
            # Don't change notification state on errors - preserve previous state
            pass
        elif staleness.is_stale and not self._last_notified_stale:
            behind_str = ""
            if staleness.commits_behind is not None:
                behind_str = f" ({staleness.commits_behind} commits behind)"
            self.notify(f"Gate is behind upstream on {staleness.branch}{behind_str}")
            self._last_notified_stale = True

            # Trigger auto-sync if enabled (with cooldown check)
            self._maybe_auto_sync(project_name)
        elif not staleness.is_stale:
            # Only reset when we have confirmed up-to-date status
            self._last_notified_stale = False

        # Refresh the project state display
        self._refresh_project_state()

    def _maybe_auto_sync(self, project_name: str) -> None:
        """Trigger auto-sync if enabled for this project.

        Runs sync in background worker to avoid blocking UI.
        Implements cooldown to prevent sync loops.
        """
        import time

        from ..lib.api import load_project

        if not project_name or project_name != self.current_project_name:
            return

        # Check cooldown (5 minute minimum between auto-syncs per project)
        now = time.time()
        cooldown_until = self._auto_sync_cooldown.get(project_name, 0)
        if now < cooldown_until:
            self._log_debug("auto-sync skipped: cooldown active")
            return

        try:
            project = load_project(project_name)
            if not project.auto_sync_enabled:
                return

            # Set cooldown before starting sync (5 minutes)
            self._auto_sync_cooldown[project_name] = now + 300

            self._log_debug(f"auto-syncing gate for {project_name}")
            self.notify("Auto-syncing gate from upstream...")

            # Run sync in background worker
            branches = project.auto_sync_branches or None
            self.run_worker(
                self._sync_worker(project_name, branches, is_auto=True),
                name="auto_sync",
                exclusive=True,
            )

        except (Exception, SystemExit) as e:  # noqa: BLE001 — background worker; must not crash TUI
            self._log_debug(f"auto-sync error: {e}")

    async def _sync_worker(
        self, project_name: str, branches: list[str] | None = None, is_auto: bool = False
    ) -> None:
        """Background worker to sync gate from upstream."""
        import asyncio

        from ..lib.api import load_project, make_git_gate

        try:
            # Run blocking sync in thread pool
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: make_git_gate(load_project(project_name)).sync_branches(branches)
            )

            # Validate project hasn't changed
            if project_name != self.current_project_name:
                return

            if result["success"]:
                label = "Auto-synced" if is_auto else "Synced"
                self.notify(f"{label} gate from upstream")

                # Re-check staleness after sync
                staleness = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: make_git_gate(load_project(project_name)).compare_vs_upstream()
                )

                if project_name == self.current_project_name:
                    self._staleness_info = staleness
                    # Only reset notification flag if we're actually up-to-date now
                    if not staleness.is_stale and not staleness.error:
                        self._last_notified_stale = False
                    self._refresh_project_state()
            else:
                label = "Auto-sync" if is_auto else "Sync"
                self.notify(f"{label} failed: {', '.join(result['errors'])}")

        except (Exception, SystemExit) as e:  # noqa: BLE001 — background worker; must not crash TUI
            label = "Auto-sync" if is_auto else "Sync"
            self.notify(f"{label} error: {e}")
