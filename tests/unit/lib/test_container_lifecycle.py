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

from terok.lib.orchestration.task_runners import ensure_task_running, task_restart
from terok.lib.orchestration.tasks import (
    get_task_container_state,
    read_task_meta,
    task_new,
    task_status,
    task_stop,
    write_task_meta,
)
from tests.test_utils import mock_git_config, project_env


def project_config(project_name: str, *, shutdown_timeout: int | None = None) -> str:
    """Build a minimal project config, optionally overriding shutdown timeout."""
    lines = [f"project:\n  id: {project_name}"]
    if shutdown_timeout is not None:
        lines.append(f"run:\n  shutdown_timeout: {shutdown_timeout}")
    return "\n".join(lines) + "\n"


def task_meta_path(ctx: SimpleNamespace, project_name: str, task_id: str) -> Path:
    """Return the metadata path for *task_id* inside the temporary project env."""
    return ctx.state_dir / "projects" / project_name / "tasks" / f"{task_id}_dossier.json"


def update_task_meta(
    ctx: SimpleNamespace, project_name: str, task_id: str, **changes: object
) -> None:
    """Patch selected metadata keys for a generated task."""
    dossier_handle = task_meta_path(ctx, project_name, task_id)
    meta = read_task_meta(dossier_handle.parent, task_id) or {}
    meta.update(changes)
    write_task_meta(dossier_handle, meta)


def create_task_with_mode(ctx: SimpleNamespace, project_name: str, *, mode: str = "cli") -> str:
    """Create a new task and persist the requested mode in its metadata."""
    task_id = task_new(project_name)
    update_task_meta(ctx, project_name, task_id, mode=mode)
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


def _mock_runtime(container: Mock, *, image_id: str = "img-current") -> Mock:
    """Runtime mock whose container and project image share *image_id*.

    Restart's image-drift probe compares the two IDs; auto-created Mock
    attributes would never compare equal, so every restart test must
    model image identity explicitly (diverge it to exercise the
    recreate rung).
    """
    image = Mock()
    image.id = image_id
    container.image = image
    runtime = Mock(spec=PodmanRuntime)
    runtime.container.return_value = container
    runtime.image.return_value = image
    return runtime


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
    ("project_name", "shutdown_timeout", "timeout_override", "expected_timeout"),
    [
        pytest.param("proj_stop", None, None, 10, id="default-timeout"),
        pytest.param("proj_stop_cfg", 30, None, 30, id="config-timeout"),
        pytest.param("proj_stop_ovr", 30, 60, 60, id="cli-timeout-override"),
    ],
)
def test_task_stop_uses_expected_timeout(
    project_name: str,
    shutdown_timeout: int | None,
    timeout_override: int | None,
    expected_timeout: int,
) -> None:
    """Stopping a task uses the default, configured, or explicit timeout."""
    with project_env(
        project_config(project_name, shutdown_timeout=shutdown_timeout),
        project_name=project_name,
    ) as ctx:
        task_id = create_task_with_mode(ctx, project_name)

        container = _mock_container(state="running")
        runtime_mock = Mock(spec=PodmanRuntime)
        runtime_mock.container.return_value = container
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
        ):
            capture_stdout(
                task_stop,
                project_name,
                task_id,
                **({"timeout": timeout_override} if timeout_override is not None else {}),
            )

        # One call for state lookup, one call for stop
        runtime_mock.container.assert_any_call(f"{project_name}-cli-{task_id}")
        container.stop.assert_called_once_with(timeout=expected_timeout)


def test_task_stop_unknown_task_raises_system_exit() -> None:
    """Stopping a missing task raises a user-facing ``SystemExit``."""
    project_name = "proj_stop_missing"
    with project_env(project_config(project_name), project_name=project_name):
        with mock_git_config(), pytest.raises(SystemExit, match="Unknown task"):
            task_stop(project_name, "999")


def test_task_restart_starts_exited_container() -> None:
    """Restarting an exited task uses ``Container.start``."""
    project_name = "proj_restart"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name)
        container_name = f"{project_name}-cli-{task_id}"

        # Exited until start is called, then running.
        container = _mock_container(state="exited")
        container.login_command.return_value = ["podman", "exec", "-it", container_name, "bash"]
        container.start.side_effect = lambda: setattr(container, "state", "running")
        runtime_mock = _mock_runtime(container)
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
        ):
            capture_stdout(task_restart, project_name, task_id)

        runtime_mock.container.assert_any_call(container_name)
        container.start.assert_called_once()
        container.stop.assert_not_called()


