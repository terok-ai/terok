# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The inotify task watcher detects changes across a set of directories.

These run against real inotify (terok targets Linux), so they assert the actual
kernel behaviour the TUI relies on: a create, delete, rename or completed write
in any watched directory becomes a readable event, a quiet set yields nothing,
and the watched set can grow and shrink as tasks come and go.  Event delivery
for a local filesystem is synchronous to the inotify queue, so a ``drain``
immediately after the filesystem op already sees it.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from terok.tui.task_watcher import TaskWatcher


def _started(paths: Iterable[Path]) -> TaskWatcher:
    """Return a watcher armed on *paths* (skips if inotify is unavailable)."""
    watcher = TaskWatcher()
    assert watcher.start(paths), "inotify watch failed to arm"
    return watcher


class _UnavailableLibc:
    """libc stand-in whose ``inotify_init1`` fails — inotify is unavailable."""

    def inotify_init1(self, _flags: int) -> int:
        """Report the failure the kernel would when no inotify fd can be opened."""
        return -1


class TestLifecycle:
    """Arming, fd exposure, and teardown."""

    def test_start_exposes_a_real_fd(self, tmp_path: Path) -> None:
        watcher = _started([tmp_path])
        try:
            assert watcher.fileno >= 0
        finally:
            watcher.stop()

    def test_start_tolerates_a_missing_dir(self, tmp_path: Path) -> None:
        """A directory that doesn't exist yet is skipped, not fatal."""
        watcher = _started([tmp_path / "not-yet"])
        try:
            assert watcher.fileno >= 0  # fd is open even though nothing is watched
            assert watcher.drain() is False
        finally:
            watcher.stop()

    def test_stop_is_idempotent(self, tmp_path: Path) -> None:
        watcher = _started([tmp_path])
        watcher.stop()
        watcher.stop()  # second call must not raise
        assert watcher.fileno == -1

    def test_restart_keeps_the_fd_and_resyncs(self, tmp_path: Path) -> None:
        """A second start() reconciles the set in place — it never leaks a new fd."""
        meta, agent_cfg = tmp_path / "meta", tmp_path / "agent-config"
        meta.mkdir()
        agent_cfg.mkdir()
        watcher = _started([meta])
        try:
            fd = watcher.fileno
            # Re-arm with an extended set: the fd is reused, not reopened, and
            # the newly added directory comes under watch (i.e. sync ran).
            assert watcher.start([meta, agent_cfg]) is True
            assert watcher.fileno == fd
            (agent_cfg / "work-status.yml").write_text("x", encoding="utf-8")
            assert watcher.drain() is True
        finally:
            watcher.stop()

    def test_start_degrades_when_inotify_is_unavailable(self, tmp_path: Path) -> None:
        """A failed ``inotify_init1`` returns False so the caller can fall back.

        The one branch real inotify won't exercise: on a kernel without inotify
        ``start`` must report failure and leave no fd, never raise — that is the
        contract [`PollingMixin`][terok.tui.polling.PollingMixin] leans on to
        degrade to the periodic resync.  Override the instance's libc rather than
        the module-level ``_load_libc`` so the stub survives the fresh module
        re-import other TUI tests perform — patching the global would miss the
        stale class this test holds.
        """
        watcher = TaskWatcher()
        watcher._libc = _UnavailableLibc()  # next inotify_init1 reports failure
        assert watcher.start([tmp_path]) is False
        assert watcher.fileno == -1
        watcher.stop()  # idempotent with no fd to close


