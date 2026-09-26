# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Setup faults leave existing tasks and restart state untouched."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from terok_util import SetupRequiredError

from terok.lib.orchestration.task_runners import cli, container, headless, restart, toad


@pytest.mark.parametrize(
    ("module", "entry", "args", "kwargs"),
    [
        (cli, "task_run_cli", ("project", "1"), {}),
        (toad, "task_run_toad", ("project", "1"), {}),
        (headless, "task_run_headless", (SimpleNamespace(project_name="project"),), {}),
        (headless, "task_followup_headless", ("project", "1", "new prompt"), {}),
        (restart, "task_restart", ("project", "1"), {}),
        (restart, "task_restart", ("project", "1"), {"fresh": True}),
        (restart, "ensure_task_running", ("project", "1"), {}),
    ],
)
def test_setup_failure_precedes_task_mutation(module, entry, args, kwargs) -> None:
    """Every managed launch refuses before metadata, prompts, ports, or containers change."""
    fault = SetupRequiredError("Bootstrap interpreter is missing; run setup")
    with (
        patch.object(module, "load_project", return_value=SimpleNamespace(name="project")),
        patch.object(module, "validate_host_setup", side_effect=fault) as validate,
        patch.object(module, "load_task_meta") as metadata,
        patch.object(module._rt, "resolve_runtime") as runtime,
        pytest.raises(SetupRequiredError) as raised,
    ):
        getattr(module, entry)(*args, **kwargs)
    assert raised.value is fault
    validate.assert_called_once_with()
    metadata.assert_not_called()
    runtime.assert_not_called()


def test_start_preserves_typed_setup_failure() -> None:
    """The start adapter must not turn a setup fault into a recreatable SystemExit."""
    fault = SetupRequiredError("Host nft is missing from PATH")
    with (
        patch.object(container, "_sandbox") as sandbox,
        pytest.raises(SetupRequiredError) as raised,
    ):
        sandbox.return_value.start.side_effect = fault
        container._podman_start(Mock(), "task")
    assert raised.value is fault


def test_setup_failure_during_start_never_recreates(tmp_path) -> None:
    """A late setup fault passes through the restart ladder without deleting the task."""
    project = SimpleNamespace(name="project", tasks_root=tmp_path)
    fault = SetupRequiredError("Setup changed after preflight")
    with (
        patch.object(restart, "load_project", return_value=project),
        patch.object(restart, "load_task_meta", return_value=({"mode": "cli"}, tmp_path)),
        patch.object(restart._rt, "resolve_runtime") as runtime,
        patch.object(restart, "_validate_restart_preconditions"),
        patch.object(restart, "_validate_shield_bundle_version"),
        patch.object(restart, "_refresh_shield_tiers"),
        patch.object(restart, "_start_and_report_restart", side_effect=fault),
        patch.object(restart, "_recreate_in_place") as recreate,
        pytest.raises(SetupRequiredError) as raised,
    ):
        runtime.return_value.container.return_value.state = "exited"
        restart.task_restart("project", "1")
    assert raised.value is fault
    recreate.assert_not_called()