def test_task_restart_running_container_stops_then_starts() -> None:
    """Restarting a running task stops it first and then starts it again."""
    project_name = "proj_restart_running"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name)
        container_name = f"{project_name}-cli-{task_id}"

        # Every state query returns "running"
        shared_container = _mock_container(state="running")
        shared_container.login_command.return_value = [
            "podman",
            "exec",
            "-it",
            container_name,
            "bash",
        ]
        runtime_mock = _mock_runtime(shared_container)
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
        ):
            output = capture_stdout(task_restart, project_name, task_id)

        runtime_mock.container.assert_any_call(container_name)
        shared_container.stop.assert_called_once_with(timeout=10)
        shared_container.start.assert_called_once_with()
        assert "Restarted" in output


def test_task_status_reports_live_container_state() -> None:
    """Task status shows both live container state and derived effective status."""
    project_name = "proj_status"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name)

        runtime_mock = Mock(spec=PodmanRuntime)
        runtime_mock.container.return_value = _mock_container(state="exited")
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
        ):
            output = capture_stdout(task_status, project_name, task_id)

    assert "exited" in output
    assert "stopped" in output


def test_task_status_verbose_shows_debug_locations() -> None:
    """``status --verbose`` appends container ID, mounts, and supervisor paths."""
    project_name = "proj_status_v"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name)

        container = _mock_container(state="exited")
        container.id = "abc123def456"
        container.mounts = [("/host/work", "/workspace")]
        runtime_mock = Mock(spec=PodmanRuntime)
        runtime_mock.container.return_value = container
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
        ):
            output = capture_stdout(task_status, project_name, task_id, verbose=True)

    assert "Debug locations" in output
    assert "abc123def456" in output  # full container ID
    assert "/host/work → /workspace" in output  # mount projection
    assert f"supervisor-{'abc123def456'}.pid" in output  # PID file keyed on ID
    assert f"terok task logs {project_name} {task_id}" in output  # the logs hint


def test_task_status_verbose_handles_removed_container() -> None:
    """With no live container ID, ID-keyed paths degrade but name-keyed ones resolve."""
    project_name = "proj_status_gone"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name)

        container = _mock_container(state=None)
        container.id = None  # container removed — no ID to inspect
        container.mounts = []
        runtime_mock = Mock(spec=PodmanRuntime)
        runtime_mock.container.return_value = container
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
        ):
            output = capture_stdout(task_status, project_name, task_id, verbose=True)

    assert "container removed" in output
    assert "Sidecar:" in output  # name-keyed → still resolvable
    assert "needs a live or exited container" in output  # ID-keyed log unavailable


def test_get_task_container_state_returns_none_without_mode() -> None:
    """Task container lookup is skipped when no mode is configured."""
    assert get_task_container_state("proj", "1", None) is None


def test_get_task_container_state_uses_project_name_and_mode() -> None:
    """Task container lookup resolves the canonical container name.

    State queries are runtime-agnostic — ``podman inspect`` is the same
    under crun and krun — so the helper short-cuts through
    ``PodmanRuntime`` directly rather than the per-project resolver
    (which would force a ``load_project`` for every one-shot probe).
    """
    runtime_mock = Mock(spec=PodmanRuntime)
    runtime_mock.container.return_value = _mock_container(state="running")
    with patch("terok.lib.integrations.sandbox.PodmanRuntime", return_value=runtime_mock):
        assert get_task_container_state("proj", "1", "cli") == "running"
        runtime_mock.container.assert_called_once_with("proj-cli-1")


def test_task_restart_no_mode_raises() -> None:
    """Restarting a task that never ran (no mode set) raises a user-facing SystemExit."""
    project_name = "proj_restart_nomode"
    with project_env(project_config(project_name), project_name=project_name):
        with mock_git_config():
            task_id = task_new(project_name)  # fresh task — mode is None
            with pytest.raises(SystemExit, match="never been run"):
                task_restart(project_name, task_id)


def test_task_restart_missing_headless_container_raises() -> None:
    """A gone headless container is never recreated — that would replay its prompt."""
    project_name = "proj_restart_gone"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name, mode="run")

        runtime_mock = _mock_runtime(_mock_container(state=None))
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
        ):
            with pytest.raises(SystemExit, match="never recreated"):
                task_restart(project_name, task_id)


def test_task_restart_missing_container_recreates_in_place() -> None:
    """A gone cli container takes the recreate rung: rm-if-present + relaunch."""
    project_name = "proj_restart_recreate"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name)
        update_task_meta(ctx, project_name, task_id, unrestricted=False)

        runtime_mock = _mock_runtime(_mock_container(state=None))
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
            patch("terok.lib.orchestration.task_runners.restart._sandbox") as sandbox,
            patch("terok.lib.orchestration.task_runners.restart.task_run_cli") as run_cli,
        ):
            output = capture_stdout(task_restart, project_name, task_id)

        assert "Recreating" in output
        sandbox.return_value.rm.assert_not_called()  # nothing to remove
        run_cli.assert_called_once_with(project_name, task_id, unrestricted=False)


