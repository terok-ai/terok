# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the [`Task`][terok.lib.domain.task.Task] aggregate.

These verify the entity's identity semantics and that every lifecycle /
observation method delegates to the orchestration layer threading the project
*name* (the slug) and task id — the contract that the project_id→project_name
rename had to preserve.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from terok.lib.domain.task import Task

_PROJ = "demoproj"
_TID = "swift-otter"


def _task(*, mode: str | None = "cli", **meta_overrides: object) -> Task:
    """Build a Task over lightweight config/meta stand-ins."""
    config = SimpleNamespace(name=_PROJ)
    meta_fields: dict[str, object] = {
        "task_id": _TID,
        "name": "my-task",
        "mode": mode,
        "status": "running",
        "container_state": "running",
    }
    meta_fields.update(meta_overrides)
    return Task(config, SimpleNamespace(**meta_fields))  # type: ignore[arg-type]


class TestTaskIdentity:
    """Identity properties read straight from the metadata / config."""

    def test_identity_properties(self) -> None:
        t = _task()
        assert t.id == _TID
        assert t.name == "my-task"
        assert t.mode == "cli"
        assert t.status == "running"
        assert t.container_state == "running"
        assert t.meta is t._meta

    def test_equality_by_project_and_id(self) -> None:
        assert _task() == _task()

    def test_inequality_across_projects(self) -> None:
        other = Task(SimpleNamespace(name="otherproj"), _task()._meta)  # type: ignore[arg-type]
        assert _task() != other

    def test_inequality_across_task_ids(self) -> None:
        assert _task() != _task(task_id="different")

    def test_hashes_by_project_and_id(self) -> None:
        assert hash(_task()) == hash(_task())
        assert {_task(), _task()} == {_task()}  # dedups in a set

    def test_repr_carries_id_name_mode(self) -> None:
        assert repr(_task()) == f"Task(id={_TID!r}, name='my-task', mode='cli')"


# Each entry: (call, patched-symbol, expected positional/keyword call)
_DELEGATIONS = [
    (lambda t: t.run_cli(), "task_run_cli", mock.call(_PROJ, _TID)),
    (lambda t: t.stop(timeout=5), "task_stop", mock.call(_PROJ, _TID, timeout=5)),
    (lambda t: t.restart(), "task_restart", mock.call(_PROJ, _TID)),
    (lambda t: t.delete(), "task_delete", mock.call(_PROJ, _TID)),
    (lambda t: t.rename("fresh"), "task_rename", mock.call(_PROJ, _TID, "fresh")),
    (
        lambda t: t.followup("more", follow=False),
        "task_followup_headless",
        mock.call(_PROJ, _TID, "more", follow=False),
    ),
    (lambda t: t.login(), "task_login", mock.call(_PROJ, _TID)),
]


@pytest.mark.parametrize(("call", "symbol", "expected"), _DELEGATIONS)
def test_lifecycle_delegations_thread_project_name(call, symbol, expected) -> None:  # type: ignore[no-untyped-def]
    """Lifecycle methods forward (project_name, task_id, …) to the service layer."""
    with mock.patch(f"terok.lib.domain.task.{symbol}") as m:
        call(_task())
    assert m.call_args == expected


class TestTaskObservation:
    """Read-side delegations and their return-value pass-through."""

    def test_logs_forwards_identity_and_options(self) -> None:
        with mock.patch("terok.lib.domain.task.task_logs") as m:
            _task().logs()
        args = m.call_args.args
        assert args[:2] == (_PROJ, _TID)
        from terok.lib.domain.task_logs import LogViewOptions

        assert isinstance(args[2], LogViewOptions)

    def test_get_login_command_passes_through(self) -> None:
        with mock.patch(
            "terok.lib.domain.task.get_login_command", return_value=["podman", "exec"]
        ) as m:
            assert _task().get_login_command() == ["podman", "exec"]
        assert m.call_args == mock.call(_PROJ, _TID)

    def test_get_workspace_diff_passes_through(self) -> None:
        with mock.patch("terok.lib.domain.task.get_workspace_git_diff", return_value="diff") as m:
            assert _task().get_workspace_diff(against="main") == "diff"
        assert m.call_args == mock.call(_PROJ, _TID, against="main")

    def test_wait_for_exit_builds_container_name(self) -> None:
        with (
            mock.patch("terok.lib.core.task_state.container_name", return_value="cname") as cn,
            mock.patch(
                "terok.lib.orchestration.tasks.wait_for_container_exit",
                return_value=(0, None),
            ) as wait,
        ):
            assert _task().wait_for_exit(timeout=10) == (0, None)
        assert cn.call_args == mock.call(_PROJ, "cli", _TID)
        assert wait.call_args == mock.call("cname", _PROJ, _TID, timeout=10)

    def test_wait_for_exit_without_mode_raises(self) -> None:
        with pytest.raises(RuntimeError, match="no mode"):
            _task(mode=None).wait_for_exit()

    def test_capture_logs_returns_none_without_mode(self) -> None:
        assert _task(mode=None).capture_logs() is None

    def test_capture_logs_delegates_with_config(self) -> None:
        t = _task()
        with mock.patch(
            "terok.lib.orchestration.tasks.capture_task_logs", return_value="/log"
        ) as m:
            assert t.capture_logs() == "/log"
        assert m.call_args == mock.call(t._config, _TID, "cli")

    def test_image_is_old_delegates(self) -> None:
        t = _task()
        with mock.patch("terok.lib.domain.project_state.is_task_image_old", return_value=True) as m:
            assert t.image_is_old() is True
        assert m.call_args == mock.call(_PROJ, t._meta)

    def test_show_status_delegates(self) -> None:
        with mock.patch("terok.lib.orchestration.tasks.task_status") as m:
            _task().show_status()
        assert m.call_args == mock.call(_PROJ, _TID)

    def test_doctor_constructs_with_identity(self) -> None:
        with mock.patch("terok.lib.orchestration.container_doctor.ContainerDoctor") as Doctor:
            Doctor.return_value.run.return_value = []
            assert _task().doctor(fix=True) == []
        assert Doctor.call_args == mock.call(_PROJ, _TID)
        Doctor.return_value.run.assert_called_once()
