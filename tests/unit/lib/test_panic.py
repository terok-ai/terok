# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Emergency shutdown never depends on untrusted task telemetry."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from terok.lib.domain import panic
from terok.lib.integrations.sandbox import RuntimeContainer


@pytest.fixture
def emergency(monkeypatch, tmp_path):
    """Replace host services and runtime operations with independent test doubles."""
    runtime = MagicMock()
    runtime.inventory.return_value = []
    monkeypatch.setattr(panic, "PodmanRuntime", lambda: runtime)
    monkeypatch.setattr(panic, "core_state_dir", lambda: tmp_path)
    for name in ("_kill_supervisors", "_stop_vault", "_raise_shield"):
        monkeypatch.setattr(panic, name, MagicMock(return_value=[]))
    return runtime


def _container(root, name="task", state="running", **annotations):
    """Make a runtime row with trusted annotations but no task metadata on disk."""
    return RuntimeContainer(
        id=("a" if name == "task" else "b") * 64,
        name=name,
        state=state,
        annotations={
            panic._DOSSIER_ANNOTATION: str(root / "projects" / "p" / "tasks" / "1_dossier.json"),
            **annotations,
        },
    )


def test_kills_live_containers_without_task_files(emergency, tmp_path):
    """Missing sidecars and corrupt task status cannot hide runtime containers."""
    (tmp_path / "work-status.yml").write_text("message: 2026-99-99")
    running = _container(tmp_path)
    paused = _container(tmp_path, "paused", "paused")
    emergency.inventory.return_value = [running, paused, _container(tmp_path, "old", "exited")]
    result = panic.execute_panic()
    assert result.total_running == 2
    assert sorted(result.containers_stopped) == ["paused", "task"]
    assert not result.has_errors
    assert {c.args[0] for c in emergency.force_stop.call_args_list} == {running.id, paused.id}
    assert panic.is_panicked()
    panic.clear_panic_lock()
    assert not panic.is_panicked()


@pytest.mark.parametrize(
    "suffix", ["foreign", "projects/p/tasks/../evil", "projects/../tasks/1_dossier.json"]
)
def test_does_not_kill_unrelated_containers(emergency, tmp_path, suffix):
    """Do not guess ownership from a container name or a foreign annotation."""
    target = _container(tmp_path)
    target.annotations[panic._DOSSIER_ANNOTATION] = str(tmp_path / suffix)
    emergency.inventory.return_value = [target]
    assert panic.execute_panic().total_running == 0
    emergency.force_stop.assert_not_called()


def test_each_failure_is_independent(emergency, tmp_path, monkeypatch):
    """A broken interlock, vault, supervisor or shield never prevents a kill."""
    emergency.inventory.return_value = [_container(tmp_path, **{panic._SHIELD_ANNOTATION: "bad"})]
    for name in ("_write_panic_lock", "_stop_vault", "_kill_supervisors", "_raise_shield"):
        monkeypatch.setattr(panic, name, MagicMock(side_effect=SystemExit(name)))
    result = panic.execute_panic()
    assert result.containers_stopped == ["task"]
    assert (
        result.lock_error
        and result.vault_error
        and result.supervisor_errors
        and result.shield_errors
    )
    assert result.has_errors
    report = panic.format_panic_report(result)
    assert "Containers killed: 1" in report
    assert "restart interlock:" in report


def test_inventory_failure_does_not_skip_global_shutdown(emergency):
    """Runtime failure is visible while host-side access is still cut."""
    emergency.inventory.side_effect = OSError("runtime unavailable")
    result = panic.execute_panic()
    assert result.discovery_error == "runtime unavailable"
    panic._kill_supervisors.assert_called_once()
    panic._stop_vault.assert_called_once()
    assert panic.is_panicked()


def test_one_kill_failure_does_not_skip_another(emergency, tmp_path):
    """Each container gets its own shutdown attempt."""
    emergency.inventory.return_value = [_container(tmp_path), _container(tmp_path, "other")]

    def stop(container_id, **_kwargs):
        if container_id.startswith("a"):
            raise OSError("cannot stop")

    emergency.force_stop.side_effect = stop
    result = panic.execute_panic()
    assert result.containers_stopped == ["other"]
    assert result.container_stop_errors == [("task", "cannot stop")]


def test_hung_actions_have_one_deadline(emergency, tmp_path, monkeypatch):
    """A wedged helper neither postpones kills nor mutates the result after return."""
    release = threading.Event()
    emergency.inventory.return_value = [_container(tmp_path)]
    monkeypatch.setattr(panic, "_PANIC_TIMEOUT_S", 0.1)
    monkeypatch.setattr(panic, "_stop_vault", lambda: release.wait(5))
    started = time.monotonic()
    try:
        result = panic.execute_panic()
        assert time.monotonic() - started < 2
        assert result.containers_stopped == ["task"]
        assert "timed out" in result.vault_error
        assert not result.vault_stopped
    finally:
        release.set()
    assert not result.vault_stopped


def test_supervisor_partial_failure_is_reported(emergency):
    """Preserve the per-supervisor success and failure rows."""
    panic._kill_supervisors.return_value = [("first", None), ("second", "refused")]
    result = panic.execute_panic()
    assert result.supervisors_killed == ["first"]
    assert result.supervisor_errors == [("second", "refused")]


@pytest.mark.parametrize("state", ["stopping", "restarting"])
def test_transitional_live_states_are_also_killed(emergency, tmp_path, state):
    """An earlier graceful stop must not buy an untrusted task extra time."""
    emergency.inventory.return_value = [_container(tmp_path, state=state)]
    assert panic.execute_panic().containers_stopped == ["task"]


def test_worker_start_failure_does_not_skip_other_actions(emergency, monkeypatch):
    """Thread resource failures are per-action errors rather than aborting panic."""
    original = threading.Thread.start

    def start(thread):
        if thread.name == "panic-vault-":
            raise RuntimeError("cannot start worker")
        original(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    result = panic.execute_panic()
    assert result.vault_error == "cannot start worker"
    panic._kill_supervisors.assert_called_once()
    emergency.inventory.assert_called_once()
    assert panic.is_panicked()
