# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Public library API — the one stable import boundary for presentation layers.

The TUI (and over time the CLI) should import everything domain-, type-, or
config-related from this module rather than reaching into ``terok.lib.*``
internals.  This keeps the consumer surface narrow and lets the internals be
refactored freely.

Three kinds of exports live here:

1. **Operations** — task/project/image lifecycle functions re-exported from
   the domain and orchestration modules (``task_new``, ``build_images``,
   ``delete_project``, …).
2. **Types** — value objects and dataclasses consumers pass around
   (``ProjectConfig``, ``TaskMeta``, ``StatusInfo``, …).
3. **Snapshots** — [`get_config`][terok.lib.api.get_config] returns a
   single [`Config`][terok.lib.api.Config] dataclass that bundles the paths,
   feature flags, and presentation hints scattered across
   ``core.config``/``core.paths``.

Pure utilities (``util.emoji``, ``util.yaml``, ``util.ansi``, ``util.text_wrap``,
``util.net``) and ``core.version`` stay importable directly — they are
genuinely cross-cutting and have no domain coupling worth funnelling.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .core import config as _config, paths as _paths, runtime as _runtime

# Side-effecting / factory helpers that don't fit on the frozen Config object.
from .core.config import (  # noqa: F401 — re-exported public API
    make_sandbox_config,
    set_experimental,
)
from .core.images import (  # noqa: F401 — re-exported public API
    installed_agents,
    installed_agents_for_project,
)

# Domain value types consumers need to type their own code.
from .core.projects import (  # noqa: F401 — re-exported public API
    BrokenProject,
    ProjectConfig,
    discover_projects,
    load_project,
    set_project_image_agents,
)

# Presentation tables for status/mode/badge rendering.
from .core.task_display import (  # noqa: F401 — re-exported public API
    GPU_DISPLAY,
    ISOLATION_DISPLAY,
    MODE_DISPLAY,
    SECURITY_CLASS_DISPLAY,
    STATUS_DISPLAY,
    ModeInfo,
    ProjectBadge,
    StatusInfo,
    mode_info,
)
from .core.task_state import (  # noqa: F401 — re-exported public API
    CONTAINER_MODES,
    TaskState,
    container_name,
    effective_status,
    has_gpu,
)

# Auth flow.
from .domain.auth import authenticate  # noqa: F401 — re-exported public API

# Image listing & cleanup.
from .domain.image_cleanup import (  # noqa: F401 — re-exported public API
    cleanup_images,
    find_orphaned_images,
    list_images,
)

# Cross-project lockdown.
from .domain.panic import (  # noqa: F401 — re-exported public API
    execute_panic,
    format_panic_report,
    panic_stop_containers,
)

