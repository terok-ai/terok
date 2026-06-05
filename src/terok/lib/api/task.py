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

from terok.lib.core.task_display import (  # noqa: F401 — re-exported public API
    GPU_DISPLAY,
    SECURITY_CLASS_DISPLAY,
    STATUS_DISPLAY,
    ModeInfo,
    StatusInfo,
    mode_info,
)
from terok.lib.core.task_state import (  # noqa: F401 — re-exported public API
    CONTAINER_MODES,
    container_name,
    effective_status,
    has_gpu,
)
from terok.lib.domain.task import Task  # noqa: F401 — re-exported public API
from terok.lib.domain.task_logs import (  # noqa: F401 — re-exported public API
    LogViewOptions,
    task_logs,
)
from terok.lib.orchestration.task_runners import (  # noqa: F401 — re-exported public API
    HeadlessRunRequest,
    task_followup_headless,
    task_restart,
    task_run_cli,
    task_run_headless,
    task_run_toad,
)
from terok.lib.orchestration.tasks import (  # noqa: F401 — re-exported public API
    ContainerEventStream,
    TaskDeleteResult,
    TaskMeta,
    agent_config_dir,
    container_event_stream,
    generate_task_name,
    get_all_task_states,
    get_login_command,
    get_task_meta,
    get_tasks,
    get_workspace_git_diff,
    mark_task_deleting,
    sanitize_task_name,
    task_archive_list,
    task_archive_logs,
    task_delete,
    task_list,
    task_login,
    task_new,
    task_rename,
    task_status,
    task_stop,
    tasks_meta_dir,
    validate_task_name,
    wait_for_container_exit,
)

__all__ = [
    "CONTAINER_MODES",
    "GPU_DISPLAY",
    "HeadlessRunRequest",
    "LogViewOptions",
    "ModeInfo",
    "SECURITY_CLASS_DISPLAY",
    "STATUS_DISPLAY",
    "ContainerEventStream",
    "StatusInfo",
    "Task",
    "TaskDeleteResult",
    "TaskMeta",
    "agent_config_dir",
    "container_event_stream",
    "container_name",
    "effective_status",
    "generate_task_name",
    "get_all_task_states",
    "get_login_command",
    "get_task_meta",
    "get_tasks",
    "get_workspace_git_diff",
    "has_gpu",
    "mark_task_deleting",
    "mode_info",
    "sanitize_task_name",
    "task_archive_list",
    "task_archive_logs",
    "task_delete",
    "task_followup_headless",
    "task_list",
    "task_login",
    "task_logs",
    "task_new",
    "task_rename",
    "task_restart",
    "task_run_cli",
    "task_run_headless",
    "task_run_toad",
    "task_status",
    "task_stop",
    "tasks_meta_dir",
    "validate_task_name",
    "wait_for_container_exit",
]
