# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Service facade — transitional re-export shim, slated for removal.

Historically the single stable import boundary for the terok library;
its logic has since moved to dedicated domain modules
([`terok.lib.domain.project`][terok.lib.domain.project],
[`terok.lib.domain.ssh`][terok.lib.domain.ssh],
[`terok.lib.domain.auth`][terok.lib.domain.auth]) and the public boundary
is now [`terok.lib.api`][terok.lib.api].

This module remains as a re-export shim while CLI and test imports are
migrated to ``terok.lib.api``.  No new imports of this module should be
added.
"""

from __future__ import annotations

from ..orchestration.image import build_images, generate_dockerfiles  # noqa: F401
from ..orchestration.task_runners import (  # noqa: F401 — re-exported public API
    HeadlessRunRequest,
    task_followup_headless,
    task_restart,
    task_run_cli,
    task_run_headless,
    task_run_toad,
)
from ..orchestration.tasks import (  # noqa: F401 — re-exported public API
    TaskDeleteResult,
    get_tasks,
    task_archive_list,
    task_archive_logs,
    task_delete,
    task_list,
    task_login,
    task_new,
    task_rename,
    task_status,
    task_stop,
)
from .auth import authenticate  # noqa: F401 — re-exported public API
from .image_cleanup import (  # noqa: F401 — re-exported public API
    cleanup_images,
    find_orphaned_images,
    list_images,
)
from .project import (  # noqa: F401 — re-exported public API
    DeleteProjectResult,
    Project,
    delete_project,
    derive_project,
    find_projects_sharing_gate,
    get_project,
    list_projects,
    make_git_gate,
    make_ssh_manager,
    project_image_exists,
)
from .project_state import get_project_state, is_task_image_old  # noqa: F401
from .ssh import (  # noqa: F401 — re-exported public API
    maybe_pause_for_ssh_key_registration,
    project_needs_key_registration,
    provision_ssh_key,
    register_ssh_key,
    summarize_ssh_init,
)
from .task import Task  # noqa: F401 — re-exported public API
from .task_logs import LogViewOptions, task_logs  # noqa: F401 — re-exported public API
from .vault import vault_db  # noqa: F401 — re-exported public API

__all__ = [
    # Project factory functions
    "get_project",
    "list_projects",
    "derive_project",
    # Rich domain objects
    "Project",
    "Task",
    # Image management
    "generate_dockerfiles",
    "build_images",
    "project_image_exists",
    # Image listing & cleanup
    "list_images",
    "find_orphaned_images",
    "cleanup_images",
    # Project lifecycle
    "delete_project",
    "DeleteProjectResult",
    # Task lifecycle
    "TaskDeleteResult",
    "task_new",
    "task_delete",
    "task_rename",
    "task_login",
    "task_list",
    "task_status",
    "task_stop",
    "task_archive_list",
    "task_archive_logs",
    "get_tasks",
    # Task runners
    "task_run_cli",
    "task_run_toad",
    "task_run_headless",
    "HeadlessRunRequest",
    "task_restart",
    "task_followup_headless",
    # Task logs
    "task_logs",
    "LogViewOptions",
    # Security setup
    "make_ssh_manager",
    "make_git_gate",
    # Workflow helpers
    "provision_ssh_key",
    "register_ssh_key",
    "summarize_ssh_init",
    "vault_db",
    "maybe_pause_for_ssh_key_registration",
    "project_needs_key_registration",
    # Auth
    "authenticate",
    # Project state
    "get_project_state",
    "is_task_image_old",
    "find_projects_sharing_gate",
]
