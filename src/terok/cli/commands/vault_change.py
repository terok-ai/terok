# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Rekey pre-flight overlay for the ``vault passphrase change`` verb.

Sandbox's handler owns the passphrase conversation and the rekey; what
it cannot know about is terok's fleet.  ``PRAGMA rekey`` needs the
credentials DB exclusively, and every running task's supervisor holds a
connection — so the bare verb dead-ends with ``database is locked`` on
a busy host.  This overlay wraps the handler with the same conversation
the TUI flow runs ([`TerokTUI._clear_rekey_blockers`][terok.tui.app]):
offer to stop the running tasks and restart them afterwards, sweep
supervisor processes orphaned by an earlier stop, and refuse — loudly,
naming pids — when a foreign process pins the DB.

The prompts only run on a TTY.  Piped stdin belongs to the handler's
own line-per-value passphrase protocol, so a non-interactive call with
blockers present fails fast with the full picture instead of consuming
a line that was meant to be a passphrase.
"""

from __future__ import annotations

import functools
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from terok.lib.api import CommandTree
    from terok.lib.api.vault import RunningTask

#: The wired-tree path of the verb this module overlays (the ``vault``
#: shortcut shares node identity with this deep path, so one overlay
#: covers both spellings).
_CHANGE_VERB_PATH = ("sandbox", "vault", "passphrase", "change")


def wrap_passphrase_change(tree: CommandTree) -> CommandTree:
    """Overlay the change verb with the rekey pre-flight conversation.

    A tree without the verb (an older sandbox wheel) passes through
    untouched — the bare handler's ``database is locked`` error is
    still a correct, if less helpful, outcome.
    """
    try:
        cmd = tree.find_at(_CHANGE_VERB_PATH)
    except KeyError:
        return tree
    if cmd is None or cmd.handler is None:
        return tree
    return tree.overlay({_CHANGE_VERB_PATH: _with_rekey_preflight(cmd.handler)})


def _with_rekey_preflight(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap *handler* so the DB is freed first and stopped tasks come back after."""

    @functools.wraps(handler)
    def wrapped(**kwargs: Any) -> Any:
        stopped = _clear_rekey_blockers()
        try:
            return handler(**kwargs)
        finally:
            casualties = _restart_stopped(stopped)
            if casualties and sys.exc_info()[0] is None:
                # With an exception already in flight its message owns the
                # exit; the casualty listing above still told the story.
                raise SystemExit(
                    "some tasks could not be restarted — bring each back with:"
                    " terok task restart <project> <task>"
                )

    return wrapped


def _clear_rekey_blockers() -> list[RunningTask]:
    """Free the credentials DB; return the tasks stopped here (owed a restart).

    Mirrors the TUI conversation: running tasks → offer the stop →
    re-encrypt → restart round trip; orphaned supervisor holders →
    offer to kill exactly those pids; foreign holders → refuse.  Every
    refusal restarts whatever was already stopped, then raises
    ``SystemExit`` before the handler consumed any input.
    """
    from terok.lib.api.vault import (
        find_running_tasks,
        stop_tasks_for_rekey,
        terminate_stale_holders,
        wait_for_db_release,
    )

    running = find_running_tasks()
    stopped: list[RunningTask] = []
    if running:
        listing = "\n".join(f"  • {t}" for t in running)
        print(
            "Re-encrypting the credentials DB needs exclusive access, but"
            f" {len(running)} running task(s) hold it open:\n{listing}"
        )
        _confirm_or_exit(
            "Stop them, re-encrypt, and restart them afterwards?",
            refusal="passphrase change cancelled — no tasks were touched.",
        )
        results = stop_tasks_for_rekey(running)
        stopped = [task for task, error in results if error is None]
        failed = [(task, error) for task, error in results if error is not None]
        if failed:
            details = "\n".join(f"  ✗ {task}: {error}" for task, error in failed)
            _restart_stopped(stopped)
            raise SystemExit(f"could not stop every task — nothing was changed:\n{details}")

    holders = wait_for_db_release(timeout_s=15.0 if stopped else 0.0)
    if not holders:
        return stopped
    listing = "\n".join(f"  • {h}" for h in holders)
    if any(not h.owned for h in holders):
        _restart_stopped(stopped)
        raise SystemExit(
            "processes outside terok are holding the credentials DB open —"
            f" close them and retry:\n{listing}\nnothing was changed."
        )
    print(
        "No running task explains these supervisor processes still holding"
        f" the credentials DB (likely orphaned by an earlier stop):\n{listing}"
    )
    _confirm_or_exit(
        "Kill them and continue?",
        refusal="passphrase change cancelled — nothing was touched.",
        stopped=stopped,
    )
    survivors = terminate_stale_holders(holders)
    if survivors:
        listing = "\n".join(f"  • {h}" for h in survivors)
        _restart_stopped(stopped)
        raise SystemExit(f"the credentials DB is still held open — nothing was changed:\n{listing}")
    return stopped


def _confirm_or_exit(
    question: str, *, refusal: str, stopped: list[RunningTask] | None = None
) -> None:
    """Put *question* to the operator; on anything but yes, restart and exit.

    Never touches stdin off a TTY — piped input belongs to the
    handler's passphrase protocol, so a blocked non-interactive run
    exits with the situation report instead of a mangled prompt.
    """
    if not sys.stdin.isatty():
        _restart_stopped(stopped or [])
        raise SystemExit(
            "cannot ask for confirmation without a TTY — stop the listed"
            " blockers first, or run the change interactively."
        )
    if input(f"{question} [y/N] ").strip().lower() not in ("y", "yes"):
        _restart_stopped(stopped or [])
        raise SystemExit(refusal)


def _restart_stopped(stopped: list[RunningTask]) -> list[RunningTask]:
    """Bring back the tasks stopped for the rekey; return the casualties.

    Prints one ladder per task (the restart output is the operator's
    live progress view) and a ✗ line per failure — but never raises:
    this runs on every exit path, success or not, and the caller
    decides what a casualty means for the exit code.
    """
    from terok.lib.api.vault import restart_tasks_after_rekey

    if not stopped:
        return []
    print(f"→ restarting {len(stopped)} task(s) …")
    casualties = []
    for task, error in restart_tasks_after_rekey(stopped):
        if error is not None:
            print(f"  ✗ {task}: {error}")
            casualties.append(task)
    return casualties


__all__ = ["wrap_passphrase_change"]
