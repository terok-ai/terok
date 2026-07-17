# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Pre-flight for re-encrypting the vault DB: who holds it, and how to let go.

``PRAGMA rekey`` needs the credentials DB exclusively: SQLCipher must
drain the WAL (whose frames are encrypted with the *old* key) before
re-encrypting, and SQLite refuses that while any other connection is
open — even an idle one.  Every running task qualifies: its host-side
supervisor's vault daemon holds a connection for the container's whole
life.  A passphrase change over a busy fleet therefore used to dead-end
with ``database is locked``.

This module turns the dead end into a conversation.  The front-ends
(TUI modal flow, CLI prompt flow) compose these verbs:

1. [`find_running_tasks`][terok.lib.domain.vault_rekey.find_running_tasks]
   — which tasks would pin the DB, so the operator can be offered the
   stop → re-encrypt → restart round trip instead of an error;
2. [`stop_tasks_for_rekey`][terok.lib.domain.vault_rekey.stop_tasks_for_rekey]
   / [`restart_tasks_after_rekey`][terok.lib.domain.vault_rekey.restart_tasks_after_rekey]
   — both halves of that round trip, error-collecting so one stubborn
   task cannot strand the rest;
3. [`wait_for_db_release`][terok.lib.domain.vault_rekey.wait_for_db_release]
   — confirm the connections are actually gone (supervisor teardown
   trails the container stop);
4. [`terminate_stale_holders`][terok.lib.domain.vault_rekey.terminate_stale_holders]
   — the orphan case: a supervisor whose poststop reap never fired
   still pins the DB with zero tasks running.  Kill exactly the holder
   pids, ours only — a foreign process is reported, never signalled.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from terok.lib.integrations.sandbox import SandboxConfig

logger = logging.getLogger(__name__)

#: Container states that hold a live supervisor (and thus a DB connection).
_STATES_PINNING_THE_DB = ("running", "paused")

#: Where the holder scan reads open file descriptors from.
_PROC = Path("/proc")

#: argv fingerprints of sandbox's own supervisor family — the module
#: invocation (``python -P -m terok_sandbox supervise-child …``) and the
#: installed wrapper script.  Only pids matching one of these are ever
#: signalled by [`terminate_stale_holders`][terok.lib.domain.vault_rekey.terminate_stale_holders].
_OWNED_CMDLINE_MARKS = (b"terok_sandbox", b"supervisor_wrapper.py")

#: Default patience for supervisor teardown after a task stop.
_DB_RELEASE_TIMEOUT_S = 15.0

#: How long a SIGTERMed holder gets to close down before SIGKILL.
_HOLDER_TERM_GRACE_S = 5.0


@dataclass(frozen=True)
class RunningTask:
    """One task whose live container pins the credentials DB."""

    project: str
    task_id: str

    def __str__(self) -> str:
        """Render as ``project/task_id`` — the fleet-wide task address."""
        return f"{self.project}/{self.task_id}"


@dataclass(frozen=True)
class DbHolder:
    """One process found holding the credentials DB (or a sidecar) open."""

    pid: int
    cmdline: str
    owned: bool
    """``True`` when the argv marks it as sandbox's own supervisor family —
    the only holders [`terminate_stale_holders`][terok.lib.domain.vault_rekey.terminate_stale_holders]
    will signal."""

    def __str__(self) -> str:
        """Render as ``pid NNN (argv…)`` for operator-facing listings."""
        return f"pid {self.pid} ({self.cmdline})"


def find_running_tasks() -> tuple[RunningTask, ...]:
    """Return every task across all projects whose container pins the DB.

    A project whose task or container-state query fails is skipped with
    a debug log — the holder scan
    ([`find_db_holders`][terok.lib.domain.vault_rekey.find_db_holders])
    stays the ground truth that actually guards the rekey; this
    enumeration only feeds the friendly stop/restart offer.
    """
    from ..core.projects import list_projects
    from ..orchestration.tasks import get_all_task_states, get_tasks

    found: list[RunningTask] = []
    for project in list_projects():
        try:
            tasks = get_tasks(project.name)
            states = get_all_task_states(project.name, tasks) if tasks else {}
        except Exception:
            logger.debug("vault rekey: failed to list tasks for %s", project.name, exc_info=True)
            continue
        if states is None:
            logger.debug("vault rekey: container state query failed for %s", project.name)
            continue
        found.extend(
            RunningTask(project.name, str(t.task_id))
            for t in tasks
            if t.mode and states.get(str(t.task_id)) in _STATES_PINNING_THE_DB
        )
    return tuple(found)


def stop_tasks_for_rekey(tasks: Iterable[RunningTask]) -> list[tuple[RunningTask, str | None]]:
    """Stop each task's container; collect per-task errors instead of raising.

    Returns one ``(task, error_or_None)`` row per input task, in order.
    A failed stop never aborts the sweep — the caller decides whether a
    partial fleet is good enough (it isn't for a rekey, but the
    successfully stopped tasks still need restarting).
    """
    from ..orchestration.tasks import task_stop

    return [(task, _swallowed_error(task_stop, task)) for task in tasks]


