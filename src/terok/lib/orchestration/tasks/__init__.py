# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task metadata, lifecycle, and query operations.

This package is the single import surface for task operations —
``from terok.lib.orchestration.tasks import …`` resolves every public
name below.  The implementation is split into focused submodules:

* [`meta`][terok.lib.orchestration.tasks.meta] — the on-disk I/O
  boundary: the dossier/bookkeeping split-write protocol, path helpers,
  and the directories task state lives in.
* [`identity`][terok.lib.orchestration.tasks.identity] — task ID
  generation, validation, and prefix resolution.
* [`naming`][terok.lib.orchestration.tasks.naming] — task name
  sanitization and random-name generation.
* [`query`][terok.lib.orchestration.tasks.query] — the ``TaskMeta``
  read model and the functions that hydrate it from disk + live
  container state.
* [`lifecycle`][terok.lib.orchestration.tasks.lifecycle] — create,
  rename, delete (with archive-on-delete), stop, login, and status.
* [`archive`][terok.lib.orchestration.tasks.archive] — reading the
  immutable snapshots a deletion leaves behind.

Container runner functions (``task_run_cli``, ``task_run_headless``,
``task_restart``) live in the companion ``task_runners`` module.
Status computation and domain value objects live in ``task_state``;
their presentation tables live in ``task_display``.  Log viewing lives
in ``task_logs``.
"""

# ``container_name`` / ``CONTAINER_MODES`` originate in core.task_state;
# tasks has historically re-exported them as part of its public surface.
from ...core.task_state import CONTAINER_MODES, container_name  # noqa: F401
from .archive import (  # noqa: F401 — re-exported public API
    ArchivedTask,
    list_archived_tasks,
    task_archive_list,
    task_archive_logs,
)
from .identity import (  # noqa: F401 — re-exported public API
    is_task_id,
    normalize_task_id_input,
    resolve_task_id,
)
from .lifecycle import (  # noqa: F401 — re-exported public API
    TaskDeleteResult,
    capture_task_logs,
    get_login_command,
    task_delete,
    task_login,
    task_new,
    task_rename,
    task_status,
    task_stop,
    wait_for_container_exit,
)
from .meta import (  # noqa: F401 — re-exported public API
    CONTAINER_TEROK_CONFIG,
    agent_config_dir,
    dossier_path,
    iter_task_ids,
    load_task_meta,
    mark_task_deleting,
    meta_path,
    read_task_meta,
    task_exists,
    tasks_archive_dir,
    tasks_meta_dir,
    update_task_exit_code,
    write_task_meta,
)
from .naming import (  # noqa: F401 — re-exported public API
    TASK_NAME_MAX_LEN,
    generate_task_name,
    sanitize_task_name,
    validate_task_name,
)
from .query import (  # noqa: F401 — re-exported public API
    ContainerEventStream,
    TaskMeta,
    container_event_stream,
    get_all_task_states,
    get_task_container_state,
    get_task_meta,
    get_tasks,
    get_workspace_git_diff,
    lookup_container_by_pt,
    task_list,
)

__all__ = [
    "ArchivedTask",
    "CONTAINER_MODES",
    "CONTAINER_TEROK_CONFIG",
    "TASK_NAME_MAX_LEN",
    "ContainerEventStream",
    "TaskDeleteResult",
    "TaskMeta",
    "agent_config_dir",
    "capture_task_logs",
    "container_event_stream",
    "container_name",
    "dossier_path",
    "generate_task_name",
    "get_all_task_states",
    "get_login_command",
    "get_task_container_state",
    "get_task_meta",
    "get_tasks",
    "get_workspace_git_diff",
    "is_task_id",
    "iter_task_ids",
    "list_archived_tasks",
    "load_task_meta",
    "lookup_container_by_pt",
    "mark_task_deleting",
    "meta_path",
    "normalize_task_id_input",
    "read_task_meta",
    "resolve_task_id",
    "sanitize_task_name",
    "task_archive_list",
    "task_archive_logs",
    "task_delete",
    "task_exists",
    "task_list",
    "task_login",
    "task_new",
    "task_rename",
    "task_status",
    "task_stop",
    "tasks_archive_dir",
    "tasks_meta_dir",
    "update_task_exit_code",
    "validate_task_name",
    "wait_for_container_exit",
    "write_task_meta",
]
