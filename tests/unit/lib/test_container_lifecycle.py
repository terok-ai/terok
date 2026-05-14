# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for container lifecycle helpers: state, stop, restart, and status."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from terok_sandbox import PodmanRuntime

from terok.lib.orchestration.task_runners import task_restart
from terok.lib.orchestration.tasks import (
    get_task_container_state,
    read_task_meta,
    task_new,
    task_status,
    task_stop,
    write_task_meta,
)
from tests.test_utils import mock_git_config, project_env


def project_config(project_id: str, *, shutdown_timeout: int | None = None) -> str:
    """Build a minimal project config, optionally overriding shutdown timeout."""
    lines = [f"project:\n  id: {project_id}"]
    if shutdown_timeout is not None:
        lines.append(f"run:\n  shutdown_timeout: {shutdown_timeout}")
    return "\n".join(lines) + "\n"


def task_meta_path(ctx: SimpleNamespace, project_id: str, task_id: str) -> Path:
    """Return the metadata path for *task_id* inside the temporary project env."""
    return ctx.state_dir / "projects" / project_id / "tasks" / f"{task_id}_dossier.json"


def update_task_meta(
    ctx: SimpleNamespace, project_id: str, task_id: str, **changes: object
) -> None:
    """Patch selected metadata keys for a generated task."""
    dossier_handle = task_meta_path(ctx, project_id, task_id)
    meta = read_task_meta(dossier_handle.parent, task_id) or {}
    meta.update(changes)
    write_task_meta(dossier_handle, meta)


def create_task_with_mode(ctx: SimpleNamespace, project_id: str, *, mode: str = "cli") -> str:
    """Create a new task and persist the requested mode in its metadata."""
    task_id = task_new(project_id)
    update_task_meta(ctx, project_id, task_id, mode=mode)
    return task_id


def capture_stdout(func: Callable[..., object], /, *args: object, **kwargs: object) -> str:
    """Run *func* and return its captured stdout."""
    output = StringIO()
    with redirect_stdout(output):
        func(*args, **kwargs)
    return output.getvalue()


def run_podman_args(run_mock: Mock, *, call_index: int = 0) -> list[str]:
    """Return the Podman argv for a mocked ``subprocess.run`` invocation."""
    return run_mock.call_args_list[call_index].args[0]


def _mock_container(state: str | None = None, **method_overrides: object) -> Mock:
    """Return a Mock that quacks like a `Container` handle."""
    container = Mock()
    container.state = state
    container.running = state == "running"
    for method, value in method_overrides.items():
        getattr(container, method).return_value = value
    return container


@pytest.mark.parametrize(
    ("output", "error", "expected"),
    [
        pytest.param("running\n", None, "running", id="running"),
        pytest.param("exited\n", None, "exited", id="exited"),
        pytest.param(None, subprocess.CalledProcessError(1, "podman"), None, id="not-found"),
        pytest.param(None, FileNotFoundError("podman"), None, id="podman-missing"),
    ],
)
def test_container_state_handles_success_and_errors(
    output: str | None,
    error: Exception | None,
    expected: str | None,
) -> None:
    """Container state lookup lowercases successful output and ignores Podman errors."""
    patch_kwargs = {"side_effect": error} if error else {"return_value": output}
    with patch("terok_sandbox.runtime.podman.subprocess.check_output", **patch_kwargs):
        assert PodmanRuntime().container("test-container").state == expected


@pytest.mark.parametrize(
    ("project_id", "shutdown_timeout", "timeout_override", "expected_timeout"),
    [
        pytest.param("proj_stop", None, None, 10, id="default-timeout"),
        pytest.param("proj_stop_cfg", 30, None, 30, id="config-timeout"),
        pytest.param("proj_stop_ovr", 30, 60, 60, id="cli-timeout-override"),
    ],
)
def test_task_stop_uses_expected_timeout(
    project_id: str,
    shutdown_timeout: int | None,
    timeout_override: int | None,
    expected_timeout: int,
) -> None:
    """Stopping a task uses the default, configured, or explicit timeout."""
    with project_env(
        project_config(project_id, shutdown_timeout=shutdown_timeout),
        project_id=project_id,
    ) as ctx:
        task_id = create_task_with_mode(ctx, project_id)

        container = _mock_container(state="running")
        runtime_mock = Mock(spec=PodmanRuntime)
        runtime_mock.container.return_value = container
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.get_runtime", return_value=runtime_mock),
        ):
            capture_stdout(
                task_stop,
                project_id,
                task_id,
                **({"timeout": timeout_override} if timeout_override is not None else {}),
            )

        # One call for state lookup, one call for stop
        runtime_mock.container.assert_any_call(f"{project_id}-cli-{task_id}")
        container.stop.assert_called_once_with(timeout=expected_timeout)


