# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""In-memory registry of dispatched-action console logs.

Backs the web-compatible replacement for terok's old ``app.suspend()``
workflows (issue #473).  A dispatched action — image build, gate sync,
vault op, container start — runs as a child process whose merged
stdout/stderr is pumped line-by-line into a
[`ConsoleLogEntry`][terok.tui.console_log.ConsoleLogEntry].

The entry outlives any modal viewing it: the pump worker is
*app-scoped*, not screen-scoped, so a
[`WorkerLogScreen`][terok.tui.worker_log_screen.WorkerLogScreen] can be
hidden to the background and the same entry re-opened later from the
``Console output`` palette command.  Entries live only for the TUI
session — there is no on-disk log.

The child process runs via the `_worker_entry` module (a referenced
callable) or a raw argv; see
[`worker_argv`][terok.tui.console_log.worker_argv].

``dispatch_*`` is deliberately **UI-free**: it registers an entry and
starts the pump, nothing more.  Showing a live view is always a
separate, explicit step — so a background action (e.g. the CLI-task
launch modal's container start) stays in the background unless the
user asks for it.
"""

from __future__ import annotations

import asyncio
import enum
import itertools
import json
import shlex
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from textual import work

if TYPE_CHECKING:
    from textual.app import App

    _MixinBase = App
else:
    _MixinBase = object

#: Module path of the child-process entrypoint (the `_worker_entry` module).
_WORKER_ENTRY_MODULE = "terok.tui._worker_entry"


class LogStatus(enum.Enum):
    """Lifecycle state of a [`ConsoleLogEntry`][terok.tui.console_log.ConsoleLogEntry]."""

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


def worker_argv(ref: str, args: Sequence[object]) -> list[str]:
    """Build the child-process argv for a referenced callable.

    *ref* is ``"module.path:function"``; *args* must be
    JSON-serialisable positional arguments.  Runs the interpreter with
    ``-u`` so the child's stdout reaches the parent's pump unbuffered
    (a pipe is block-buffered by default, which would stall the live
    log).
    """
    return [sys.executable, "-u", "-m", _WORKER_ENTRY_MODULE, ref, json.dumps(list(args))]


@dataclass(eq=False)
class ConsoleLogEntry:
    """One dispatched action's captured output plus its lifecycle state."""

    id: int
    """Session-unique, monotonically increasing entry id."""

    title: str
    """Human-facing label, e.g. ``"Building images for myproject"``."""

    argv: list[str]
    """The child-process argv whose output this entry captures."""

    command: str
    """Human-readable form of what ran, e.g. ``"terok setup"`` — the raw
    ``_worker_entry`` argv is noise, so dispatch passes a tidy display string."""

    status: LogStatus = LogStatus.RUNNING
    """Current lifecycle state — flips to ``DONE`` / ``FAILED`` on exit."""

    lines: list[str] = field(default_factory=list)
    """Captured output lines, in order, newline-stripped."""

    exit_code: int | None = None
    """Child exit status; ``None`` while still running."""

    started_at: float = field(default_factory=time.monotonic)
    """``time.monotonic()`` at dispatch."""

    ended_at: float | None = None
    """``time.monotonic()`` at completion; ``None`` while running."""

    _line_subs: list[Callable[[str], None]] = field(default_factory=list, repr=False)
    _finish_subs: list[Callable[[], None]] = field(default_factory=list, repr=False)
    _done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def running(self) -> bool:
        """``True`` while the child process has not yet exited."""
        return self.status is LogStatus.RUNNING

    @property
    def ok(self) -> bool:
        """``True`` once the child process has exited with code 0."""
        return self.exit_code == 0

    def append(self, line: str) -> None:
        """Append a captured output *line* and notify live subscribers."""
        self.lines.append(line)
        for callback in tuple(self._line_subs):
            callback(line)

    def finish(self, exit_code: int) -> None:
        """Mark the entry finished with *exit_code*, notify, drop subscribers."""
        self.exit_code = exit_code
        self.status = LogStatus.DONE if exit_code == 0 else LogStatus.FAILED
        self.ended_at = time.monotonic()
        for callback in tuple(self._finish_subs):
            callback()
        self._line_subs.clear()
        self._finish_subs.clear()
        self._done.set()

    async def wait(self) -> None:
        """Block until the entry finishes — unaffected by hiding any viewer.

        Lets a caller that genuinely needs the outcome (e.g. the
        first-run flow deciding whether to chain into the wizard) await
        completion without holding a modal open: the pump worker is
        app-scoped, so the entry finishes whether or not a
        [`WorkerLogScreen`][terok.tui.worker_log_screen.WorkerLogScreen]
        is on stage.
        """
        await self._done.wait()

    def subscribe(
        self,
        on_line: Callable[[str], None],
        on_finish: Callable[[], None] | None = None,
    ) -> Callable[[], None]:
        """Register live-tailing callbacks; return an unsubscribe callable.

        *on_line* fires for each new line; *on_finish*, if given, fires
        once when the entry finishes.  A viewer replays
        [`lines`][terok.tui.console_log.ConsoleLogEntry.lines] on mount,
        then subscribes for live tailing and unsubscribes on unmount.
        Subscribing an already-finished entry is a harmless no-op (the
        caller is expected to check [`running`][terok.tui.console_log.ConsoleLogEntry.running]
        first); the returned callable still works.
        """
        if self.running:
            self._line_subs.append(on_line)
            if on_finish is not None:
                self._finish_subs.append(on_finish)

        def _unsubscribe() -> None:
            for subs, cb in ((self._line_subs, on_line), (self._finish_subs, on_finish)):
                if cb is not None:
                    try:
                        subs.remove(cb)  # type: ignore[arg-type]
                    except ValueError:
                        pass

        return _unsubscribe


class ConsoleLogRegistry:
    """Session-scoped store of [`ConsoleLogEntry`][terok.tui.console_log.ConsoleLogEntry] objects."""

    def __init__(self) -> None:
        """Start an empty registry with a fresh id counter."""
        self._entries: list[ConsoleLogEntry] = []
        self._ids = itertools.count(1)

    def create(self, title: str, argv: list[str], command: str) -> ConsoleLogEntry:
        """Register a new ``RUNNING`` entry and return it.

        *command* is the human-readable display string; *argv* is what
        actually runs.
        """
        entry = ConsoleLogEntry(id=next(self._ids), title=title, argv=list(argv), command=command)
        self._entries.append(entry)
        return entry

    @property
    def entries(self) -> list[ConsoleLogEntry]:
        """All entries, most-recent first."""
        return list(reversed(self._entries))

    @property
    def running(self) -> list[ConsoleLogEntry]:
        """Currently-running entries, most-recent first."""
        return [entry for entry in self.entries if entry.running]


class ConsoleLogMixin(_MixinBase):
    """Dispatch helpers that run an action as a captured child process.

    Mixed into [`TerokTUI`][terok.tui.app.TerokTUI]; the registry lives
    at ``self.console_logs``.  The pump worker is started via the app's
    ``@work`` machinery, so it is app-scoped and survives the dismissal
    of any [`WorkerLogScreen`][terok.tui.worker_log_screen.WorkerLogScreen]
    viewing it — that is what makes "hide to background" possible.
    """

    if TYPE_CHECKING:
        console_logs: ConsoleLogRegistry

    def dispatch_console_action(
        self,
        ref: str,
        *args: object,
        title: str,
        on_complete: Callable[[ConsoleLogEntry], None] | None = None,
    ) -> ConsoleLogEntry:
        """Dispatch a referenced callable as a captured background child process.

        *ref* is ``"module.path:function"`` and *args* must be
        JSON-serialisable.  Returns the
        [`ConsoleLogEntry`][terok.tui.console_log.ConsoleLogEntry]
        immediately — **no screen is pushed**.  Callers that want a
        live view push a
        [`WorkerLogScreen`][terok.tui.worker_log_screen.WorkerLogScreen]
        over the returned entry themselves; callers that want it to
        stay in the background just keep the entry.

        *on_complete*, if given, runs on the event loop once the child
        exits — regardless of whether a viewer is open — so an action
        can refresh derived state (project list, task list) even when
        its log was hidden to the background.
        """
        command = " ".join([ref, *(str(arg) for arg in args)])
        return self._dispatch_console(worker_argv(ref, args), command, title, on_complete)

    def dispatch_console_command(
        self,
        argv: list[str],
        *,
        title: str,
        on_complete: Callable[[ConsoleLogEntry], None] | None = None,
    ) -> ConsoleLogEntry:
        """Dispatch a raw *argv* as a captured background child process.

        For genuine external commands such as ``["terok", "setup"]``.
        Same UI-free contract as
        [`dispatch_console_action`][terok.tui.console_log.ConsoleLogMixin.dispatch_console_action].
        """
        return self._dispatch_console(list(argv), shlex.join(argv), title, on_complete)

    def _dispatch_console(
        self,
        argv: list[str],
        command: str,
        title: str,
        on_complete: Callable[[ConsoleLogEntry], None] | None,
    ) -> ConsoleLogEntry:
        """Register the entry and kick off the app-scoped pump worker."""
        entry = self.console_logs.create(title, argv, command)
        self._pump_console_entry(entry, on_complete)
        return entry

    @work(group="console-log", exit_on_error=False)
    async def _pump_console_entry(
        self,
        entry: ConsoleLogEntry,
        on_complete: Callable[[ConsoleLogEntry], None] | None,
    ) -> None:
        """Spawn the child for *entry*, stream its output in, finish + notify on exit.

        ``start_new_session=True`` + ``stdin=DEVNULL`` detach the child
        from the controlling terminal so nothing below it can reach
        ``/dev/tty`` and draw over the Textual frame — the same guard
        the wizard's ``_run_isolated`` helper already relies on.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                *entry.argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            entry.append(f"[failed to launch] {exc}")
            self._finalise_console_entry(entry, 127, on_complete)
            return

        assert proc.stdout is not None
        async for raw in proc.stdout:
            entry.append(raw.decode(errors="replace").rstrip("\n"))
        self._finalise_console_entry(entry, await proc.wait(), on_complete)

    def _finalise_console_entry(
        self,
        entry: ConsoleLogEntry,
        exit_code: int,
        on_complete: Callable[[ConsoleLogEntry], None] | None,
    ) -> None:
        """Append the exit marker, finish *entry*, fire the toast, run *on_complete*.

        The marker goes through [`append`][terok.tui.console_log.ConsoleLogEntry.append]
        *before* [`finish`][terok.tui.console_log.ConsoleLogEntry.finish] so
        a live viewer prints it, and it lands in
        [`lines`][terok.tui.console_log.ConsoleLogEntry.lines] so a viewer
        opened later from ``Console output`` sees it too.
        """
        entry.append("")
        entry.append("— exited 0 —" if exit_code == 0 else f"— exited with code {exit_code} —")
        entry.finish(exit_code)
        self._notify_console_done(entry)
        if on_complete is not None:
            on_complete(entry)

    def _notify_console_done(self, entry: ConsoleLogEntry) -> None:
        """Fire the completion toast for a finished *entry*."""
        if entry.ok:
            self.notify(f"{entry.title} — done", timeout=6)
        else:
            self.notify(
                f"{entry.title} — failed (exit {entry.exit_code}). "
                f"Open “Console output” to see the log.",
                severity="error",
                timeout=10,
            )
