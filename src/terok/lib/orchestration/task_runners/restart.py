# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task restart runner.

``task_restart`` stops a running container (if any) and starts it again.
"Restart" means *restart* — if the container is gone it raises rather
than re-creating it; that's ``task run``'s job.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core import runtime as _rt
from ...core.config import get_public_host
from ...core.projects import load_project
from ...util.ansi import (
    blue as _blue,
    green as _green,
    hyperlink as _hyperlink,
    supports_color as _supports_color,
)
from ..environment import ensure_vault
from ..hooks import run_hook
from ..ports import assign_web_port, release_web_port
from ..tasks import container_name, load_task_meta
from .container import _assert_running, _podman_start, _print_login_instructions
from .shield import _apply_shield_policy
from .toad import _rehydrate_toad_token, _toad_browser_url

if TYPE_CHECKING:
    from pathlib import Path

    from ...core.project_model import ProjectConfig


def _validate_restart_preconditions(
    project: ProjectConfig, task_id: str, mode: str, meta: dict, cname: str
) -> None:
    """Validate what would fail the restart *before* the container is stopped.

    Taking down a working service only to then error out is worse than
    refusing to stop: re-claim the saved web port and rehydrate the toad
    token here, raising ``SystemExit`` if either is no longer viable.
    """
    web_port = meta.get("web_port")
    if isinstance(web_port, int):
        actual = assign_web_port(project.id, task_id, preferred=web_port)
        if actual != web_port:
            release_web_port(project.id, task_id)
            raise SystemExit(
                f"Port {web_port} for {project.id}/{task_id} is no longer available "
                f"(got {actual}).  Re-create the task to use the new port."
            )
    if mode == "toad":
        _rehydrate_toad_token(project, task_id, meta, cname)


def _stop_running_container(
    project: ProjectConfig, task_id: str, mode: str, cname: str, meta_path: Path
) -> None:
    """Stop the running task container, then fire its ``post_stop`` hook."""
    try:
        _rt.resolve_runtime(project).container(cname).stop(timeout=project.shutdown_timeout)
    except FileNotFoundError:
        raise SystemExit("podman not found; please install podman")
    except RuntimeError as exc:
        raise SystemExit(f"Failed to stop container: {exc}")
    run_hook(
        "post_stop",
        project.hook_post_stop,
        project_id=project.id,
        task_id=task_id,
        mode=mode,
        cname=cname,
        task_dir=project.tasks_root / str(task_id),
        meta_path=meta_path,
    )


def _start_and_report_restart(
    project: ProjectConfig, task_id: str, mode: str, cname: str, meta: dict, meta_path: Path
) -> None:
    """Start the (stopped) container, apply shield policy, print how to reach it."""
    task_dir = project.tasks_root / str(task_id)
    _podman_start(project, cname)
    _assert_running(project, cname)
    run_hook(
        "post_start",
        project.hook_post_start,
        project_id=project.id,
        task_id=task_id,
        mode=mode,
        cname=cname,
        task_dir=task_dir,
        meta_path=meta_path,
    )
    _apply_shield_policy(project, cname, task_dir, is_restart=True)

    color_enabled = _supports_color()
    print(f"Restarted task {task_id}: {_green(cname, color_enabled)}")
    if mode == "cli":
        _print_login_instructions(project.id, task_id, cname, color_enabled)
    elif mode == "toad":
        port = meta.get("web_port")
        token = meta.get("web_token")
        if isinstance(port, int) and isinstance(token, str):
            url = _toad_browser_url(get_public_host(), port, token)
            print(f"Toad: {_hyperlink(_blue(url, color_enabled), url, enabled=color_enabled)}")


def task_restart(project_id: str, task_id: str) -> None:
    """Restart a task's container.

    Semantics: stop the container if running, then start it.  If the
    container doesn't exist (e.g. because it was deleted out-of-band),
    raise ``SystemExit`` with an actionable pointer to ``terok task run``
    — "restart" only means restart, not re-run.
    """
    project = load_project(project_id)
    meta, meta_path = load_task_meta(project.id, task_id)

    mode = meta.get("mode")
    if not mode:
        raise SystemExit(f"Task {task_id} has never been run (no mode set)")

    cname = container_name(project.id, mode, task_id)
    container_state = _rt.resolve_runtime(project).container(cname).state

    print(f"Restarting task {project_id}/{task_id} ({mode})...")
    ensure_vault()

    if container_state is None:
        # Container is gone — restart can't recreate it.  User must start
        # a fresh task with ``task run``.
        raise SystemExit(
            f"Container {cname} no longer exists.  Restart requires a running "
            f"or stopped container.  Create a new task with:\n"
            f"  terok task run {project_id}"
            + (' "<prompt>" --mode headless' if mode == "run" else "")
        )

    _validate_restart_preconditions(project, task_id, mode, meta, cname)
    if container_state == "running":
        _stop_running_container(project, task_id, mode, cname, meta_path)
    _start_and_report_restart(project, task_id, mode, cname, meta, meta_path)


__all__ = [
    "task_restart",
]