def test_task_stop_unknown_task_raises_system_exit() -> None:
    """Stopping a missing task raises a user-facing ``SystemExit``."""
    project_id = "proj_stop_missing"
    with project_env(project_config(project_id), project_id=project_id):
        with mock_git_config(), pytest.raises(SystemExit, match="Unknown task"):
            task_stop(project_id, "999")


def test_task_restart_starts_exited_container() -> None:
    """Restarting an exited task uses ``Container.start``."""
    project_id = "proj_restart"
    with project_env(project_config(project_id), project_id=project_id) as ctx:
        task_id = create_task_with_mode(ctx, project_id)
        container_name = f"{project_id}-cli-{task_id}"

        # First state query → "exited"; subsequent queries (after start) → "running"
        container_states = iter(["exited", "running"])
        cache: dict[str, Mock] = {}

        def make_container(name: str) -> Mock:
            """Return the cached Mock for *name*, updating .state per the schedule."""
            c = cache.get(name)
            if c is None:
                c = Mock()
                c.login_command.return_value = ["podman", "exec", "-it", name, "bash"]
                cache[name] = c
            try:
                c.state = next(container_states)
            except StopIteration:
                c.state = "running"
            c.running = c.state == "running"
            return c

        runtime_mock = Mock(spec=PodmanRuntime)
        runtime_mock.container.side_effect = make_container
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.get_runtime", return_value=runtime_mock),
        ):
            capture_stdout(task_restart, project_id, task_id)

        runtime_mock.container.assert_any_call(container_name)
        assert container_name in cache, "runtime.container should have been queried for the task"
        cache[container_name].start.assert_called_once()


def test_task_restart_running_container_stops_then_starts() -> None:
    """Restarting a running task stops it first and then starts it again."""
    project_id = "proj_restart_running"
    with project_env(project_config(project_id), project_id=project_id) as ctx:
        task_id = create_task_with_mode(ctx, project_id)
        container_name = f"{project_id}-cli-{task_id}"

        # Every state query returns "running"
        shared_container = _mock_container(state="running")
        shared_container.login_command.return_value = [
            "podman",
            "exec",
            "-it",
            container_name,
            "bash",
        ]
        runtime_mock = Mock(spec=PodmanRuntime)
        runtime_mock.container.return_value = shared_container
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.get_runtime", return_value=runtime_mock),
        ):
            output = capture_stdout(task_restart, project_id, task_id)

        runtime_mock.container.assert_any_call(container_name)
        shared_container.stop.assert_called_once_with(timeout=10)
        shared_container.start.assert_called_once_with()
        assert "Restarted" in output


def test_task_status_reports_live_container_state() -> None:
    """Task status shows both live container state and derived effective status."""
    project_id = "proj_status"
    with project_env(project_config(project_id), project_id=project_id) as ctx:
        task_id = create_task_with_mode(ctx, project_id)

        runtime_mock = Mock(spec=PodmanRuntime)
        runtime_mock.container.return_value = _mock_container(state="exited")
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.get_runtime", return_value=runtime_mock),
        ):
            output = capture_stdout(task_status, project_id, task_id)

    assert "exited" in output
    assert "stopped" in output


def test_get_task_container_state_returns_none_without_mode() -> None:
    """Task container lookup is skipped when no mode is configured."""
    assert get_task_container_state("proj", "1", None) is None


def test_get_task_container_state_uses_project_id_and_mode() -> None:
    """Task container lookup resolves the canonical container name."""
    runtime_mock = Mock(spec=PodmanRuntime)
    runtime_mock.container.return_value = _mock_container(state="running")
    with patch("terok.lib.core.runtime.get_runtime", return_value=runtime_mock):
        assert get_task_container_state("proj", "1", "cli") == "running"
        runtime_mock.container.assert_called_once_with("proj-cli-1")


