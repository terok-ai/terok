# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""inotify-backed watcher for a task's host-side files.

The TUI's task list is driven by files on the host: a task appears as a new
metadata file, its ``ready_at`` init marker and ``exit_code`` land as in-place
writes, a delete unlinks the file, and the agent reports progress by writing
``work-status.yml`` into its (bind-mounted) ``agent-config`` directory.  Polling
those directories on a timer wakes the disk forever even when nothing changes;
an inotify watch instead stays idle until the kernel reports an actual change.

Because the metadata directory and each task's ``agent-config`` directory live
in different trees, a watcher follows a *set* of directories that grows and
shrinks as tasks come and go.  [`sync`][terok.tui.task_watcher.TaskWatcher.sync]
reconciles that set against the live tasks; all of them share one inotify fd, so
the loop integration stays a single reader.

This module owns only the OS mechanism — open an inotify instance, watch a set
of directories, expose the readable fd, drain pending events, close.  Loop
registration, debouncing and the reconcile reaction live in
[`PollingMixin`][terok.tui.polling.PollingMixin], which keeps this class free of
asyncio/Textual coupling and testable against real directories.

inotify is Linux-only.  That is the only platform terok's Podman runtime
targets, and the watched files are always on a local filesystem (never NFS), so
the watch is reliable here.  A failure to open the inotify instance degrades to
``False`` from [`start`][terok.tui.task_watcher.TaskWatcher.start] so the caller
can fall back to the periodic resync rather than crash.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from collections.abc import Iterable
from pathlib import Path

# inotify_init1 flag — hand back a non-blocking fd so a fully drained read
# raises EAGAIN (BlockingIOError) instead of stalling the event loop.
_IN_NONBLOCK = 0o4000

# Watch mask.  Membership moves (create / delete / rename in or out) plus
# CLOSE_WRITE, which fires when an in-place write to a file finishes — that is
# how the ``ready_at`` / ``exit_code`` and ``work-status.yml`` updates surface.
# Atomic temp-file-then-rename writes surface as MOVED_TO.  We never inspect
# *which* file moved: any event on a watched directory means "reconcile".
_IN_CREATE = 0x00000100
_IN_DELETE = 0x00000200
_IN_MOVED_FROM = 0x00000040
_IN_MOVED_TO = 0x00000080
_IN_CLOSE_WRITE = 0x00000008
_WATCH_MASK = _IN_CREATE | _IN_DELETE | _IN_MOVED_FROM | _IN_MOVED_TO | _IN_CLOSE_WRITE

# One read drains every event queued since the last wake-up; the buffer only
# needs to clear the kernel queue, not hold a single event.
_READ_BUFFER_BYTES = 4096


def _load_libc() -> ctypes.CDLL:
    """Bind the three inotify syscalls from libc with errno tracking."""
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
    return libc


class TaskWatcher:
    """Watch a changing set of directories for changes via one inotify fd."""

    def __init__(self) -> None:
        """Bind libc; no syscalls until ``start``."""
        self._libc = _load_libc()
        self._fd = -1
        self._watches: dict[Path, int] = {}

    @property
    def fileno(self) -> int:
        """The inotify fd, ready for ``loop.add_reader``.  ``-1`` until started."""
        return self._fd

    def start(self, paths: Iterable[Path]) -> bool:
        """Open the inotify instance and watch *paths*.

        Returns ``True`` once the fd is open (whatever subset of *paths* could
        be watched), ``False`` only if inotify itself is unavailable — the
        caller then leans on the periodic resync.  Individual directories that
        can't be watched yet (e.g. an ``agent-config`` dir not created until
        the container starts) are skipped and picked up by a later
        [`sync`][terok.tui.task_watcher.TaskWatcher.sync].
        """
        fd = self._libc.inotify_init1(_IN_NONBLOCK)
        if fd < 0:
            return False
        self._fd = fd
        self.sync(paths)
        return True

    def sync(self, paths: Iterable[Path]) -> None:
        """Reconcile the watched set to *paths* — add new, drop departed.

        Best-effort: a path that can't be watched (doesn't exist yet) is simply
        absent until the next sync finds it.  Called whenever the task set
        changes so new tasks' ``agent-config`` directories come under watch and
        deleted ones are released.
        """
        if self._fd < 0:
            return
        desired = {Path(p) for p in paths}
        for departed in self._watches.keys() - desired:
            self._unwatch(departed)
        for added in desired - self._watches.keys():
            self._watch(added)

    def _watch(self, path: Path) -> None:
        """Add a watch for *path*, ignoring a directory that can't be watched."""
        wd = self._libc.inotify_add_watch(self._fd, str(path).encode(), _WATCH_MASK)
        if wd >= 0:
            self._watches[path] = wd

    def _unwatch(self, path: Path) -> None:
        """Drop the watch for *path* if held."""
        wd = self._watches.pop(path, None)
        if wd is not None:
            self._libc.inotify_rm_watch(self._fd, wd)

    def drain(self) -> bool:
        """Read and discard every queued event; report whether any were seen.

        The watch mask already restricts events to relevant ones, so the
        payload never needs parsing — clearing the queue and signalling "a
        watched directory changed" is enough to trigger a single reconcile.
        """
        seen = False
        while True:
            try:
                data = os.read(self._fd, _READ_BUFFER_BYTES)
            except BlockingIOError:
                break
            if not data:
                break
            seen = True
        return seen

    def stop(self) -> None:
        """Close the fd, dropping every watch with it.  Idempotent."""
        if self._fd < 0:
            return
        os.close(self._fd)
        self._fd = -1
        self._watches.clear()


__all__ = ["TaskWatcher"]
