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

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terok.lib.core.projects import (
        BrokenProject as BrokenProject,
        ProjectConfig as ProjectConfig,
        discover_projects as discover_projects,
        load_project as load_project,
        require_project_exists as require_project_exists,
        set_project_image_agents as set_project_image_agents,
    )
    from terok.lib.domain.image_cleanup import (
        cleanup_images as cleanup_images,
        find_orphaned_images as find_orphaned_images,
        list_images as list_images,
        remove_images as remove_images,
    )
    from terok.lib.domain.panic import (
        execute_panic as execute_panic,
        format_panic_report as format_panic_report,
        panic_stop_containers as panic_stop_containers,
    )
    from terok.lib.domain.project import (
        DeleteProjectResult as DeleteProjectResult,
        Project as Project,
        delete_project as delete_project,
        derive_project as derive_project,
        find_projects_sharing_gate as find_projects_sharing_gate,
        get_project as get_project,
        list_projects as list_projects,
        project_image_exists as project_image_exists,
    )
    from terok.lib.domain.project_state import (
        auth_image_staleness_warning as auth_image_staleness_warning,
    )
    from terok.lib.domain.ssh import (
        summarize_ssh_init as summarize_ssh_init,
    )
    from terok.lib.domain.wizards.new_project import (
        AGENTS_QUESTION as AGENTS_QUESTION,
        BASE_GPU_VENDOR as BASE_GPU_VENDOR,
        CUSTOM_BASE as CUSTOM_BASE,
        CUSTOM_IMAGE_WARNING as CUSTOM_IMAGE_WARNING,
        QUESTIONS as QUESTIONS,
        GpuDeviceChoice as GpuDeviceChoice,
        Question as Question,
        detect_gpu_choices as detect_gpu_choices,
        render_project_yaml as render_project_yaml,
        validate_answer as validate_answer,
        validate_custom_image as validate_custom_image,
        validate_gpus as validate_gpus,
        write_project_yaml as write_project_yaml,
    )

#: Public name -> defining module (PEP 562 lazy resolution).
_LAZY: dict[str, str] = {
    "AGENTS_QUESTION": "terok.lib.domain.wizards.new_project",
    "validate_custom_image": "terok.lib.domain.wizards.new_project",
    "validate_gpus": "terok.lib.domain.wizards.new_project",
    "detect_gpu_choices": "terok.lib.domain.wizards.new_project",
    "GpuDeviceChoice": "terok.lib.domain.wizards.new_project",
    "BASE_GPU_VENDOR": "terok.lib.domain.wizards.new_project",
    "CUSTOM_BASE": "terok.lib.domain.wizards.new_project",
    "CUSTOM_IMAGE_WARNING": "terok.lib.domain.wizards.new_project",
    "BrokenProject": "terok.lib.core.projects",
    "DeleteProjectResult": "terok.lib.domain.project",
    "Project": "terok.lib.domain.project",
    "ProjectConfig": "terok.lib.core.projects",
    "QUESTIONS": "terok.lib.domain.wizards.new_project",
    "Question": "terok.lib.domain.wizards.new_project",
    "auth_image_staleness_warning": "terok.lib.domain.project_state",
    "cleanup_images": "terok.lib.domain.image_cleanup",
    "delete_project": "terok.lib.domain.project",
    "derive_project": "terok.lib.domain.project",
    "discover_projects": "terok.lib.core.projects",
    "execute_panic": "terok.lib.domain.panic",
    "find_orphaned_images": "terok.lib.domain.image_cleanup",
    "find_projects_sharing_gate": "terok.lib.domain.project",
    "format_panic_report": "terok.lib.domain.panic",
    "get_project": "terok.lib.domain.project",
    "list_images": "terok.lib.domain.image_cleanup",
    "list_projects": "terok.lib.domain.project",
    "load_project": "terok.lib.core.projects",
    "panic_stop_containers": "terok.lib.domain.panic",
    "project_image_exists": "terok.lib.domain.project",
    "remove_images": "terok.lib.domain.image_cleanup",
    "render_project_yaml": "terok.lib.domain.wizards.new_project",
    "require_project_exists": "terok.lib.core.projects",
    "set_project_image_agents": "terok.lib.core.projects",
    "summarize_ssh_init": "terok.lib.domain.ssh",
    "validate_answer": "terok.lib.domain.wizards.new_project",
    "write_project_yaml": "terok.lib.domain.wizards.new_project",
}

# Every project symbol is consumed through the flat [`terok.lib.api`][terok.lib.api]
# front door (``from terok.lib.api import load_project``), never ``from
# terok.lib.api.project import load_project`` — so the stable surface is
# advertised there.  ``_LAZY`` above stays the resolution source of truth;
# this module advertises no names of its own.
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
