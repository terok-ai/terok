# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The make-it-running ladder for task containers.

Two entry points share one ladder: ``task_restart`` (stop if running,
then start) and ``ensure_task_running`` (report if running, otherwise
start).  The start itself resumes the existing container when possible
and recreates it in place through the launch-only mode runners when
not.
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
    container in place — kept as-is even when the project image was
    rebuilt underneath it, so a long-running task keeps its in-container
    state.  A stale image is only *warned* about, not acted on (see
    ``_warn_if_stale_image``).
    When the resume rung is gone — the container no longer exists or
    podman refuses to start it — recreate the container through the
    normal launch path: same task and container name, workspace reused
    as-is (never re-seeded), project config re-read, the task's
    persistent gate token reused (the workspace's origin URL embeds it,
    so it must survive the recreate), per-task settings carried over
    from the saved metadata.

    *fresh* skips straight to the recreate rung — the explicit
    "recreate + restart" that picks up a rebuilt image, upgrading a task
    that a plain restart deliberately left on its old image.

    Headless tasks (mode ``run``) only ever take the resume rung:
    recreating one would replay its original prompt against the
    workspace.
    """
    _make_running(project_name, task_id, bounce=True, fresh=fresh)


def ensure_task_running(
    project_name: str,
    task_id: str,
    *,
    mode: str | None = None,
    unrestricted: bool | None = None,
) -> None:
    """Bring a task to running without bouncing it.

    The attach flavor of the restart ladder: already running → report
    how to reach it; stopped → resume; container gone → launch through
    the mode runner.  *mode* overrides the task's recorded mode — an
    attach picks the interface, and a first attach on a fresh task
    records it.  *unrestricted* seeds a launch; a resume keeps the
    existing container as-is.
    """
    _make_running(
        project_name,
        task_id,
        bounce=False,
        fresh=False,
        mode_override=mode,
        unrestricted=unrestricted,
    )


def _make_running(
    project_name: str,
    task_id: str,
    *,
    bounce: bool,
    fresh: bool,
    mode_override: str | None = None,
    unrestricted: bool | None = None,
) -> None:
    """The shared ladder behind both entry points.

    *bounce* is the only fork: a restart stops a running container first,
    an ensure reports it and returns.  Everything below — resume, the
    recreate fallbacks, the headless refusal — is identical.
    """
    project = load_project(project_name)
    meta, meta_path = load_task_meta(project.name, task_id)

    mode = mode_override or meta.get("mode")
    if not mode:
        raise SystemExit(f"Task {task_id} has never been run (no mode set)")

    cname = container_name(project.name, mode, task_id)
    container_state = _rt.resolve_runtime(project).container(cname).state

    if not bounce and container_state == "running":
        # Ensure never disturbs a live container — stale image or not.
        _validate_restart_preconditions(project, task_id, mode, meta, cname)
        print(f"Task {task_id} is already running: {_green(cname, _supports_color())}")
        _print_reach_footer(project, task_id, mode, cname, meta)
        return

    if bounce:
        print(f"Restarting task {project_name}/{task_id} ({mode})...")

    reason = _recreate_reason(container_state, fresh=fresh)

    if container_state is not None:
        _validate_restart_preconditions(project, task_id, mode, meta, cname)
    elif isinstance(meta.get("web_port"), int):
        # Best-effort pin: keep the task's port stable across a recreate
        # when it is still free; the launch path reuses the claim.  A
        # lost port is no reason to refuse — the relaunch just moves.
        assign_web_port(project.name, task_id, preferred=meta["web_port"])

    if reason is None:
        if container_state == "running":
            _stop_running_container(project, task_id, mode, cname, meta_path)
        try:
            _start_and_report_restart(project, task_id, mode, cname, meta, meta_path)
            return
        except SystemExit as exc:
            reason = str(exc) or "podman start failed"

    _recreate_in_place(
        project, task_id, mode, cname, meta, meta_path, reason, unrestricted=unrestricted
    )


def _recreate_reason(container_state: str | None, *, fresh: bool) -> str | None:
    """First reason the resume rung can't be taken, or ``None`` to resume.

    Image drift is deliberately *not* a reason: a plain restart keeps a
    long-running task's container as-is and only warns about the stale
    image (see ``_warn_if_stale_image``).
    Picking up a rebuilt image is the explicit job of *fresh* — the
    "recreate + restart" the caller asked for.
    """
    if fresh:
        return "recreate requested"
    if container_state is None:
        return "the container no longer exists"
    return None


def _warn_if_stale_image(project: ProjectConfig, task_id: str, mode: str, cname: str) -> None:
    """Print a highlighted warning when a resumed container's image is stale.

    A plain restart keeps the existing container even when the project
    image was rebuilt underneath it — long-running tasks depend on their
    in-container state and prefer stability over an upgrade.  This makes
    the reuse a *visible* choice rather than silent staleness: it names
    the drift and points at the recreate + restart that would pick up the
    new image.  Headless tasks (mode ``run``) are skipped — they have no
    recreate rung to point at.
    """
    if mode == "run":
        return
    drift = _image_drift(project, cname)
    if drift is None:
        return
    print(
        _yellow(
            f"Warning: task {task_id} restarted on an OUTDATED image ({drift}).\n"
            f"  The container was reused as-is.  Recreate + restart to upgrade it "
            f"to the rebuilt image (TUI: R;  CLI: terok task restart --recreate).",
            _supports_color(),
        )
    )


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
    project: ProjectConfig,
    task_id: str,
    mode: str,
    cname: str,
    meta_path: Path,
    *,
    timeout: int | None = None,
    fatal: bool = True,
) -> None:
    """Stop the running task container, then fire its ``post_stop`` hook.

    *timeout* overrides the project's graceful-shutdown grace period.
    With *fatal* (the default) a failed stop aborts via ``SystemExit``
    — right for the resume rung, which wants this same container back.
    The recreate rung passes ``fatal=False``: the container is
    force-removed right after, so a wedged stop is worth a warning,
    not an abort.
    """
    try:
        _rt.resolve_runtime(project).container(cname).stop(
            timeout=project.shutdown_timeout if timeout is None else timeout
        )
    except FileNotFoundError:
        raise SystemExit("podman not found; please install podman")
    except RuntimeError as exc:
        if fatal:
            raise SystemExit(f"Failed to stop container: {exc}")
        print(_yellow(f"Stop did not finish cleanly ({exc}); removing anyway", _supports_color()))
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

    _warn_if_stale_image(project, task_id, mode, cname)
    print(f"Restarted task {task_id}: {_green(cname, _supports_color())}")
    _print_reach_footer(project, task_id, mode, cname, meta)


def _print_reach_footer(
    project: ProjectConfig, task_id: str, mode: str, cname: str, meta: dict
) -> None:
    """Print how to reach a running container: login command or toad URL."""
    color_enabled = _supports_color()
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
    *,
    unrestricted: bool | None = None,
) -> None:
    """Tear the old container down and relaunch the task into the same name.

    The mode runner's launch path re-reads project config and reuses the
    task's persistent gate token; the workspace is reused as-is — with no
    new-task marker the init script fetches without resetting — and the task's
    ``unrestricted`` choice (caller override, else saved metadata)
    overrides the runner's config resolution.

    The headless refusal lives here, next to the teardown it guards:
    every path that could destroy a container — pre-checked reasons and
    the resume-failure fallback alike — must refuse *before* anything
    is removed, or a headless container would be torn down with nothing
    relaunched into its place.
    """
    if mode == "run":
        raise SystemExit(
            f"Container {cname} cannot be resumed ({reason}), and a headless "
            f"container is never recreated in place — starting one replays "
            f"its original prompt.  Create a new task with:\n"
            f'  terok task run {project.name} "<prompt>" --mode headless'
        )

    print(_yellow(f"Recreating container {cname}: {reason}", _supports_color()))
    runtime = _rt.resolve_runtime(project)
    if runtime.container(cname).state == "running":
        # The container is destroyed on the next line — the grace
        # period would be pure latency, so kill outright, and let the
        # force-remove clean up after a stop that failed anyway.
        _stop_running_container(project, task_id, mode, cname, meta_path, timeout=0, fatal=False)
    if runtime.container(cname).state is not None:
        _sandbox(project).rm([cname])
    if unrestricted is None:
        unrestricted = meta.get("unrestricted")
    # Recreation rewrites the sidecar from scratch, so debug mode is not
    # inherited from the old one — carry the task's saved choice forward
    # so a recreated container keeps its relaxed hardening (and its badge).
    debug = bool(meta.get("debug"))
    if mode == "cli":
        task_run_cli(project.name, task_id, unrestricted=unrestricted, debug=debug)
    else:  # toad — the refusal above keeps mode binary here
        task_run_toad(project.name, task_id, unrestricted=unrestricted, debug=debug)


__all__ = [
    "ensure_task_running",
    "task_restart",
]