class TestDetectsChanges:
    """Every membership / lifecycle move surfaces as a drained event."""

    def test_file_create_is_seen(self, tmp_path: Path) -> None:
        watcher = _started([tmp_path])
        try:
            (tmp_path / "1.json").write_text("{}", encoding="utf-8")
            assert watcher.drain() is True
        finally:
            watcher.stop()

    def test_in_place_write_is_seen(self, tmp_path: Path) -> None:
        """A completed write (the ``ready_at`` update) fires CLOSE_WRITE."""
        meta = tmp_path / "1.json"
        meta.write_text("{}", encoding="utf-8")
        watcher = _started([tmp_path])
        try:
            assert watcher.drain() is False  # the pre-watch write isn't ours to see
            meta.write_text('{"ready_at": "now"}', encoding="utf-8")
            assert watcher.drain() is True
        finally:
            watcher.stop()

    def test_atomic_rename_is_seen(self, tmp_path: Path) -> None:
        """Temp-file-then-rename writes (work-status.yml) surface as MOVED_TO."""
        watcher = _started([tmp_path])
        try:
            tmp = tmp_path / ".work-status.yml.tmp"
            tmp.write_text("status: coding", encoding="utf-8")
            watcher.drain()  # clear the temp-file create/close events
            tmp.replace(tmp_path / "work-status.yml")
            assert watcher.drain() is True
        finally:
            watcher.stop()

    def test_delete_is_seen(self, tmp_path: Path) -> None:
        meta = tmp_path / "1.json"
        meta.write_text("{}", encoding="utf-8")
        watcher = _started([tmp_path])
        try:
            watcher.drain()
            meta.unlink()
            assert watcher.drain() is True
        finally:
            watcher.stop()

    def test_quiet_directory_drains_empty(self, tmp_path: Path) -> None:
        watcher = _started([tmp_path])
        try:
            assert watcher.drain() is False
        finally:
            watcher.stop()

    def test_change_in_any_watched_dir_is_seen(self, tmp_path: Path) -> None:
        """The whole point of the class: one fd, several dirs, each independent.

        A task's metadata dir and its agent-config dir live in different trees;
        a change in *either* must surface, so the metadata write and the later
        ``work-status.yml`` write are each seen on their own.
        """
        meta, agent_cfg = tmp_path / "meta", tmp_path / "agent-config"
        meta.mkdir()
        agent_cfg.mkdir()
        watcher = _started([meta, agent_cfg])
        try:
            (meta / "1.json").write_text("{}", encoding="utf-8")
            assert watcher.drain() is True  # a write in the metadata dir fires
            (agent_cfg / "work-status.yml").write_text("status: coding", encoding="utf-8")
            assert watcher.drain() is True  # ...and so does one in the agent-config dir
        finally:
            watcher.stop()


class TestSync:
    """The watched set follows the live tasks as they come and go."""

    def test_sync_picks_up_a_new_directory(self, tmp_path: Path) -> None:
        meta, agent_cfg = tmp_path / "meta", tmp_path / "agent-config"
        meta.mkdir()
        agent_cfg.mkdir()
        watcher = _started([meta])
        try:
            # agent-config isn't watched yet — its writes are invisible.
            (agent_cfg / "work-status.yml").write_text("a", encoding="utf-8")
            assert watcher.drain() is False
            # Once synced in, the same directory's writes are seen.
            watcher.sync([meta, agent_cfg])
            (agent_cfg / "work-status.yml").write_text("b", encoding="utf-8")
            assert watcher.drain() is True
        finally:
            watcher.stop()

    def test_sync_drops_a_departed_directory(self, tmp_path: Path) -> None:
        meta, gone = tmp_path / "meta", tmp_path / "gone"
        meta.mkdir()
        gone.mkdir()
        watcher = _started([meta, gone])
        try:
            watcher.sync([meta])  # task deleted → its dir leaves the set
            # rm_watch queues one IN_IGNORED bookkeeping event; clear it (in
            # production it costs at most one harmless idempotent reconcile).
            watcher.drain()
            (gone / "x").write_text("x", encoding="utf-8")
            assert watcher.drain() is False  # no longer watched
        finally:
            watcher.stop()

    def test_sync_before_start_is_inert(self, tmp_path: Path) -> None:
        """sync() on an unstarted watcher does nothing (no fd to add to)."""
        watcher = TaskWatcher()
        watcher.sync([tmp_path])  # must not raise
        assert watcher.fileno == -1

    def test_partial_set_still_watches_the_reachable_dirs(self, tmp_path: Path) -> None:
        """An unwatchable dir is skipped without disarming its watchable siblings.

        A task's agent-config dir doesn't exist until its container starts, so a
        sync routinely mixes present and absent paths; the present ones must
        still fire while the absent one is simply picked up by a later sync.
        """
        present, absent = tmp_path / "present", tmp_path / "absent"
        present.mkdir()  # `absent` is never created
        watcher = _started([present, absent])
        try:
            assert watcher.fileno >= 0
            (present / "x.json").write_text("{}", encoding="utf-8")
            assert watcher.drain() is True
        finally:
            watcher.stop()
