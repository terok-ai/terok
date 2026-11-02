# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Retained task containers cannot resume the obsolete writable-input protocol."""

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from terok.lib.orchestration.task_runners import restart


@pytest.fixture
def upgrade_task(tmp_path: Path, mock_runtime: MagicMock) -> Iterator[SimpleNamespace]:
    """Isolate restart policy from launch, token and firewall implementation details."""
    project = SimpleNamespace(
        name="protocol-upgrade",
        tasks_root=tmp_path / "tasks",
        shutdown_timeout=10,
        hook_post_stop=None,
    )
    meta = {"mode": "cli", "unrestricted": False, "debug": True}
    with (
        patch.object(restart, "load_project", return_value=project),
        patch.object(restart, "validate_host_setup"),
        patch.object(restart, "load_task_meta", return_value=(meta, tmp_path / "dossier.json")),
        patch.object(restart, "_validate_restart_preconditions") as preconditions,
        patch.object(restart, "require_image_input_protocol", create=True) as image_protocol,
        patch.object(restart, "_print_reach_footer"),
        patch.object(restart, "run_hook"),
        patch.object(restart, "_sandbox") as sandbox,
        patch.object(restart, "task_run_cli") as cli,
        patch.object(restart, "task_run_toad") as toad,
    ):
        yield SimpleNamespace(
            project=project,
            meta=meta,
            container=mock_runtime.container.return_value,
            sandbox=sandbox.return_value,
            cli=cli,
            toad=toad,
            preconditions=preconditions,
            image_protocol=image_protocol,
        )


@pytest.mark.parametrize("entrypoint", [restart.ensure_task_running, restart.task_restart])
@pytest.mark.parametrize("mode", ["cli", "toad"])
@pytest.mark.parametrize("state", ["running", "exited"])
@pytest.mark.parametrize("obsolete", ["container-env", "image-label", "missing-image"])
def test_legacy_interactive_container_is_recreated(
    upgrade_task: SimpleNamespace, entrypoint, mode: str, state: str, obsolete: str
) -> None:
    """Both attach and restart upgrade legacy containers instead of resuming them."""
    task = upgrade_task
    task.meta["mode"] = mode
    task.container.state = state
    if obsolete == "container-env":
        task.container.env = {"TEROK_CONTAINER_PROTOCOL": "3"}
    elif obsolete == "image-label":
        task.container.image.labels.return_value = {}
    else:
        task.container.image = None

    entrypoint(task.project.name, "1")

    runner = task.cli if mode == "cli" else task.toad
    other = task.toad if mode == "cli" else task.cli
    runner.assert_called_once_with(task.project.name, "1", unrestricted=False, debug=True)
    other.assert_not_called()
    task.sandbox.rm.assert_called_once_with([f"{task.project.name}-{mode}-1"])
    task.container.start.assert_not_called()
    if state == "running":
        task.container.stop.assert_called_once_with(timeout=0)
    else:
        task.container.stop.assert_not_called()


@pytest.mark.parametrize("entrypoint", [restart.ensure_task_running, restart.task_restart])
@pytest.mark.parametrize("state", ["running", "exited"])
def test_legacy_headless_container_is_refused_without_replaying(
    upgrade_task: SimpleNamespace, entrypoint, state: str
) -> None:
    """A headless protocol upgrade neither resumes nor destroys the retained task."""
    task = upgrade_task
    task.meta["mode"] = "run"
    task.container.state = state
    task.container.env = {}

    with pytest.raises(SystemExit, match="obsolete input protocol.*never recreated"):
        entrypoint(task.project.name, "1")

    task.container.start.assert_not_called()
    task.container.stop.assert_not_called()
    task.sandbox.rm.assert_not_called()
    task.cli.assert_not_called()
    task.toad.assert_not_called()


def test_current_running_container_is_not_recreated(upgrade_task: SimpleNamespace) -> None:
    """The secure input protocol preserves the ordinary already-running fast path."""
    task = upgrade_task
    task.container.state = "running"

    restart.ensure_task_running(task.project.name, "1")

    task.container.start.assert_not_called()
    task.container.stop.assert_not_called()
    task.sandbox.rm.assert_not_called()
    task.cli.assert_not_called()
    task.toad.assert_not_called()


@pytest.mark.parametrize("entrypoint", [restart.ensure_task_running, restart.task_restart])
@pytest.mark.parametrize("mode", ["cli", "toad"])
def test_legacy_upgrade_requires_replacement_image_before_teardown(
    upgrade_task: SimpleNamespace, entrypoint, mode: str
) -> None:
    """Missing rebuilt images leave the retained container and start inputs untouched."""
    task = upgrade_task
    task.meta["mode"] = mode
    task.container.state = "running"
    task.container.env = {}
    task.image_protocol.side_effect = RuntimeError("Rebuild replacement image first")

    with pytest.raises(SystemExit, match="Rebuild replacement image"):
        entrypoint(task.project.name, "1")

    task.image_protocol.assert_called_once()
    task.preconditions.assert_not_called()
    task.container.start.assert_not_called()
    task.container.stop.assert_not_called()
    task.sandbox.rm.assert_not_called()
    task.cli.assert_not_called()
    task.toad.assert_not_called()


def test_resume_protocol_guard_runs_before_mutating_start_inputs(
    upgrade_task: SimpleNamespace,
) -> None:
    """The headless-follow-up guard refuses legacy inputs before any refresh."""
    task = upgrade_task
    task.container.env = {}
    with (
        patch.object(restart, "_validate_shield_bundle_version") as version,
        patch.object(restart, "_refresh_shield_tiers") as tiers,
        patch.object(restart, "_refresh_clearance_mode") as clearance,
        pytest.raises(SystemExit, match="obsolete input protocol") as error,
    ):
        restart._refresh_before_resume(task.project, "legacy", task.project.tasks_root / "1")

    assert isinstance(error.value.__cause__, RuntimeError)
    version.assert_not_called()
    tiers.assert_not_called()
    clearance.assert_not_called()
    task.container.start.assert_not_called()
