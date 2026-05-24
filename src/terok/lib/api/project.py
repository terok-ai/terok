# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Project entities, lifecycle, panic, SSH provisioning — public API surface.

Re-export catalog for everything project-shaped.  Sources:
[`terok.lib.core.projects`][terok.lib.core.projects] for the pure
``ProjectConfig`` value type and discovery helpers;
[`terok.lib.domain.project`][terok.lib.domain.project] for the rich
``Project`` aggregate and lifecycle;
[`terok.lib.domain.project_state`][terok.lib.domain.project_state] for
infrastructure-state queries;
[`terok.lib.domain.image_cleanup`][terok.lib.domain.image_cleanup] for
image listing & cleanup;
[`terok.lib.domain.panic`][terok.lib.domain.panic] for cross-project
lockdown;
[`terok.lib.domain.ssh`][terok.lib.domain.ssh] for the SSH provisioning
workflow; and
[`terok.lib.domain.wizards.new_project`][terok.lib.domain.wizards.new_project]
for the new-project wizard primitives that CLI prompts and TUI screens
share.
"""

from terok.lib.core.projects import (  # noqa: F401 — re-exported public API
    BrokenProject,
    ProjectConfig,
    discover_projects,
    load_project,
    require_project_exists,
    set_project_image_agents,
)
from terok.lib.domain.image_cleanup import (  # noqa: F401 — re-exported public API
    cleanup_images,
    find_orphaned_images,
    list_images,
    remove_images,
)
from terok.lib.domain.panic import (  # noqa: F401 — re-exported public API
    execute_panic,
    format_panic_report,
    panic_stop_containers,
)
from terok.lib.domain.project import (  # noqa: F401 — re-exported public API
    DeleteProjectResult,
    Project,
    delete_project,
    derive_project,
    find_projects_sharing_gate,
    get_project,
    list_projects,
    project_image_exists,
)
from terok.lib.domain.ssh import (  # noqa: F401 — re-exported public API
    summarize_ssh_init,
)
from terok.lib.domain.wizards.new_project import (  # noqa: F401 — re-exported public API
    AGENTS_QUESTION,
    QUESTIONS,
    Question,
    render_project_yaml,
    validate_answer,
    write_project_yaml,
)

__all__ = [
    "AGENTS_QUESTION",
    "BrokenProject",
    "DeleteProjectResult",
    "Project",
    "ProjectConfig",
    "QUESTIONS",
    "Question",
    "cleanup_images",
    "delete_project",
    "derive_project",
    "discover_projects",
    "execute_panic",
    "find_orphaned_images",
    "find_projects_sharing_gate",
    "format_panic_report",
    "get_project",
    "list_images",
    "list_projects",
    "load_project",
    "panic_stop_containers",
    "project_image_exists",
    "remove_images",
    "render_project_yaml",
    "require_project_exists",
    "set_project_image_agents",
    "summarize_ssh_init",
    "validate_answer",
    "write_project_yaml",
]
