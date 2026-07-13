# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the [`Project`][terok.lib.domain.project.Project] aggregate.

Cover the identity semantics and the task-containment / infrastructure
delegations.  Every delegation must thread the project *name* (the slug) into
the orchestration layer — the invariant the project_id→project_name rename had
to preserve.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from terok.lib.domain.project import Project

_PROJ = "demoproj"


def _project(**config_overrides: object) -> Project:
    """Build a Project over a lightweight config stand-in."""
    fields: dict[str, object] = {"name": _PROJ, "security_class": "online", "upstream_url": None}
    fields.update(config_overrides)
    return Project(SimpleNamespace(**fields))  # type: ignore[arg-type]


class TestProjectIdentity:
    """Identity delegates to the wrapped config; equality is name-based."""

    def test_name_and_config_and_security_class(self) -> None:
        p = _project()
        assert p.name == _PROJ
        assert p.security_class == "online"
        assert p.config is p._config

    def test_equality_and_hash_by_name(self) -> None:
        # Two separate instances throughout: equality is by name, not identity.
        first, second = _project(), _project()
        assert first == second
        assert first != _project(name="other")
        assert first != object()
        assert hash(first) == hash(second)
        assert {first, second} == {first}

    def test_repr(self) -> None:
        assert repr(_project()) == f"Project(name={_PROJ!r}, security='online')"


class TestTaskContainment:
    """create_task / get_task / list_tasks thread the project name."""

    def test_create_task_delegates_and_wraps(self) -> None:
        meta = SimpleNamespace(task_id="t1")
        with (
            mock.patch("terok.lib.domain.project.task_new", return_value="t1") as new,
            mock.patch("terok.lib.domain.project.get_task_meta", return_value=meta) as get_meta,
        ):
            task = _project().create_task(name="fancy")
        assert new.call_args == mock.call(_PROJ, name="fancy")
        assert get_meta.call_args == mock.call(_PROJ, "t1")
        assert task.id == "t1"

    def test_get_task_delegates_and_wraps(self) -> None:
        meta = SimpleNamespace(task_id="t9")
        with mock.patch("terok.lib.domain.project.get_task_meta", return_value=meta) as get_meta:
            task = _project().get_task("t9")
        assert get_meta.call_args == mock.call(_PROJ, "t9")
        assert task.id == "t9"

    def test_list_tasks_hydrates_state_and_filters(self) -> None:
        metas = [
            SimpleNamespace(task_id="a", mode="cli", status="running", container_state=None),
            SimpleNamespace(task_id="b", mode="run", status="stopped", container_state=None),
        ]
        with (
            mock.patch("terok.lib.domain.project.get_tasks", return_value=metas) as get_tasks,
            mock.patch(
                "terok.lib.domain.project.get_all_task_states",
                return_value={"a": "running", "b": "exited"},
            ) as states,
        ):
            result = _project().list_tasks(mode="cli")
        assert get_tasks.call_args == mock.call(_PROJ)
        # State hydration runs over the already mode-filtered metas (cli-only).
        assert states.call_args.args[0] == _PROJ
        assert [m.task_id for m in states.call_args.args[1]] == ["a"]
        # Only the cli-mode task survives the mode filter, and its live state was hydrated.
        assert [t.id for t in result] == ["a"]
        assert metas[0].container_state == "running"

    def test_list_tasks_errors_when_state_query_fails(self) -> None:
        """A failed batch query (``None``) is an error — not silently wrong statuses."""
        metas = [SimpleNamespace(task_id="a", mode="cli", status="running", container_state=None)]
        with (
            mock.patch("terok.lib.domain.project.get_tasks", return_value=metas),
            mock.patch("terok.lib.domain.project.get_all_task_states", return_value=None),
            pytest.raises(SystemExit, match="runtime unavailable"),
        ):
            _project().list_tasks()

    def test_tasks_property_delegates_to_list_tasks(self) -> None:
        with mock.patch.object(Project, "list_tasks", return_value=["sentinel"]) as lt:
            assert _project().tasks == ["sentinel"]
        lt.assert_called_once_with()


# Each entry exercises a one-line delegation that must forward the project name.
_NAME_DELEGATIONS = [
    (lambda p: p.delete(), "terok.lib.domain.project.delete_project", mock.call(_PROJ)),
    (
        lambda p: p.generate_dockerfiles(),
        "terok.lib.domain.project.generate_dockerfiles",
        mock.call(_PROJ),
    ),
    (
        lambda p: p.build_images(include_dev=True, refresh_agents=True, full=True),
        "terok.lib.domain.project.build_images",
        mock.call(_PROJ, include_dev=True, refresh_agents=True, full_rebuild=True),
    ),
    (
        lambda p: p.storage_detail(),
        "terok.lib.domain.storage.get_project_storage_detail",
        mock.call(_PROJ),
    ),
    (
        lambda p: p.followup_headless("t1", "go", follow=False),
        "terok.lib.orchestration.task_runners.task_followup_headless",
        mock.call(_PROJ, "t1", "go", follow=False),
    ),
]


@pytest.mark.parametrize(("call", "symbol", "expected"), _NAME_DELEGATIONS)
def test_name_delegations(call, symbol, expected) -> None:  # type: ignore[no-untyped-def]
    """Infrastructure helpers forward the project name to the service layer."""
    with mock.patch(symbol) as m:
        call(_project())
    assert m.call_args == expected


class TestInfrastructureManagers:
    """Lazy-initialized managers are built from the config and cached."""

    def test_state_threads_name_and_config(self) -> None:
        p = _project()
        with mock.patch(
            "terok.lib.domain.project_state.get_project_state", return_value={"ok": True}
        ) as gs:
            assert p.state() == {"ok": True}
        assert gs.call_args == mock.call(_PROJ, gate_commit_provider=None, project=p._config)

    def test_gate_is_lazy_and_cached(self) -> None:
        p = _project()
        with mock.patch("terok.lib.domain.project.make_git_gate", return_value="GATE") as factory:
            assert p.gate == "GATE"
            assert p.gate == "GATE"  # second access reuses the cache
        factory.assert_called_once_with(p._config)

    def test_ssh_is_lazy_and_cached(self) -> None:
        p = _project()
        with mock.patch("terok.lib.domain.project.make_ssh_manager", return_value="SSH") as factory:
            assert p.ssh == "SSH"
            assert p.ssh == "SSH"
        factory.assert_called_once_with(p._config)

    def test_agents_is_lazy_and_cached(self) -> None:
        p = _project()
        with mock.patch("terok.lib.domain.project.AgentManager", return_value="AGENTS") as factory:
            assert p.agents == "AGENTS"
            assert p.agents == "AGENTS"
        factory.assert_called_once_with(p._config)
