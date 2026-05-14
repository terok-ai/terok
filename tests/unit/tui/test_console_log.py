# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ConsoleLog registry, dispatch pump, and _worker_entry.

The registry / entry layer is pure data + callbacks and is tested
directly.  The dispatch pump is driven through a minimal
[`ConsoleLogMixin`][terok.tui.console_log.ConsoleLogMixin] host app under
Textual's ``run_test`` harness so the real ``asyncio.subprocess`` /
pipe plumbing gets exercised end-to-end.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from textual.app import App

from terok.tui import _worker_entry
from terok.tui.console_log import (
    ConsoleLogEntry,
    ConsoleLogMixin,
    ConsoleLogRegistry,
    LogStatus,
    child_process_env,
    worker_argv,
)

# ── ConsoleLogEntry ───────────────────────────────────────────────────


def _entry(**overrides: object) -> ConsoleLogEntry:
    """Build a RUNNING entry with sane defaults for focused assertions."""
    base: dict[str, object] = {"id": 1, "title": "t", "argv": ["x"], "command": "x"}
    base.update(overrides)
    return ConsoleLogEntry(**base)


def test_append_notifies_line_subscribers_and_records() -> None:
    """``append`` both records the line and fans it out to live subscribers."""
    entry = _entry()
    seen: list[str] = []
    entry.subscribe(seen.append)
    entry.append("one")
    entry.append("two")
    assert seen == ["one", "two"]
    assert entry.lines == ["one", "two"]


def test_finish_flips_state_notifies_and_drops_subscribers() -> None:
    """``finish`` sets terminal state, fires on_finish, then ignores later lines."""
    entry = _entry()
    lines: list[str] = []
    finished: list[bool] = []
    entry.subscribe(lines.append, lambda: finished.append(True))

    entry.finish(0)
    assert entry.status is LogStatus.DONE
    assert entry.ok and not entry.running
    assert entry.ended_at is not None
    assert finished == [True]

    entry.append("after finish")
    assert lines == [], "subscribers must be dropped once finished"
    assert entry.lines == ["after finish"], "line is still recorded"


def test_finish_nonzero_is_failed_not_ok() -> None:
    """A non-zero exit code lands the entry in FAILED / not-ok."""
    entry = _entry()
    entry.finish(7)
    assert entry.status is LogStatus.FAILED
    assert entry.exit_code == 7 and not entry.ok


def test_unsubscribe_stops_delivery() -> None:
    """The handle returned by ``subscribe`` removes both callbacks."""
    entry = _entry()
    seen: list[str] = []
    unsubscribe = entry.subscribe(seen.append)
    entry.append("kept")
    unsubscribe()
    entry.append("dropped")
    assert seen == ["kept"]


def test_subscribe_finished_entry_is_noop() -> None:
    """Subscribing an already-finished entry never fires — caller checks ``running``."""
    entry = _entry()
    entry.finish(0)
    seen: list[str] = []
    fired: list[bool] = []
    entry.subscribe(seen.append, lambda: fired.append(True))
    entry.append("x")
    assert seen == [] and fired == []


@pytest.mark.asyncio
async def test_wait_resolves_on_finish() -> None:
    """``wait`` blocks until ``finish`` is called, then returns immediately after."""
    entry = _entry()

    async def _finish_soon() -> None:
        await asyncio.sleep(0)
        entry.finish(0)

    await asyncio.gather(entry.wait(), _finish_soon())
    # Already-finished: a second wait returns without blocking.
    await asyncio.wait_for(entry.wait(), timeout=1.0)


# ── ConsoleLogRegistry ────────────────────────────────────────────────


def test_registry_allocates_ids_and_orders_recent_first() -> None:
    """``create`` hands out monotonically increasing ids; ``entries`` is newest-first."""
    registry = ConsoleLogRegistry()
    first = registry.create("a", ["x"], "x")
    second = registry.create("b", ["y"], "y")
    assert (first.id, second.id) == (1, 2)
    assert [e.id for e in registry.entries] == [2, 1]


def test_registry_running_filters_finished() -> None:
    """``running`` excludes entries whose child process has exited."""
    registry = ConsoleLogRegistry()
    done = registry.create("done", ["x"], "x")
    live = registry.create("live", ["y"], "y")
    done.finish(0)
    assert [e.id for e in registry.running] == [live.id]


# ── worker_argv ───────────────────────────────────────────────────────


def test_worker_argv_structure() -> None:
    """``worker_argv`` produces an unbuffered ``-m _worker_entry`` invocation."""
    argv = worker_argv("terok.lib.api:build_images", ["proj", True])
    assert argv[0] == sys.executable
    assert argv[1:4] == ["-u", "-m", "terok.tui._worker_entry"]
    assert argv[4] == "terok.lib.api:build_images"
    assert argv[5] == '["proj", true]'


# ── child_process_env (Nix #717 shim) ─────────────────────────────────


def test_child_process_env_threads_sys_path_as_pythonpath() -> None:
    """The child env carries the parent's ``sys.path`` as ``PYTHONPATH`` (#717)."""
    env = child_process_env()
    assert env["PYTHONPATH"] == os.pathsep.join(sys.path)
    # And it still inherits the parent environment.
    assert "PATH" in env


def test_child_process_env_pythonpath_wins_over_overrides() -> None:
    """An ambient/override ``PYTHONPATH`` can never shadow the parent's real path."""
    env = child_process_env({"FOO": "bar", "PYTHONPATH": "ambient-junk"})
    assert env["FOO"] == "bar"
    assert env["PYTHONPATH"] == os.pathsep.join(sys.path)


