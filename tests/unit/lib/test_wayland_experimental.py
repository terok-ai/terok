# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Desktop access requires today's experimental opt-in before launch or restart."""

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from terok.lib.core import config
from terok.lib.integrations.sandbox import WaylandMode
from terok.lib.orchestration.task_runners import container, headless, restart


@pytest.fixture(autouse=True)
def experimental_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the process-level opt-in independent of other CLI tests."""
    monkeypatch.setattr(config, "_experimental", False)


@pytest.mark.parametrize("mode", [WaylandMode.DIRECT, WaylandMode.MITM])
def test_desktop_modes_require_explicit_opt_in(mode: WaylandMode) -> None:
    """Both desktop modes explain the existing config and command-line remedies."""
    with pytest.raises(SystemExit, match="requires the experimental flag") as raised:
        container._require_wayland_experimental(mode)

    message = str(raised.value)
    assert mode in message
    assert "experimental: true" in message
    assert "--experimental" in message


def test_no_desktop_does_not_require_experimental() -> None:
    """The default remains usable without opting into experimental features."""
    container._require_wayland_experimental(WaylandMode.NONE)


@pytest.mark.parametrize("mode", [WaylandMode.DIRECT, WaylandMode.MITM])
@pytest.mark.parametrize("source", ["config", "cli"])
def test_existing_experimental_sources_authorize_desktop(
    mode: WaylandMode, source: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI opt-in overrides a false config; a true config needs no CLI flag."""
    config_file = tmp_path / "config.yml"
    config_file.write_text(f"experimental: {str(source == 'config').lower()}\n")
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(config_file))
    config.set_experimental(source == "cli")

    container._require_wayland_experimental(mode)


@pytest.mark.parametrize("mode", [WaylandMode.DIRECT, WaylandMode.MITM])
def test_new_launch_refuses_before_allocating_runner(mode: WaylandMode, tmp_path: Path) -> None:
    """The common launch path gates CLI, Toad and headless container creation."""
    with (
        patch.object(container, "_agent_runner") as runner,
        pytest.raises(SystemExit, match="requires the experimental flag"),
    ):
        container._run_container(
            cname="wayland-cli-1",
            image="test-image",
            env={},
            volumes=[],
            project=SimpleNamespace(wayland=mode),
            task_id="1",
            task_dir=tmp_path,
            broker_context=MagicMock(),
        )

    runner.assert_not_called()


@pytest.fixture
def retained_task(tmp_path: Path, mock_runtime: MagicMock) -> Iterator[SimpleNamespace]:
    """Retain a valid task while mocking host mutations and the parsed sidecar."""
    project = SimpleNamespace(name="wayland", wayland=WaylandMode.NONE, tasks_root=tmp_path)
    meta = {"mode": "cli"}
    sidecar = SimpleNamespace(wayland=SimpleNamespace(mode=WaylandMode.NONE))
    sidecar_path = tmp_path / "sidecar.json"
    sidecar_path.touch()
    mock_runtime.container.return_value.state = "exited"
    with (
        patch.object(restart, "load_project", return_value=project),
        patch.object(restart, "load_task_meta", return_value=(meta, tmp_path / "dossier.json")),
        patch.object(restart, "make_sandbox_config") as sandbox_config,
        patch.object(restart, "load_sidecar", return_value=sidecar) as load_sidecar,
        patch.object(restart, "_validate_restart_preconditions") as preconditions,
        patch.object(restart, "_validate_shield_bundle_version"),
        patch.object(restart, "_refresh_shield_tiers") as refresh,
        patch.object(restart, "_refresh_clearance_mode"),
        patch.object(restart, "_stop_running_container") as stop,
        patch.object(restart, "_start_and_report_restart") as start,
        patch.object(restart, "_recreate_in_place") as recreate,
    ):
        sandbox_config.return_value.sidecar_path.return_value = sidecar_path
        yield SimpleNamespace(
            project=project,
            meta=meta,
            sidecar=sidecar,
            load_sidecar=load_sidecar,
            container=mock_runtime.container.return_value,
            preconditions=preconditions,
            refresh=refresh,
            stop=stop,
            start=start,
            recreate=recreate,
        )


