# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task lifecycle, runners, metadata, and display — public API surface.

Re-export catalog for everything task-shaped.  Sources:
[`terok.lib.orchestration.tasks`][terok.lib.orchestration.tasks] for
metadata/lifecycle/queries,
[`terok.lib.orchestration.task_runners`][terok.lib.orchestration.task_runners]
for mode-specific entry points,
[`terok.lib.core.task_state`][terok.lib.core.task_state] and
[`terok.lib.core.task_display`][terok.lib.core.task_display] for the
value object + presentation tables, plus
[`terok.lib.domain.task`][terok.lib.domain.task] for the entity and
[`terok.lib.domain.task_logs`][terok.lib.domain.task_logs] for log
streaming.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terok.lib.core.task_display import (
        DEBUG_BADGE as DEBUG_BADGE,
        GPU_DISPLAY as GPU_DISPLAY,
        SECURITY_CLASS_DISPLAY as SECURITY_CLASS_DISPLAY,
        STATUS_DISPLAY as STATUS_DISPLAY,
        ModeInfo as ModeInfo,
        StatusInfo as StatusInfo,
        mode_info as mode_info,
    )
    from terok.lib.core.task_state import (
        CONTAINER_MODES as CONTAINER_MODES,
        container_name as container_name,
        effective_status as effective_status,
        has_gpu as has_gpu,
    )
    from terok.lib.domain.task import (
        Task as Task,
    )
    from terok.lib.domain.task_logs import (
        LogViewOptions as LogViewOptions,
        task_logs as task_logs,
    )
    from terok.lib.orchestration.task_runners import (
        HeadlessRunRequest as HeadlessRunRequest,
        ensure_task_running as ensure_task_running,
        task_followup_headless as task_followup_headless,
        task_restart as task_restart,
        task_run_cli as task_run_cli,
        task_run_headless as task_run_headless,
        task_run_toad as task_run_toad,
    )
    from terok.lib.orchestration.tasks import (
        ContainerEventStream as ContainerEventStream,
        TaskDeleteResult as TaskDeleteResult,
        TaskMeta as TaskMeta,
        agent_config_dir as agent_config_dir,
        container_event_stream as container_event_stream,
        generate_task_name as generate_task_name,
        get_all_task_states as get_all_task_states,
        get_login_command as get_login_command,
        get_task_meta as get_task_meta,
        get_tasks as get_tasks,
        get_workspace_git_diff as get_workspace_git_diff,
        mark_task_deleting as mark_task_deleting,
        sanitize_task_name as sanitize_task_name,
        task_archive_list as task_archive_list,
        task_archive_logs as task_archive_logs,
        task_delete as task_delete,
        task_list as task_list,
        task_login as task_login,
        task_new as task_new,
        task_rename as task_rename,
        task_status as task_status,
        task_stop as task_stop,
        tasks_meta_dir as tasks_meta_dir,
        validate_task_name as validate_task_name,
        wait_for_container_exit as wait_for_container_exit,
    )

#: Public name -> defining module (PEP 562 lazy resolution).
_LAZY: dict[str, str] = {
    "CONTAINER_MODES": "terok.lib.core.task_state",
    "ContainerEventStream": "terok.lib.orchestration.tasks",
    "DEBUG_BADGE": "terok.lib.core.task_display",
    "GPU_DISPLAY": "terok.lib.core.task_display",
    "HeadlessRunRequest": "terok.lib.orchestration.task_runners",
    "LogViewOptions": "terok.lib.domain.task_logs",
    "ModeInfo": "terok.lib.core.task_display",
    "SECURITY_CLASS_DISPLAY": "terok.lib.core.task_display",
    "STATUS_DISPLAY": "terok.lib.core.task_display",
    "StatusInfo": "terok.lib.core.task_display",
    "Task": "terok.lib.domain.task",
    "TaskDeleteResult": "terok.lib.orchestration.tasks",
    "TaskMeta": "terok.lib.orchestration.tasks",
    "agent_config_dir": "terok.lib.orchestration.tasks",
    "container_event_stream": "terok.lib.orchestration.tasks",
    "container_name": "terok.lib.core.task_state",
    "effective_status": "terok.lib.core.task_state",
    "ensure_task_running": "terok.lib.orchestration.task_runners",
    "generate_task_name": "terok.lib.orchestration.tasks",
    "get_all_task_states": "terok.lib.orchestration.tasks",
    "get_login_command": "terok.lib.orchestration.tasks",
    "get_task_meta": "terok.lib.orchestration.tasks",
    "get_tasks": "terok.lib.orchestration.tasks",
    "get_workspace_git_diff": "terok.lib.orchestration.tasks",
    "has_gpu": "terok.lib.core.task_state",
    "mark_task_deleting": "terok.lib.orchestration.tasks",
    "mode_info": "terok.lib.core.task_display",
    "sanitize_task_name": "terok.lib.orchestration.tasks",
    "task_archive_list": "terok.lib.orchestration.tasks",
    "task_archive_logs": "terok.lib.orchestration.tasks",
    "task_delete": "terok.lib.orchestration.tasks",
    "task_followup_headless": "terok.lib.orchestration.task_runners",
    "task_list": "terok.lib.orchestration.tasks",
    "task_login": "terok.lib.orchestration.tasks",
    "task_logs": "terok.lib.domain.task_logs",
    "task_new": "terok.lib.orchestration.tasks",
    "task_rename": "terok.lib.orchestration.tasks",
    "task_restart": "terok.lib.orchestration.task_runners",
    "task_run_cli": "terok.lib.orchestration.task_runners",
    "task_run_headless": "terok.lib.orchestration.task_runners",
    "task_run_toad": "terok.lib.orchestration.task_runners",
    "task_status": "terok.lib.orchestration.tasks",
    "task_stop": "terok.lib.orchestration.tasks",
    "tasks_meta_dir": "terok.lib.orchestration.tasks",
    "validate_task_name": "terok.lib.orchestration.tasks",
    "wait_for_container_exit": "terok.lib.orchestration.tasks",
}

# Every task symbol is consumed through the flat [`terok.lib.api`][terok.lib.api]
# front door (``from terok.lib.api import Task``), never ``from
# terok.lib.api.task import Task`` — so the stable surface is advertised there.
# ``_LAZY`` above stays the resolution source of truth; this module advertises
# no names of its own.
__all__: list[str] = []


def __getattr__(name: str) -> object:
    """Resolve a re-exported name to its source module on first access (PEP 562)."""
    try:
        target = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module_path, _, source_name = target.partition(":")
    value = getattr(importlib.import_module(module_path), source_name or name)
    globals()[name] = value  # cache so subsequent lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    """Expose the lazy names to ``dir()`` / autocompletion."""
    return sorted({*globals(), *_LAZY})