def restart_tasks_after_rekey(
    tasks: Iterable[RunningTask],
) -> list[tuple[RunningTask, str | None]]:
    """Bring the tasks stopped for the rekey back up; collect per-task errors.

    Same row shape as
    [`stop_tasks_for_rekey`][terok.lib.domain.vault_rekey.stop_tasks_for_rekey].
    Runs regardless of whether the rekey itself succeeded — a task
    stopped under the old passphrase restarts fine under either key,
    and leaving the fleet down after a failed change would punish the
    operator twice.
    """
    return [(task, _swallowed_error(_restart_one, task)) for task in tasks]


def find_db_holders(cfg: SandboxConfig | None = None) -> tuple[DbHolder, ...]:
    """Scan ``/proc`` for processes holding the credentials DB open.

    Matches any open fd under the DB path prefix, so the ``-wal`` /
    ``-shm`` sidecars count too — SQLite's journal-mode switch is
    refused while any of them is held.  Only same-uid processes are
    visible in a rootless install, which is exactly the set we could
    ever remediate.  The scanning process itself is excluded: terok
    closes its own connections around each vault operation, and
    offering the operator a self-kill would be absurd.
    """
    db_prefix = str(_db_path(cfg))
    holders = []
    for proc_dir in _PROC.glob("[0-9]*"):
        pid = int(proc_dir.name)
        if pid == os.getpid():
            continue
        if _holds_path_prefix(proc_dir, db_prefix):
            holders.append(_describe_holder(pid))
    return tuple(holders)


def wait_for_db_release(
    cfg: SandboxConfig | None = None, *, timeout_s: float = _DB_RELEASE_TIMEOUT_S
) -> tuple[DbHolder, ...]:
    """Poll until nothing holds the DB; return whoever still does at timeout.

    Supervisor teardown trails the container stop (the poststop hook
    SIGTERMs it asynchronously), so a scan right after
    [`stop_tasks_for_rekey`][terok.lib.domain.vault_rekey.stop_tasks_for_rekey]
    would cry wolf.  An empty result means the rekey may proceed;
    ``timeout_s=0`` degenerates to a single immediate scan.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        holders = find_db_holders(cfg)
        if not holders or time.monotonic() >= deadline:
            return holders
        time.sleep(0.5)


def terminate_stale_holders(
    holders: Sequence[DbHolder], cfg: SandboxConfig | None = None
) -> tuple[DbHolder, ...]:
    """SIGTERM the terok-owned holders, escalate to SIGKILL, re-scan.

    SIGTERM first because a supervisor parent reacts to it by tearing
    its service children down gracefully — the vault daemon closes its
    connection instead of dying mid-write.  Foreign pids (``owned`` is
    ``False``) are never signalled; the caller is expected to have
    refused the kill offer when any are present.

    Returns the holders still present after the sweep — empty means the
    DB is free and the rekey may proceed.
    """
    owned = [h for h in holders if h.owned]
    for holder in owned:
        with contextlib.suppress(OSError):
            os.kill(holder.pid, signal.SIGTERM)
    _await_exits([h.pid for h in owned], grace_s=_HOLDER_TERM_GRACE_S)
    for holder in owned:
        if _is_alive(holder.pid):
            with contextlib.suppress(OSError):
                os.kill(holder.pid, signal.SIGKILL)
    return wait_for_db_release(cfg, timeout_s=2.0)


# ── Internal helpers ────────────────────────────────────────────────────


def _restart_one(project: str, task_id: str) -> None:
    """Restart one task via the make-it-running ladder."""
    from ..orchestration.task_runners import task_restart

    task_restart(project, task_id)


def _swallowed_error(action: Callable[[str, str], None], task: RunningTask) -> str | None:
    """Run *action* on *task*; return its error message instead of raising.

    ``SystemExit`` is caught deliberately, not re-raised: the task
    lifecycle verbs (``task_stop`` / ``task_restart``) report failures
    by raising it, and a fleet sweep must record the row and carry on
    — one stubborn task exiting the whole flow would strand every
    other stopped task.  Same convention as the TUI's task actions.
    """
    try:
        action(task.project, task.task_id)
    except (Exception, SystemExit) as exc:
        return str(exc) or type(exc).__name__
    return None


def _db_path(cfg: SandboxConfig | None) -> Path:
    """Resolve the credentials-DB path under terok's effective config."""
    from ..core.config import make_sandbox_config

    return (cfg if cfg is not None else make_sandbox_config()).db_path


def _holds_path_prefix(proc_dir: Path, prefix: str) -> bool:
    """Return ``True`` when any fd of *proc_dir*'s process targets a path under *prefix*."""
    try:
        fds = list((proc_dir / "fd").iterdir())
    except OSError:  # process vanished, or not ours to inspect
        return False
    for fd in fds:
        with contextlib.suppress(OSError):
            if os.readlink(fd).startswith(prefix):
                return True
    return False


def _describe_holder(pid: int) -> DbHolder:
    """Build the operator-facing row for one holder pid."""
    raw = b""
    with contextlib.suppress(OSError):
        raw = (_PROC / str(pid) / "cmdline").read_bytes()
    cmdline = raw.replace(b"\0", b" ").decode(errors="replace").strip() or "?"
    owned = any(mark in raw for mark in _OWNED_CMDLINE_MARKS)
    return DbHolder(pid=pid, cmdline=cmdline, owned=owned)


def _await_exits(pids: Sequence[int], *, grace_s: float) -> None:
    """Wait up to *grace_s* for every pid in *pids* to exit."""
    deadline = time.monotonic() + grace_s
    while any(_is_alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.2)


def _is_alive(pid: int) -> bool:
    """Signal-0 liveness probe."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