def test_task_restart_missing_toad_container_recreates_via_toad_runner() -> None:
    """A gone toad container takes the recreate rung through the toad runner."""
    project_name = "proj_restart_toad_gone"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name, mode="toad")
        update_task_meta(ctx, project_name, task_id, web_port=8080, web_token="tok")

        runtime_mock = _mock_runtime(_mock_container(state=None))
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
            patch("terok.lib.orchestration.task_runners.restart._sandbox"),
            patch("terok.lib.orchestration.task_runners.restart.assign_web_port"),
            patch("terok.lib.orchestration.task_runners.restart.task_run_toad") as run_toad,
            patch("terok.lib.orchestration.task_runners.restart.task_run_cli") as run_cli,
        ):
            output = capture_stdout(task_restart, project_name, task_id)

        assert "Recreating" in output
        run_toad.assert_called_once_with(project_name, task_id, unrestricted=None)
        run_cli.assert_not_called()


def test_ensure_task_running_launches_missing_container() -> None:
    """The ensure ladder (attach) launches a task whose container is gone."""
    project_name = "proj_ensure_launch"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name)

        runtime_mock = _mock_runtime(_mock_container(state=None))
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
            patch("terok.lib.orchestration.task_runners.restart.task_run_cli") as run_cli,
        ):
            capture_stdout(ensure_task_running, project_name, task_id, unrestricted=True)

        run_cli.assert_called_once_with(project_name, task_id, unrestricted=True)


def test_task_restart_fresh_skips_resume_and_recreates() -> None:
    """``--recreate`` (fresh) tears a healthy running container down and relaunches it."""
    project_name = "proj_restart_fresh"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name)
        container_name = f"{project_name}-cli-{task_id}"

        container = _mock_container(state="running")
        runtime_mock = _mock_runtime(container)
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
            patch("terok.lib.orchestration.task_runners.restart._sandbox") as sandbox,
            patch("terok.lib.orchestration.task_runners.restart.task_run_cli") as run_cli,
        ):
            output = capture_stdout(task_restart, project_name, task_id, fresh=True)

        assert "Recreating" in output
        container.stop.assert_called_once_with(timeout=10)
        container.start.assert_not_called()
        sandbox.return_value.rm.assert_called_once_with([container_name])
        run_cli.assert_called_once()


def test_task_restart_image_drift_warns_and_resumes() -> None:
    """A container on a superseded image is resumed as-is, with a stale-image warning.

    A plain restart keeps a long-running task's container rather than
    upgrading it: it starts the existing container and only warns that
    the image is out of date, pointing at recreate + restart.
    """
    project_name = "proj_restart_drift"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name)

        container_name = f"{project_name}-cli-{task_id}"
        container = _mock_container(state="exited")
        container.login_command.return_value = ["podman", "exec", "-it", container_name, "bash"]
        container.start.side_effect = lambda: setattr(container, "state", "running")
        runtime_mock = _mock_runtime(container)
        rebuilt = Mock()
        rebuilt.id = "img-rebuilt"
        runtime_mock.image.return_value = rebuilt
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
            patch("terok.lib.orchestration.task_runners.restart._sandbox") as sandbox,
            patch("terok.lib.orchestration.task_runners.restart.task_run_cli") as run_cli,
        ):
            output = capture_stdout(task_restart, project_name, task_id)

        assert "OUTDATED image" in output
        assert "rebuilt" in output
        assert "--recreate" in output
        container.start.assert_called_once()
        sandbox.return_value.rm.assert_not_called()
        run_cli.assert_not_called()
        assert "Restarted" in output


def test_task_restart_recreate_on_image_drift_upgrades() -> None:
    """``--recreate`` (fresh) tears the container down and relaunches on the new image."""
    project_name = "proj_restart_drift_recreate"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name)
        container_name = f"{project_name}-cli-{task_id}"

        container = _mock_container(state="exited")
        runtime_mock = _mock_runtime(container)
        rebuilt = Mock()
        rebuilt.id = "img-rebuilt"
        runtime_mock.image.return_value = rebuilt
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
            patch("terok.lib.orchestration.task_runners.restart._sandbox") as sandbox,
            patch("terok.lib.orchestration.task_runners.restart.task_run_cli") as run_cli,
        ):
            output = capture_stdout(task_restart, project_name, task_id, fresh=True)

        assert "Recreating" in output
        container.start.assert_not_called()
        sandbox.return_value.rm.assert_called_once_with([container_name])
        run_cli.assert_called_once()