# ── _worker_entry.main ────────────────────────────────────────────────


def test_worker_entry_success() -> None:
    """A resolvable callable with valid JSON args runs and exits 0."""
    assert _worker_entry.main(["builtins:print", '["hi"]']) == 0


def test_worker_entry_bad_ref_exits_2() -> None:
    """An unresolvable reference exits 2 (malformed invocation)."""
    assert _worker_entry.main(["builtins:does_not_exist", "[]"]) == 2


def test_worker_entry_colonless_ref_exits_2() -> None:
    """A ref without a 'module:function' colon is rejected as a malformed invocation."""
    assert _worker_entry.main(["no-colon-here", "[]"]) == 2


def test_worker_entry_non_callable_ref_exits_2() -> None:
    """A ref that resolves to a non-callable is rejected at resolve time, not at call time."""
    # sys.version is a str — resolvable, but not callable.
    assert _worker_entry.main(["sys:version", "[]"]) == 2


def test_worker_entry_malformed_argv_exits_2() -> None:
    """Wrong argument count exits 2 with a usage line."""
    assert _worker_entry.main(["only-one"]) == 2
    assert _worker_entry.main(["a", "b", "c"]) == 2


def test_worker_entry_non_list_json_exits_2() -> None:
    """JSON args that are not an array exit 2."""
    assert _worker_entry.main(["builtins:print", '{"not": "a list"}']) == 2


def test_worker_entry_systemexit_int_propagates() -> None:
    """A ``SystemExit`` from the callable propagates — the child exits with its code."""
    with pytest.raises(SystemExit) as exc:
        _worker_entry.main(["sys:exit", "[5]"])
    assert exc.value.code == 5
    with pytest.raises(SystemExit) as exc_zero:
        _worker_entry.main(["sys:exit", "[0]"])
    assert exc_zero.value.code == 0


def test_worker_entry_systemexit_str_propagates() -> None:
    """A string ``SystemExit`` code propagates — the interpreter prints it and exits 1."""
    with pytest.raises(SystemExit) as exc:
        _worker_entry.main(["sys:exit", '["something broke"]'])
    assert exc.value.code == "something broke"


def test_worker_entry_exception_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    """An unhandled exception exits 1 with the traceback in the captured output."""
    # int("nope") raises ValueError — a stand-in for any facade-level crash.
    assert _worker_entry.main(["builtins:int", '["nope"]']) == 1
    assert "ValueError" in capsys.readouterr().err


# ── ConsoleLogMixin dispatch pump (end-to-end) ────────────────────────


class _DispatchHost(ConsoleLogMixin, App):
    """Minimal app exercising the dispatch pump against real subprocesses."""

    def __init__(self) -> None:
        """Start with an empty registry."""
        super().__init__()
        self.console_logs = ConsoleLogRegistry()


@pytest.mark.asyncio
async def test_dispatch_command_captures_output_and_finishes() -> None:
    """``dispatch_console_command`` streams a child's stdout into the entry and finishes ok."""
    app = _DispatchHost()
    completed: list[ConsoleLogEntry] = []
    async with app.run_test():
        entry = app.dispatch_console_command(
            [sys.executable, "-c", "print('line one'); print('line two')"],
            title="echo test",
            on_complete=completed.append,
        )
        await asyncio.wait_for(entry.wait(), timeout=10.0)
    assert entry.ok and entry.status is LogStatus.DONE
    assert "line one" in entry.lines and "line two" in entry.lines
    assert entry.lines[-1] == "— exited 0 —"
    assert completed == [entry], "on_complete must fire for the finished entry"


@pytest.mark.asyncio
async def test_dispatch_nonzero_marks_failed() -> None:
    """A child that exits non-zero leaves the entry FAILED with the code recorded."""
    app = _DispatchHost()
    async with app.run_test():
        entry = app.dispatch_console_command(
            [sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"],
            title="failing test",
        )
        await asyncio.wait_for(entry.wait(), timeout=10.0)
    assert not entry.ok and entry.exit_code == 3
    assert entry.status is LogStatus.FAILED
    assert "boom" in entry.lines
    assert entry.lines[-1] == "— exited with code 3 —"


@pytest.mark.asyncio
async def test_dispatch_missing_executable_surfaces_as_127() -> None:
    """A command that cannot be launched is reported in the log, not silently lost."""
    app = _DispatchHost()
    async with app.run_test():
        entry = app.dispatch_console_command(
            ["/nonexistent/terok-never-existed"], title="missing exe"
        )
        await asyncio.wait_for(entry.wait(), timeout=10.0)
    assert entry.exit_code == 127 and not entry.ok
    assert any("failed to launch" in line for line in entry.lines)


@pytest.mark.asyncio
async def test_dispatch_action_runs_referenced_callable() -> None:
    """``dispatch_console_action`` resolves a ``module:function`` ref in a child process."""
    app = _DispatchHost()
    async with app.run_test():
        # builtins:print is a safe, dependency-free stand-in for a facade call.
        entry = app.dispatch_console_action(
            "builtins:print", "from the worker", title="action test"
        )
        await asyncio.wait_for(entry.wait(), timeout=10.0)
    assert entry.ok
    assert "from the worker" in entry.lines
    assert entry.command == "builtins:print from the worker"
