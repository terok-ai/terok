# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Focused tests for project/task list widget identity plumbing."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from terok.lib.api import BrokenProject
from tests.unit.tui.tui_test_helpers import import_widgets


def _wire_listview(widget: Any) -> tuple[list[Any], list[Any]]:
    """Attach the tiny ListView surface the Textual stubs intentionally omit."""
    appended: list[Any] = []
    posted: list[Any] = []

    def append(item: Any) -> None:
        item.parent = widget
        appended.append(item)

    widget.append = append
    widget.clear = appended.clear
    widget.post_message = posted.append
    return appended, posted


def test_project_list_uses_project_names_for_rows_and_messages(tmp_path: Path) -> None:
    """Healthy and broken project rows carry the canonical project name."""
    widgets = import_widgets()
    project_list = widgets.ProjectList()
    rows, messages = _wire_listview(project_list)

    healthy = SimpleNamespace(name="healthy", security_class="online", root=tmp_path / "healthy")
    broken = BrokenProject(
        name="broken",
        config_path=tmp_path / "broken" / "project.yml",
        error="project.name must match directory",
    )

    project_list.set_projects([healthy], [broken])

    assert [row.project_name for row in rows] == ["broken", "healthy"]
    assert [row.is_broken for row in rows] == [True, False]

    project_list.select_project("broken")
    assert project_list.index == 0
    project_list.select_project("healthy")
    assert project_list.index == 1

    project_list._post_selected_project(rows[1])
    assert messages[-1].project_name == "healthy"
    assert messages[-1].is_broken is False


def test_task_list_preserves_live_state_and_posts_current_project() -> None:
    """Task rows keep live state across reloads and emit the owning project name."""
    widgets = import_widgets()
    task_list = widgets.TaskList()
    rows, messages = _wire_listview(task_list)
    task_list._label_width = lambda: 80

    first_snapshot = widgets.TaskMeta(
        task_id="t1", mode="cli", workspace="", web_port=None, container_state="running"
    )
    task_list.set_tasks("alpha", [first_snapshot])

    refreshed_snapshot = widgets.TaskMeta(
        task_id="t1", mode="cli", workspace="", web_port=None, container_state=None
    )
    task_list.set_tasks("alpha", [refreshed_snapshot])

    assert task_list.tasks[0].container_state == "running"
    assert rows[-1].project_name == "alpha"

    task_list._post_selected_task(rows[-1])
    assert messages[-1].project_name == "alpha"
    assert messages[-1].task.task_id == "t1"


def test_task_label_shows_debug_badge_only_when_debug() -> None:
    """A debug-mode task gets the cockroach badge; a normal one does not."""
    widgets = import_widgets()
    task_list = widgets.TaskList()
    _wire_listview(task_list)
    task_list._label_width = lambda: 80

    cockroach = "\U0001fab3"
    normal = widgets.TaskMeta(task_id="t1", mode="cli", workspace="", web_port=None)
    debugged = widgets.TaskMeta(task_id="t2", mode="cli", workspace="", web_port=None, debug=True)

    assert cockroach not in task_list._format_task_label(normal)
    assert cockroach in task_list._format_task_label(debugged)


def test_task_list_drops_stale_selection_messages() -> None:
    """Stale rows from another project cannot update the current selection."""
    widgets = import_widgets()
    task_list = widgets.TaskList()
    rows, messages = _wire_listview(task_list)
    task_list._label_width = lambda: 80

    task_list.set_tasks(
        "alpha", [widgets.TaskMeta(task_id="t1", mode="cli", workspace="", web_port=None)]
    )
    task_list.project_name = "beta"

    task_list._post_selected_task(rows[-1])

    assert messages == []