@pytest.mark.parametrize("mode", [WaylandMode.DIRECT, WaylandMode.MITM])
@pytest.mark.parametrize("state", ["running", "exited"])
@pytest.mark.parametrize("task_mode", ["cli", "toad"])
def test_plain_restart_checks_saved_mode_before_stop_or_fallback(
    retained_task: SimpleNamespace, mode: WaylandMode, state: str, task_mode: str
) -> None:
    """Changing project Wayland to none cannot bypass an existing desktop grant."""
    task = retained_task
    task.sidecar.wayland.mode = mode
    task.container.state = state
    task.meta["mode"] = task_mode

    with pytest.raises(SystemExit, match="requires the experimental flag"):
        restart.task_restart(task.project.name, "1")

    task.preconditions.assert_not_called()
    task.refresh.assert_not_called()
    task.stop.assert_not_called()
    task.start.assert_not_called()
    task.recreate.assert_not_called()


def test_invalid_sidecar_refuses_before_mutating_retained_task(
    retained_task: SimpleNamespace,
) -> None:
    """Unrelated invalid sidecar fields cannot hide a saved desktop grant."""
    task = retained_task
    task.container.state = "running"
    task.load_sidecar.return_value = None

    with pytest.raises(SystemExit):
        restart.task_restart(task.project.name, "1")

    task.preconditions.assert_not_called()
    task.refresh.assert_not_called()
    task.stop.assert_not_called()
    task.start.assert_not_called()
    task.recreate.assert_not_called()


@pytest.mark.parametrize(
    ("mode", "experimental"),
    [(WaylandMode.NONE, False), (WaylandMode.DIRECT, True), (WaylandMode.MITM, True)],
)
def test_authorized_restart_preserves_saved_mode(
    retained_task: SimpleNamespace, mode: WaylandMode, experimental: bool
) -> None:
    """Opt-in permits saved grants; ordinary tasks need no opt-in to resume."""
    task = retained_task
    task.sidecar.wayland.mode = mode
    config.set_experimental(experimental)

    restart.task_restart(task.project.name, "1")

    task.start.assert_called_once()
    task.recreate.assert_not_called()
    assert task.sidecar.wayland.mode == mode


@pytest.mark.parametrize("mode", [WaylandMode.DIRECT, WaylandMode.MITM])
def test_recreation_checks_new_mode_before_teardown(
    retained_task: SimpleNamespace, mode: WaylandMode
) -> None:
    """Explicit recreation must not destroy a task before rejecting its replacement."""
    task = retained_task
    task.project.wayland = mode
    task.container.state = "running"

    with pytest.raises(SystemExit, match="requires the experimental flag"):
        restart.task_restart(task.project.name, "1", fresh=True)

    task.stop.assert_not_called()
    task.start.assert_not_called()
    task.recreate.assert_not_called()


@pytest.mark.parametrize("mode", [WaylandMode.DIRECT, WaylandMode.MITM])
def test_failed_resume_cannot_fall_back_to_experimental_replacement(
    retained_task: SimpleNamespace, mode: WaylandMode
) -> None:
    """An ordinary retained task cannot acquire desktop access through fallback."""
    task = retained_task
    task.project.wayland = mode
    task.start.side_effect = SystemExit("start failed")

    with pytest.raises(SystemExit, match="requires the experimental flag"):
        restart.task_restart(task.project.name, "1")

    task.start.assert_called_once()
    task.stop.assert_not_called()
    task.recreate.assert_not_called()


def test_recreation_can_remove_saved_desktop_without_experimental(
    retained_task: SimpleNamespace,
) -> None:
    """An explicit recreation to none remains the way to remove the retained grant."""
    task = retained_task
    task.sidecar.wayland.mode = WaylandMode.DIRECT

    restart.task_restart(task.project.name, "1", fresh=True)

    task.load_sidecar.assert_not_called()
    task.recreate.assert_called_once()


@pytest.mark.parametrize("mode", [WaylandMode.DIRECT, WaylandMode.MITM])
def test_followup_refuses_before_prompt_injection(
    retained_task: SimpleNamespace, mode: WaylandMode, tmp_path: Path
) -> None:
    """A headless follow-up preserves its prior prompt when saved access is gated."""
    task = retained_task
    task.meta["mode"] = "run"
    task.sidecar.wayland.mode = mode
    with (
        patch.object(headless, "load_project", return_value=task.project),
        patch.object(
            headless, "load_task_meta", return_value=(task.meta, tmp_path / "dossier.json")
        ),
        patch.object(headless, "_inject_followup_prompt") as inject,
        patch.object(headless, "_podman_start") as start,
        pytest.raises(SystemExit, match="requires the experimental flag"),
    ):
        headless.task_followup_headless(task.project.name, "1", "Continue", follow=False)

    inject.assert_not_called()
    start.assert_not_called()
    task.refresh.assert_not_called()
