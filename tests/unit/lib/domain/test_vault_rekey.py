# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the rekey pre-flight domain module.

The holder scan runs against a fake ``/proc`` built in ``tmp_path``
(pid directories with ``fd/`` symlinks and ``cmdline`` files), the
fleet enumeration against patched project/task queries — no live
podman, no real signals: ``os.kill`` is recorded, never delivered.
"""

from __future__ import annotations

import os
import signal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from terok.lib.domain import vault_rekey
from terok.lib.domain.vault_rekey import (
    DbHolder,
    RunningTask,
    find_db_holders,
    find_running_tasks,
    restart_tasks_after_rekey,
    stop_tasks_for_rekey,
    terminate_stale_holders,
    wait_for_db_release,
)

_SUPERVISOR_ARGV = b"/usr/bin/python\0-P\0-m\0terok_sandbox\0supervise-child\0vault\0abc123\0"
_FOREIGN_ARGV = b"/usr/bin/sqlitebrowser\0/home/op/creds.db\0"


@pytest.fixture
def fake_vault(tmp_path: Path) -> SimpleNamespace:
    """A cfg stand-in whose ``db_path`` points into ``tmp_path``."""
    db_path = tmp_path / "vault" / "credentials.db"
    db_path.parent.mkdir(parents=True)
    db_path.touch()
    return SimpleNamespace(db_path=db_path)


@pytest.fixture
def fake_proc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty fake ``/proc`` the scan walks instead of the real one."""
    proc = tmp_path / "proc"
    proc.mkdir()
    monkeypatch.setattr(vault_rekey, "_PROC", proc)
    return proc


def _add_process(proc: Path, pid: int, *, cmdline: bytes, holds: Path | None = None) -> None:
    """Materialise one fake process, optionally holding *holds* open."""
    pid_dir = proc / str(pid)
    (pid_dir / "fd").mkdir(parents=True)
    (pid_dir / "cmdline").write_bytes(cmdline)
    if holds is not None:
        (pid_dir / "fd" / "4").symlink_to(holds)


class TestFindDbHolders:
    """The ``/proc`` fd scan — ground truth for "is the rekey possible"."""

    def test_finds_supervisor_holding_the_db(
        self, fake_proc: Path, fake_vault: SimpleNamespace
    ) -> None:
        """A supervisor-family argv holding the DB file is an owned holder."""
        _add_process(fake_proc, 4242, cmdline=_SUPERVISOR_ARGV, holds=fake_vault.db_path)

        holders = find_db_holders(fake_vault)

        assert [(h.pid, h.owned) for h in holders] == [(4242, True)]
        assert "supervise-child" in holders[0].cmdline

    def test_wal_sidecar_counts_as_holding(
        self, fake_proc: Path, fake_vault: SimpleNamespace
    ) -> None:
        """SQLite refuses the journal-mode switch over a held ``-wal`` too."""
        wal = fake_vault.db_path.with_name(fake_vault.db_path.name + "-wal")
        wal.touch()
        _add_process(fake_proc, 77, cmdline=_SUPERVISOR_ARGV, holds=wal)

        assert [h.pid for h in find_db_holders(fake_vault)] == [77]

    def test_foreign_process_is_reported_not_owned(
        self, fake_proc: Path, fake_vault: SimpleNamespace
    ) -> None:
        """A non-terok argv is listed so the operator sees it — but never ``owned``."""
        _add_process(fake_proc, 99, cmdline=_FOREIGN_ARGV, holds=fake_vault.db_path)

        holders = find_db_holders(fake_vault)

        assert [(h.pid, h.owned) for h in holders] == [(99, False)]

    def test_unrelated_fds_do_not_match(self, fake_proc: Path, fake_vault: SimpleNamespace) -> None:
        """Holding some other file is not holding the credentials DB."""
        other = fake_vault.db_path.parent / "unrelated.log"
        other.touch()
        _add_process(fake_proc, 11, cmdline=_SUPERVISOR_ARGV, holds=other)

        assert find_db_holders(fake_vault) == ()

    def test_own_pid_is_excluded(self, fake_proc: Path, fake_vault: SimpleNamespace) -> None:
        """The scanning process never lists (or later kills) itself."""
        _add_process(fake_proc, os.getpid(), cmdline=_SUPERVISOR_ARGV, holds=fake_vault.db_path)

        assert find_db_holders(fake_vault) == ()


