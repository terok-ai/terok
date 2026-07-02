# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task restart runner.

``task_restart`` is the one verb that brings a task's container back to
running: stop it if running, then start it — resuming the existing
container when possible, recreating it in place when not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core import runtime as _rt
from ...core.config import get_public_host
from ...core.images import project_cli_image
from ...core.projects import load_project
from ...util.ansi import (
    blue as _blue,
    green as _green,
    hyperlink as _hyperlink,
    supports_color as _supports_color,
    yellow as _yellow,
)
from ..hooks import run_hook
from ..ports import assign_web_port, release_web_port
from ..tasks import container_name, load_task_meta
from .cli import task_run_cli
from .container import _assert_running, _podman_start, _print_login_instructions, _sandbox
from .shield import _apply_shield_policy
from .toad import _rehydrate_toad_token, _toad_browser_url, task_run_toad

if TYPE_CHECKING:
    from pathlib import Path

    from ...core.project_model import ProjectConfig


def task_restart(project_name: str, task_id: str, *, fresh: bool = False) -> None:
    """Bring a task's container back to running: stop if running, then start.

    The start is a best-effort ladder.  First rung: resume the existing
    container in place.  When that rung is gone — the container no longer
    exists, podman refuses to start it, or its image no longer matches
    the current project image — warn and recreate the container through
    the normal launch path: same task and container name, workspace
    reused as-is (never re-seeded), project config re-read, tokens
    minted fresh, per-task settings carried over from the saved
    metadata.  *fresh* skips straight to the recreate rung (e.g. to pick
    up a rebuilt image).

    Headless tasks (mode ``run``) only ever take the resume rung:
    recreating one would replay its original prompt against the
    workspace.
    """
    project = load_project(project_name)
    meta, meta_path = load_task_meta(project.name, task_id)

    mode = meta.get("mode")
    if not mode:
        raise SystemExit(f"Task {task_id} has never been run (no mode set)")

    cname = container_name(project.name, mode, task_id)
    container_state = _rt.resolve_runtime(project).container(cname).state

    print(f"Restarting task {project_name}/{task_id} ({mode})...")

    reason = _recreate_reason(project, mode, cname, container_state, fresh=fresh)
    if reason is not None and mode == "run":
        raise SystemExit(
            f"Container {cname} cannot be resumed ({reason}), and a headless "
            f"container is never recreated in place — starting one replays "
            f"its original prompt.  Create a new task with:\n"
            f'  terok task run {project_name} "<prompt>" --mode headless'
        )

    _validate_restart_preconditions(project, task_id, mode, meta, cname)

    if reason is None:
        if container_state == "running":
            _stop_running_container(project, task_id, mode, cname, meta_path)
        try:
            _start_and_report_restart(project, task_id, mode, cname, meta, meta_path)
            return
        except SystemExit as exc:
            reason = str(exc) or "podman start failed"

    _recreate_in_place(project, task_id, mode, cname, meta, meta_path, reason)


def _recreate_reason(
    project: ProjectConfig, mode: str, cname: str, container_state: str | None, *, fresh: bool
) -> str | None:
    """First reason the resume rung can't be taken, or ``None`` to resume.

    Headless tasks skip the image-drift probe: they have no recreate
    rung (see [`task_restart`][terok.lib.orchestration.task_runners.restart.task_restart]),
    so flagging drift would only turn a working resume into an error.
    """
    if fresh:
        return "--fresh requested"
    if container_state is None:
        return "the container no longer exists"
    if mode == "run":
        return None
    return _image_drift(project, cname)


def _image_drift(project: ProjectConfig, cname: str) -> str | None:
    """Detect a container whose image no longer matches the project image.

    Compares image IDs, not tags: a rebuild moves the tag to a new ID
    while the existing container keeps pointing at the old one — exactly
    the situation where a plain start would resurrect stale tooling.
    When either ID is unavailable the probe abstains rather than block
    the resume.
    """
    runtime = _rt.resolve_runtime(project)
    current = runtime.container(cname).image
    expected = runtime.image(project_cli_image(project.name))
    if current is None or expected is None:
        return None
    current_id, expected_id = current.id, expected.id
    if current_id and expected_id and current_id != expected_id:
        return "the project image was rebuilt since this container was created"
    return None


def _validate_restart_preconditions(
    project: ProjectConfig, task_id: str, mode: str, meta: dict, cname: str
) -> None:
    """Validate what would fail the restart *before* the container is stopped.

    Taking down a working service only to then error out is worse than
    refusing to stop: re-claim the saved web port and rehydrate the toad
    token here, raising ``SystemExit`` if either is no longer viable.
    The port claim also pins the recreate rung to the saved port — the
    registry hands the same claim back to the launch path.
    """
    web_port = meta.get("web_port")
    if isinstance(web_port, int):
        actual = assign_web_port(project.name, task_id, preferred=web_port)
        if actual != web_port:
            release_web_port(project.name, task_id)
            raise SystemExit(
                f"Port {web_port} for {project.name}/{task_id} is no longer available "
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
        project_name=project.name,
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
        project_name=project.name,
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
        _print_login_instructions(project.name, task_id, cname, color_enabled)
    elif mode == "toad":
        port = meta.get("web_port")
        token = meta.get("web_token")
        if isinstance(port, int) and isinstance(token, str):
            url = _toad_browser_url(get_public_host(), port, token)
            print(f"Toad: {_hyperlink(_blue(url, color_enabled), url, enabled=color_enabled)}")


def _recreate_in_place(
    project: ProjectConfig,
    task_id: str,
    mode: str,
    cname: str,
    meta: dict,
    meta_path: Path,
    reason: str,
) -> None:
    """Tear the old container down and relaunch the task into the same name.

    The mode runner's launch path re-reads project config and mints
    fresh tokens; the workspace is reused as-is — with no new-task
    marker the init script fetches without resetting — and the saved
    per-task ``unrestricted`` choice overrides the runner's config
    resolution.
    """
    print(_yellow(f"Recreating container {cname}: {reason}", _supports_color()))
    runtime = _rt.resolve_runtime(project)
    if runtime.container(cname).state == "running":
        _stop_running_container(project, task_id, mode, cname, meta_path)
    if runtime.container(cname).state is not None:
        _sandbox(project).rm([cname])
    unrestricted = meta.get("unrestricted")
    if mode == "cli":
        task_run_cli(project.name, task_id, unrestricted=unrestricted)
    else:  # toad — headless never reaches the recreate rung
        task_run_toad(project.name, task_id, unrestricted=unrestricted)


__all__ = [
    "task_restart",
]