def test_task_restart_no_mode_raises() -> None:
    """Restarting a task that never ran (no mode set) raises a user-facing SystemExit."""
    project_id = "proj_restart_nomode"
    with project_env(project_config(project_id), project_id=project_id):
        with mock_git_config():
            task_id = task_new(project_id)  # fresh task — mode is None
            with pytest.raises(SystemExit, match="never been run"):
                task_restart(project_id, task_id)


def test_task_restart_missing_container_raises() -> None:
    """Restarting a task whose container is gone raises, pointing at ``task run``."""
    project_id = "proj_restart_gone"
    with project_env(project_config(project_id), project_id=project_id) as ctx:
        task_id = create_task_with_mode(ctx, project_id)

        runtime_mock = Mock(spec=PodmanRuntime)
        runtime_mock.container.return_value = _mock_container(state=None)
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.get_runtime", return_value=runtime_mock),
        ):
            with pytest.raises(SystemExit, match="no longer exists"):
                task_restart(project_id, task_id)


def test_task_restart_stop_failure_raises() -> None:
    """A RuntimeError from Container.stop surfaces as a user-facing SystemExit."""
    project_id = "proj_restart_stopfail"
    with project_env(project_config(project_id), project_id=project_id) as ctx:
        task_id = create_task_with_mode(ctx, project_id)

        container = _mock_container(state="running")
        container.stop.side_effect = RuntimeError("container locked")
        runtime_mock = Mock(spec=PodmanRuntime)
        runtime_mock.container.return_value = container
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.get_runtime", return_value=runtime_mock),
        ):
            with pytest.raises(SystemExit, match="Failed to stop container"):
                task_restart(project_id, task_id)


def test_task_restart_port_unavailable_aborts_before_stopping() -> None:
    """A toad task whose saved web_port is now taken aborts *before* the container is stopped.

    Pins the "validate preconditions before stopping a healthy container"
    safety property: a restart that would fail anyway must not first take
    down a working service.
    """
    project_id = "proj_restart_port"
    with project_env(project_config(project_id), project_id=project_id) as ctx:
        task_id = create_task_with_mode(ctx, project_id, mode="toad")
        update_task_meta(ctx, project_id, task_id, web_port=8080, web_token="tok")

        container = _mock_container(state="running")
        runtime_mock = Mock(spec=PodmanRuntime)
        runtime_mock.container.return_value = container
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.get_runtime", return_value=runtime_mock),
            patch(
                "terok.lib.orchestration.task_runners.restart.assign_web_port",
                return_value=9999,  # != saved 8080 → port no longer available
            ),
            patch("terok.lib.orchestration.task_runners.restart.release_web_port") as release,
        ):
            with pytest.raises(SystemExit, match="no longer available"):
                task_restart(project_id, task_id)

        release.assert_called_once_with(project_id, task_id)
        container.stop.assert_not_called()


def test_task_restart_toad_rehydrates_token_and_prints_url() -> None:
    """Restarting a running toad task rehydrates its token and prints the browser URL."""
    project_id = "proj_restart_toad"
    with project_env(project_config(project_id), project_id=project_id) as ctx:
        task_id = create_task_with_mode(ctx, project_id, mode="toad")
        update_task_meta(ctx, project_id, task_id, web_port=8080, web_token="sekret")

        container = _mock_container(state="running")
        runtime_mock = Mock(spec=PodmanRuntime)
        runtime_mock.container.return_value = container
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.get_runtime", return_value=runtime_mock),
            patch(
                "terok.lib.orchestration.task_runners.restart.assign_web_port",
                return_value=8080,  # same port → available
            ),
            patch(
                "terok.lib.orchestration.task_runners.restart._rehydrate_toad_token"
            ) as rehydrate,
        ):
            output = capture_stdout(task_restart, project_id, task_id)

        rehydrate.assert_called_once()
        container.stop.assert_called_once()
        container.start.assert_called_once()
        assert "Toad:" in output
        assert "8080" in output