# Project aggregate + factories + lifecycle.
from .domain.project import (  # noqa: F401 — re-exported public API
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
from .domain.project_state import (  # noqa: F401 — re-exported public API
    get_project_state,
    is_task_image_old,
)

# SSH provisioning workflow.
from .domain.ssh import (  # noqa: F401 — re-exported public API
    maybe_pause_for_ssh_key_registration,
    project_needs_key_registration,
    provision_ssh_key,
    register_ssh_key,
    summarize_ssh_init,
)

# Task entity.
from .domain.task import Task  # noqa: F401 — re-exported public API

# Task logs.
from .domain.task_logs import (  # noqa: F401 — re-exported public API
    LogViewOptions,
    task_logs,
)

# Vault context manager.
from .domain.vault import vault_db  # noqa: F401 — re-exported public API

# Wizard pieces shared between CLI prompts and TUI screens.
from .domain.wizards.new_project import (  # noqa: F401 — re-exported public API
    AGENTS_QUESTION,
    QUESTIONS,
    Question,
    render_project_yaml,
    validate_answer,
    write_project_yaml,
)
from .orchestration.agent_config import (  # noqa: F401 — re-exported public API
    resolve_agent_config,
)
from .orchestration.autopilot import (  # noqa: F401 — re-exported public API
    wait_for_container_exit,
)

# Image build + dockerfile generation.
from .orchestration.image import (  # noqa: F401 — re-exported public API
    build_images,
    generate_dockerfiles,
)

# Task runners (mode-specific entry points).
from .orchestration.task_runners import (  # noqa: F401 — re-exported public API
    HeadlessRunRequest,
    task_followup_headless,
    task_restart,
    task_run_cli,
    task_run_headless,
    task_run_toad,
)

# Task orchestration helpers used by CLI commands + TUI workers.
from .orchestration.tasks import (  # noqa: F401 — re-exported public API
    TaskDeleteResult,
    TaskMeta,
    agent_config_dir,
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
    validate_task_name,
)

# ── Config snapshot ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Config:
    """Snapshot of the global config values consumers read at startup.

    Bundles the paths, feature flags, and presentation hints that the TUI
    (and CLI) previously pulled from a dozen scattered getters in
    [`terok.lib.core.config`][terok.lib.core.config] and
    [`terok.lib.core.paths`][terok.lib.core.paths].  Capture once with
    [`get_config`][terok.lib.api.get_config]; pass the result around.

    Side-effecting helpers — ``set_experimental``, ``make_sandbox_config`` —
    stay as functions on the [`api`][terok.lib.api] module; they don't
    belong on a frozen value object.
    """

    # Paths
    config_root: Path
    core_state_dir: Path
    runtime_dir: Path
    archive_dir: Path
    vault_dir: Path
    user_projects_dir: Path
    global_config_path: Path
    # Settings
    public_host: str
    shield_bypass_firewall_no_protection: bool
    tui_default_tmux: bool
    tui_external_editor: bool
    # Presentation hints
    shield_security_hint: str


def get_config() -> Config:
    """Snapshot the global config into a single [`Config`][terok.lib.api.Config] value."""
    return Config(
        config_root=_paths.config_root(),
        core_state_dir=_paths.core_state_dir(),
        runtime_dir=_paths.runtime_dir(),
        archive_dir=_config.archive_dir(),
        vault_dir=_config.vault_dir(),
        user_projects_dir=_config.user_projects_dir(),
        global_config_path=_config.global_config_path(),
        public_host=_config.get_public_host(),
        shield_bypass_firewall_no_protection=_config.get_shield_bypass_firewall_no_protection(),
        tui_default_tmux=_config.get_tui_default_tmux(),
        tui_external_editor=_config.get_tui_external_editor(),
        shield_security_hint=_config.SHIELD_SECURITY_HINT,
    )


# ── Runtime peek ───────────────────────────────────────────────────────


def get_container_state(cname: str) -> str | None:
    """Return the live podman state for a container, or ``None`` if not found.

    Thin wrapper around the runtime driver so callers do not need to
    reach into [`terok.lib.core.runtime`][terok.lib.core.runtime] for
    one-shot state lookups.
    """
    return _runtime.get_runtime().container(cname).state


__all__ = [
    # Snapshot
    "Config",
    "get_config",
    # Side-effecting helpers
    "make_sandbox_config",
    "set_experimental",
    "get_container_state",
    # Project factories + rich aggregates
    "get_project",
    "list_projects",
    "derive_project",
    "Project",
    "Task",
    "project_image_exists",
    # Project types
    "ProjectConfig",
    "BrokenProject",
    "discover_projects",
    "load_project",
    "set_project_image_agents",
    # Image management
    "generate_dockerfiles",
    "build_images",
    "list_images",
    "find_orphaned_images",
    "cleanup_images",
    "installed_agents",
    "installed_agents_for_project",
    # Project lifecycle
    "delete_project",
    "DeleteProjectResult",
    "find_projects_sharing_gate",
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
    # Task helpers
    "TaskMeta",
    "TaskState",
    "CONTAINER_MODES",
    "container_name",
    "agent_config_dir",
    "generate_task_name",
    "get_all_task_states",
    "get_login_command",
    "get_task_meta",
    "get_workspace_git_diff",
    "mark_task_deleting",
    "sanitize_task_name",
    "validate_task_name",
    "effective_status",
    "has_gpu",
    "wait_for_container_exit",
    # Task logs
    "task_logs",
    "LogViewOptions",
    # Display tables
    "STATUS_DISPLAY",
    "MODE_DISPLAY",
    "SECURITY_CLASS_DISPLAY",
    "ISOLATION_DISPLAY",
    "GPU_DISPLAY",
    "StatusInfo",
    "ModeInfo",
    "ProjectBadge",
    "mode_info",
    # Security setup
    "make_ssh_manager",
    "make_git_gate",
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
    # Wizards
    "AGENTS_QUESTION",
    "QUESTIONS",
    "Question",
    "render_project_yaml",
    "validate_answer",
    "write_project_yaml",
    # Agent config
    "resolve_agent_config",
    # Emergency
    "execute_panic",
    "format_panic_report",
    "panic_stop_containers",
]