class TestWaitForDbRelease:
    """Polling out the asynchronous supervisor teardown."""

    def test_returns_empty_once_released(
        self, fake_vault: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A holder that lets go within the timeout yields a clean result."""
        holder = DbHolder(pid=1, cmdline="x", owned=True)
        scans = iter([(holder,), ()])
        monkeypatch.setattr(vault_rekey, "find_db_holders", lambda cfg=None: next(scans))

        assert wait_for_db_release(fake_vault, timeout_s=5.0) == ()

    def test_zero_timeout_is_a_single_scan(
        self, fake_vault: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With nothing stopped there is nothing to wait out — scan once, report."""
        holder = DbHolder(pid=1, cmdline="x", owned=True)
        scan = MagicMock(return_value=(holder,))
        monkeypatch.setattr(vault_rekey, "find_db_holders", scan)

        assert wait_for_db_release(fake_vault, timeout_s=0.0) == (holder,)
        assert scan.call_count == 1


class TestTerminateStaleHolders:
    """The orphan sweep — ours only, graceful first."""

    def test_sigterms_owned_and_never_foreign(
        self, fake_vault: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Owned pids get SIGTERM; a foreign pid is never signalled."""
        ours = DbHolder(pid=101, cmdline="terok_sandbox", owned=True)
        theirs = DbHolder(pid=202, cmdline="sqlitebrowser", owned=False)
        sent: list[tuple[int, int]] = []

        def fake_kill(pid: int, sig: int) -> None:
            sent.append((pid, sig))
            if sig == 0:
                raise ProcessLookupError  # SIGTERM "worked" — pid is gone

        monkeypatch.setattr(os, "kill", fake_kill)
        monkeypatch.setattr(vault_rekey, "wait_for_db_release", lambda cfg=None, timeout_s=0: ())

        assert terminate_stale_holders([ours, theirs], fake_vault) == ()
        assert (101, signal.SIGTERM) in sent
        assert all(pid != 202 for pid, sig in sent if sig != 0)

    def test_escalates_to_sigkill_when_term_is_ignored(
        self, fake_vault: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A holder that shrugs off SIGTERM gets SIGKILL after the grace window."""
        stubborn = DbHolder(pid=303, cmdline="terok_sandbox", owned=True)
        sent: list[tuple[int, int]] = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
        monkeypatch.setattr(vault_rekey, "_HOLDER_TERM_GRACE_S", 0.0)
        monkeypatch.setattr(
            vault_rekey, "wait_for_db_release", lambda cfg=None, timeout_s=0: (stubborn,)
        )

        assert terminate_stale_holders([stubborn], fake_vault) == (stubborn,)
        assert (303, signal.SIGTERM) in sent
        assert (303, signal.SIGKILL) in sent


class TestFindRunningTasks:
    """Fleet-wide enumeration feeding the stop/restart offer."""

    def test_collects_running_and_paused_across_projects(self) -> None:
        """Only live container states qualify; stopped tasks don't pin the DB."""
        projects = [SimpleNamespace(name="alpha"), SimpleNamespace(name="beta")]
        tasks = {
            "alpha": [
                SimpleNamespace(task_id="1", mode="cli"),
                SimpleNamespace(task_id="2", mode="cli"),
            ],
            "beta": [SimpleNamespace(task_id="7", mode="toad")],
        }
        states = {
            "alpha": {"1": "running", "2": "exited"},
            "beta": {"7": "paused"},
        }
        with (
            patch("terok.lib.core.projects.list_projects", return_value=projects),
            patch("terok.lib.orchestration.tasks.get_tasks", side_effect=lambda p: tasks[p]),
            patch(
                "terok.lib.orchestration.tasks.get_all_task_states",
                side_effect=lambda p, t: states[p],
            ),
        ):
            found = find_running_tasks()

        assert found == (RunningTask("alpha", "1"), RunningTask("beta", "7"))

    def test_failed_state_query_skips_the_project(self) -> None:
        """``None`` states (podman busy, #1134) must not masquerade as running tasks."""
        projects = [SimpleNamespace(name="alpha")]
        tasks = [SimpleNamespace(task_id="1", mode="cli")]
        with (
            patch("terok.lib.core.projects.list_projects", return_value=projects),
            patch("terok.lib.orchestration.tasks.get_tasks", return_value=tasks),
            patch("terok.lib.orchestration.tasks.get_all_task_states", return_value=None),
        ):
            assert find_running_tasks() == ()

    def test_broken_project_is_skipped_not_fatal(self) -> None:
        """One unreadable project must not sink the fleet-wide sweep."""
        projects = [SimpleNamespace(name="broken"), SimpleNamespace(name="ok")]

        def tasks_for(project: str) -> list:
            if project == "broken":
                raise RuntimeError("corrupt meta")
            return [SimpleNamespace(task_id="3", mode="cli")]

        with (
            patch("terok.lib.core.projects.list_projects", return_value=projects),
            patch("terok.lib.orchestration.tasks.get_tasks", side_effect=tasks_for),
            patch(
                "terok.lib.orchestration.tasks.get_all_task_states",
                return_value={"3": "running"},
            ),
        ):
            assert find_running_tasks() == (RunningTask("ok", "3"),)


class TestStopRestartSweeps:
    """Error-collecting round-trip halves — one stubborn task never strands the rest."""

    def test_stop_collects_errors_and_continues(self) -> None:
        """A refused stop lands in its row; later tasks still get stopped."""
        rows = [RunningTask("alpha", "1"), RunningTask("alpha", "2")]

        def stop(project: str, task_id: str) -> None:
            if task_id == "1":
                raise SystemExit("container does not exist")

        with patch("terok.lib.orchestration.tasks.task_stop", side_effect=stop) as stop_mock:
            results = stop_tasks_for_rekey(rows)

        assert results == [
            (rows[0], "container does not exist"),
            (rows[1], None),
        ]
        assert stop_mock.call_count == 2

    def test_restart_collects_errors_and_continues(self) -> None:
        """Same discipline on the way back up."""
        rows = [RunningTask("alpha", "1"), RunningTask("beta", "7")]

        def restart(project: str, task_id: str) -> None:
            if project == "beta":
                raise RuntimeError("image gone")

        with patch(
            "terok.lib.orchestration.task_runners.task_restart", side_effect=restart
        ) as restart_mock:
            results = restart_tasks_after_rekey(rows)

        assert results == [(rows[0], None), (rows[1], "image gone")]
        assert restart_mock.call_count == 2
