# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task restart runner.

``task_restart`` stops a running container (if any) and starts it again.
"Restart" means *restart* — if the container is gone it raises rather
than re-creating it; that's ``task run``'s job.
"""

from __future__ import annotations

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
    container_state = _rt.get_runtime().container(cname).state

    print(f"Restarting task {project_id}/{task_id} ({mode})...")
    ensure_vault()

    # Validate the preconditions that would fail the restart *before*
    # stopping a healthy container — taking down a working service only
    # to then error out is a worse outcome than refusing to stop.
    if container_state is not None:
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

    if container_state == "running":
        # Container is running - stop it first, then start it again
        try:
            _rt.get_runtime().container(cname).stop(timeout=project.shutdown_timeout)
        except FileNotFoundError:
            raise SystemExit("podman not found; please install podman")
        except RuntimeError as exc:
            raise SystemExit(f"Failed to stop container: {exc}")
        run_hook(
            "post_stop",
            project.hook_post_stop,
            project_id=project_id,
            task_id=task_id,
            mode=mode,
            cname=cname,
            task_dir=project.tasks_root / str(task_id),
            meta_path=meta_path,
        )

    if container_state is not None:
        task_dir = project.tasks_root / str(task_id)
        _podman_start(cname)
        _assert_running(cname)
        run_hook(
            "post_start",
            project.hook_post_start,
            project_id=project_id,
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
            _print_login_instructions(project_id, task_id, cname, color_enabled)
        elif mode == "toad":
            port = meta.get("web_port")
            token = meta.get("web_token")
            if isinstance(port, int) and isinstance(token, str):
                url = _toad_browser_url(get_public_host(), port, token)
                print(f"Toad: {_hyperlink(_blue(url, color_enabled), url, enabled=color_enabled)}")
    else:
        # Container is gone — restart can't recreate it.  User must start
        # a fresh task with ``task run``.
        raise SystemExit(
            f"Container {cname} no longer exists.  Restart requires a running "
            f"or stopped container.  Create a new task with:\n"
            f"  terok task run {project_id}"
            + (' "<prompt>" --mode headless' if mode == "run" else "")
        )


__all__ = [
    "task_restart",
]
