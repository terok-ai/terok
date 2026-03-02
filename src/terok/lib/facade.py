# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Service facade for common cross-cutting operations.

Provides a single entry point for operations that both the CLI and TUI
frontends use, reducing the number of direct service-module imports
required by the presentation layer.

The facade re-exports key service functions and provides composite
helpers for multi-step workflows like project initialization.
"""

import logging
import shutil

from .containers.docker import build_images, generate_dockerfiles
from .containers.environment import WEB_BACKENDS
from .containers.project_state import get_project_state, is_task_image_old
from .containers.task_logs import task_logs  # noqa: F401 — re-exported public API
from .containers.task_runners import (  # noqa: F401 — re-exported public API
    task_followup_headless,
    task_restart,
    task_run_cli,
    task_run_headless,
    task_run_web,
)
from .containers.tasks import (  # noqa: F401 — re-exported public API
    get_tasks,
    task_delete,
    task_list,
    task_login,
    task_new,
    task_rename,
    task_status,
    task_stop,
    tasks_meta_dir,
)
from .core.config import build_root, get_envs_base_dir, state_root
from .core.projects import load_project
from .security.auth import AUTH_PROVIDERS, AuthProvider, authenticate
from .security.git_gate import (
    GateStalenessInfo,
    compare_gate_vs_upstream,
    find_projects_sharing_gate,
    get_gate_last_commit,
    sync_gate_branches,
    sync_project_gate,
)
from .security.ssh import init_project_ssh

_logger = logging.getLogger(__name__)


def project_delete(project_id: str) -> None:
    """Delete a project's configuration, state, and all associated artifacts.

    Removes (in order):

    1. All task containers (stopped best-effort) and their workspaces/metadata
    2. Git gate mirror (only if no other projects share it)
    3. SSH key directory
    4. Build artifacts (generated Dockerfiles)
    5. Project config directory (``project.yml``, instructions, presets)

    Raises :class:`SystemExit` if the project is not found.
    """
    project = load_project(project_id)

    # Delete every task (stops containers, removes workspace + metadata)
    meta_dir = tasks_meta_dir(project.id)
    if meta_dir.is_dir():
        for meta_file in meta_dir.glob("*.yml"):
            try:
                task_delete(project_id, meta_file.stem)
            except Exception:
                pass

    # Remove leftover task workspaces dir (task_delete handles individual dirs,
    # but the parent may remain)
    if project.tasks_root.is_dir():
        shutil.rmtree(project.tasks_root, ignore_errors=True)

    # Remove project state directory
    project_state_dir = state_root() / "projects" / project.id
    if project_state_dir.is_dir():
        shutil.rmtree(project_state_dir, ignore_errors=True)

    # Remove gate only if no other projects share it
    if project.gate_path.is_dir():
        sharing = find_projects_sharing_gate(project.gate_path, exclude_project=project.id)
        if not sharing:
            shutil.rmtree(project.gate_path, ignore_errors=True)
        else:
            shared_ids = ", ".join(pid for pid, _ in sharing)
            _logger.info("Keeping gate %s (shared by: %s)", project.gate_path, shared_ids)

    # Remove SSH directory
    ssh_dir = project.ssh_host_dir or (get_envs_base_dir() / f"_ssh-config-{project.id}")
    if ssh_dir.is_dir():
        shutil.rmtree(ssh_dir, ignore_errors=True)

    # Remove build artifacts
    project_build_dir = build_root() / project.id
    if project_build_dir.is_dir():
        shutil.rmtree(project_build_dir, ignore_errors=True)

    # Remove project config directory (last — load_project needed it above)
    if project.root.is_dir():
        shutil.rmtree(project.root, ignore_errors=True)

    _logger.info("Deleted project '%s'", project_id)


def maybe_pause_for_ssh_key_registration(project_id: str) -> None:
    """If the project's upstream uses SSH, pause so the user can register the deploy key.

    Call this right after ``init_project_ssh()`` — the public key will already
    have been printed to the terminal.  For HTTPS upstreams this is a no-op.
    """
    project = load_project(project_id)
    upstream = project.upstream_url or ""
    if upstream.startswith("git@") or upstream.startswith("ssh://"):
        print("\n" + "=" * 60)
        print("ACTION REQUIRED: Add the public key shown above as a")
        print("deploy key (or to your SSH keys) on the git remote.")
        print("=" * 60)
        input("Press Enter once the key is registered... ")


__all__ = [
    # Docker / image management
    "generate_dockerfiles",
    "build_images",
    # Environment
    "WEB_BACKENDS",
    # Task lifecycle
    "task_new",
    "task_delete",
    "task_rename",
    "task_login",
    "task_list",
    "task_status",
    "task_stop",
    "get_tasks",
    # Task runners
    "task_run_cli",
    "task_run_web",
    "task_run_headless",
    "task_restart",
    "task_followup_headless",
    # Task logs
    "task_logs",
    # Security setup
    "init_project_ssh",
    "sync_project_gate",
    # Project lifecycle
    "project_delete",
    # Workflow helpers
    "maybe_pause_for_ssh_key_registration",
    # Auth
    "AUTH_PROVIDERS",
    "AuthProvider",
    "authenticate",
    # Git gate
    "compare_gate_vs_upstream",
    "sync_gate_branches",
    "get_gate_last_commit",
    "GateStalenessInfo",
    "find_projects_sharing_gate",
    # Project state
    "get_project_state",
    "is_task_image_old",
]