def test_task_restart_headless_start_failure_never_destroys() -> None:
    """A headless container that refuses to start is refused, not torn down.

    The resume-failure fallback must hit the headless refusal *before*
    any teardown — otherwise the container would be removed with nothing
    relaunched into its place (a headless task has no recreate rung).
    """
    project_name = "proj_restart_run_fallback"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name, mode="run")

        container = _mock_container(state="exited")
        container.start.side_effect = RuntimeError("layer missing")
        runtime_mock = _mock_runtime(container)
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
            patch("terok.lib.orchestration.task_runners.restart._sandbox") as sandbox,
        ):
            with pytest.raises(SystemExit, match="never recreated"):
                task_restart(project_name, task_id)

        sandbox.return_value.rm.assert_not_called()


def test_task_restart_headless_ignores_image_drift() -> None:
    """Headless tasks resume on their old image — drift has no recreate rung to take."""
    project_name = "proj_restart_run_drift"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name, mode="run")

        container = _mock_container(state="exited")
        container.start.side_effect = lambda: setattr(container, "state", "running")
        runtime_mock = _mock_runtime(container)
        rebuilt = Mock()
        rebuilt.id = "img-rebuilt"
        runtime_mock.image.return_value = rebuilt
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
        ):
            output = capture_stdout(task_restart, project_name, task_id)

        container.start.assert_called_once()
        assert "Restarted" in output


def test_task_restart_start_failure_falls_back_to_recreate() -> None:
    """When podman refuses to start the container, the ladder recreates instead."""
    project_name = "proj_restart_fallback"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name)

        container = _mock_container(state="exited")
        container.start.side_effect = RuntimeError("layer missing")
        runtime_mock = _mock_runtime(container)
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
            patch("terok.lib.orchestration.task_runners.restart._sandbox") as sandbox,
            patch("terok.lib.orchestration.task_runners.restart.task_run_cli") as run_cli,
        ):
            output = capture_stdout(task_restart, project_name, task_id)

        assert "Recreating" in output
        sandbox.return_value.rm.assert_called_once()
        run_cli.assert_called_once()


def test_task_restart_stop_failure_raises() -> None:
    """A RuntimeError from Container.stop surfaces as a user-facing SystemExit."""
    project_name = "proj_restart_stopfail"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name)

        container = _mock_container(state="running")
        container.stop.side_effect = RuntimeError("container locked")
        runtime_mock = _mock_runtime(container)
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
        ):
            with pytest.raises(SystemExit, match="Failed to stop container"):
                task_restart(project_name, task_id)


def test_task_restart_port_unavailable_aborts_before_stopping() -> None:
    """A toad task whose saved web_port is now taken aborts *before* the container is stopped.

    Pins the "validate preconditions before stopping a healthy container"
    safety property: a restart that would fail anyway must not first take
    down a working service.
    """
    project_name = "proj_restart_port"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name, mode="toad")
        update_task_meta(ctx, project_name, task_id, web_port=8080, web_token="tok")

        container = _mock_container(state="running")
        runtime_mock = _mock_runtime(container)
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
            patch(
                "terok.lib.orchestration.task_runners.restart.assign_web_port",
                return_value=9999,  # != saved 8080 → port no longer available
            ),
            patch("terok.lib.orchestration.task_runners.restart.release_web_port") as release,
        ):
            with pytest.raises(SystemExit, match="no longer available"):
                task_restart(project_name, task_id)

        release.assert_called_once_with(project_name, task_id)
        container.stop.assert_not_called()


def test_task_restart_toad_rehydrates_token_and_prints_url() -> None:
    """Restarting a running toad task rehydrates its token and prints the browser URL."""
    project_name = "proj_restart_toad"
    with project_env(project_config(project_name), project_name=project_name) as ctx:
        task_id = create_task_with_mode(ctx, project_name, mode="toad")
        update_task_meta(ctx, project_name, task_id, web_port=8080, web_token="sekret")

        container = _mock_container(state="running")
        runtime_mock = _mock_runtime(container)
        with (
            mock_git_config(),
            patch("terok.lib.core.runtime.resolve_runtime", return_value=runtime_mock),
            patch(
                "terok.lib.orchestration.task_runners.restart.assign_web_port",
                return_value=8080,  # same port → available
            ),
            patch(
                "terok.lib.orchestration.task_runners.restart._rehydrate_toad_token"
            ) as rehydrate,
        ):
            output = capture_stdout(task_restart, project_name, task_id)

        rehydrate.assert_called_once()
        container.stop.assert_called_once()
        container.start.assert_called_once()
        assert "Toad:" in output
        assert "8080" in output
