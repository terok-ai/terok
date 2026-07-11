# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task lifecycle — create, rename, delete, archive-on-delete, stop, login,
and the status display.  These are the state-mutating and interactive
operations, layered on top of the [`meta`][terok.lib.orchestration.tasks.meta]
I/O boundary and the [`query`][terok.lib.orchestration.tasks.query] read models.
"""

import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from terok_util import ensure_dir

from terok.lib.integrations.executor import AgentRunner
from terok.lib.integrations.sandbox import Sandbox, container_diagnostics, remove_container_state

from ...core import runtime as _rt
from ...core.config import make_sandbox_config
from ...core.projects import ProjectConfig, load_project
from ...core.task_display import STATUS_DISPLAY, mode_info
from ...core.task_state import CONTAINER_MODES, container_name, effective_status
from ...core.work_status import read_work_status
from ...util.ansi import (
    green as _green,
    red as _red,
    supports_color as _supports_color,
    yellow as _yellow,
)
from ...util.emoji import render_emoji
from ...util.fs import archive_timestamp, create_archive_dir
from ...util.host_cmd import WORKSPACE_DANGEROUS_DIRNAME
from ...util.logging_utils import _log_debug
from .identity import _generate_unique_id
from .meta import (
    _atomic_write,
    _to_plain,
    dossier_path,
    iter_task_ids,
    meta_path,
    read_task_meta,
    tasks_archive_dir,
    tasks_meta_dir,
    write_task_meta,
)
from .naming import generate_task_name, sanitize_task_name, validate_task_name
from .query import TaskMeta, _is_initialized


def _write_task_readme(task_dir: Path) -> None:
    """Write a README.md explaining the task directory layout and security."""
    readme = task_dir / "README.md"
    readme.write_text(
        "# Task Directory\n"
        "\n"
        "## workspace-dangerous/\n"
        "\n"
        "This directory contains a git repository checked out from the project\n"
        "source. It is mounted into the task container at `/workspace`.\n"
        "\n"
        "**WARNING: Do not execute code or run git commands in this directory\n"
        "from the host.** The container has full write access and could have\n"
        "rewritten git hooks, checked in malicious scripts, or otherwise\n"
        "poisoned the repository contents.\n"
        "\n"
        "The safe way to interact with the repository is through the **git\n"
        "gate** — a separate, host-controlled bare repo that agents push to.\n"
        "Even online-mode agents can be instructed to mirror their work to\n"
        "the gate.\n",
        encoding="utf-8",
    )


def _task_new(project: ProjectConfig, *, name: str | None = None) -> str:
    """Create a new task with a fresh workspace.  Returns the task ID."""
    if name is not None:
        task_name = sanitize_task_name(name)
        if task_name is None:
            raise SystemExit(f"Invalid task name: {name!r}")
        err = validate_task_name(task_name)
        if err:
            raise SystemExit(f"Invalid task name: {err}")
    else:
        task_name = generate_task_name(project.name)
    tasks_root = project.tasks_root
    ensure_dir(tasks_root)
    meta_dir = tasks_meta_dir(project.name)
    ensure_dir(meta_dir)

    existing = set(iter_task_ids(meta_dir))
    next_id = _generate_unique_id(existing)

    ws = tasks_root / next_id
    ensure_dir(ws)

    workspace_dir = ws / WORKSPACE_DANGEROUS_DIRNAME
    ensure_dir(workspace_dir)
    workspace_dir.chmod(0o700)
    marker_path = workspace_dir / ".new-task-marker"
    marker_path.write_text(
        "# This marker signals that the workspace should be reset to the latest remote HEAD.\n"
        "# It is created by 'terok task new' and removed by init-ssh-and-repo.sh"
        " after reset.\n"
        "# If you see this file in an initialized workspace, something went wrong.\n",
        encoding="utf-8",
    )

    _write_task_readme(ws)

    meta = {
        "project_name": project.name,
        "task_id": next_id,
        "name": task_name,
        "mode": None,
        "workspace": str(ws),
        "web_port": None,
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    write_task_meta(dossier_path(meta_dir, next_id), meta)
    print(f"Created task {next_id} ({task_name}) in {ws}")
    return next_id


def task_new(project_name: str, *, name: str | None = None) -> str:
    """Create a new task with a fresh workspace for a project.

    Args:
        project_name: The project to create the task under.
        name: Optional human-readable name.  Allowed characters are
            lowercase letters, digits, hyphens, and underscores.
            If ``None``, a random slug-style name is generated via
            [`generate_task_name`][terok.lib.orchestration.tasks.generate_task_name].

    Workspace Initialization Protocol:
    ----------------------------------
    Each task gets its own workspace directory that persists across container
    runs. When a container starts, the init script (init-ssh-and-repo.sh) needs
    to know whether this is:

    1. A NEW task that should be reset to the latest remote HEAD
    2. A RESTARTED task where local changes should be preserved

    We use a marker file (.new-task-marker) to signal intent:

    - task_new() creates the marker in the workspace directory
    - init-ssh-and-repo.sh checks for the marker:
      - If marker exists: reset to origin/HEAD, then delete marker
      - If no marker: fetch only, preserve local state
    - Subsequent container runs on the same task won't see the marker,
      so local work is preserved

    This handles edge cases like:
    - Stale workspace from incompletely deleted previous task with same ID
    - Ensuring new tasks always start with latest code
    """
    return _task_new(load_project(project_name), name=name)


def _task_rename(project: ProjectConfig, task_id: str, new_name: str) -> None:
    """Rename a task by updating its metadata file."""
    meta_dir = tasks_meta_dir(project.name)
    meta = read_task_meta(meta_dir, task_id)
    if meta is None:
        raise SystemExit(f"Unknown task {task_id}")
    sanitized = sanitize_task_name(new_name)
    if sanitized is None:
        raise SystemExit(f"Invalid task name: {new_name!r}")
    err = validate_task_name(sanitized)
    if err:
        raise SystemExit(f"Invalid task name: {err}")
    meta["name"] = sanitized
    write_task_meta(dossier_path(meta_dir, task_id), meta)
    print(f"Renamed task {task_id} to {sanitized}")


def task_rename(project_name: str, task_id: str, new_name: str) -> None:
    """Rename a task by updating its metadata file.

    Sanitizes *new_name* and writes the result to the task's metadata file.
    Raises ``SystemExit`` if the task is unknown or the sanitized name is invalid.
    """
    _task_rename(load_project(project_name), task_id, new_name)


def capture_task_logs(project: ProjectConfig | str, task_id: str, mode: str) -> Path | None:
    """Capture container logs to the task's ``logs/`` directory on the host.

    Writes stdout/stderr from ``podman logs`` to
    ``<tasks_root>/<task_id>/logs/container.log``.  Returns the log file
    path on success, or ``None`` if the container doesn't exist or podman
    fails.

    *project* may be a [`ProjectConfig`][terok.cli.commands.sickbay.ProjectConfig] or a project name string
    (the string form loads the config internally for backward compat).
    """
    if isinstance(project, str):
        project = load_project(project)
    task_dir = project.tasks_root / str(task_id)
    logs_dir = task_dir / "logs"
    ensure_dir(logs_dir)
    log_file = logs_dir / "container.log"

    cname = container_name(project.name, mode, task_id)
    if AgentRunner().capture_logs(cname, log_file, timestamps=True, timeout=60.0):
        return log_file
    return None


def _archive_task(project: ProjectConfig, task_id: str, meta: dict) -> Path | None:
    """Archive task metadata and logs before deletion.

    Creates an entry at ``archive/<project_name>/tasks/<ts>_<task_id>[_<name>]/``
    containing the task metadata YAML and any captured logs.  The archival
    timestamp is the primary identifier because task numbers and names can
    be reused after deletion.

    Returns the archive directory path, or ``None`` if archiving failed.
    """
    try:
        task_name = meta.get("name", "")
        ts = archive_timestamp()
        # Build archive dir name: timestamp_taskid_name (name may be empty)
        dir_name = f"{ts}_{task_id}"
        if task_name:
            dir_name = f"{dir_name}_{task_name}"

        archive_root = tasks_archive_dir(project.name)
        archive_dir = create_archive_dir(archive_root, dir_name)

        # Save the *merged* meta as a single self-contained JSON snapshot.
        # The live-task layout splits dossier (JSON) from state (YAML)
        # because each file has a different audience; an archive entry
        # has only one audience (the operator looking at history) and is
        # never re-read by shield, so the split brings no value here.
        _atomic_write(
            archive_dir / "task.json",
            json.dumps(_to_plain(meta), indent=2, ensure_ascii=False, default=str) + "\n",
        )

        # Copy logs if they exist
        task_dir = project.tasks_root / str(task_id)
        logs_dir = task_dir / "logs"
        if logs_dir.is_dir():
            archive_logs_dir = archive_dir / "logs"
            shutil.copytree(logs_dir, archive_logs_dir, dirs_exist_ok=True)

        _log_debug(f"_archive_task: archived task {task_id} to {archive_dir}")
        return archive_dir
    except Exception as e:
        _log_debug(f"_archive_task: failed to archive task {task_id}: {e}")
        return None


@dataclass
class TaskDeleteResult:
    """Outcome of a task deletion — always completes, collects warnings.

    The task is considered deleted regardless (metadata and workspace
    removed), but individual cleanup steps may fail.  ``warnings``
    carries human-readable descriptions of any steps that did not
    complete cleanly.
    """

    task_id: str
    warnings: list[str]


def _remove_task_containers(project_name: str, task_id: str, warnings: list[str]) -> bool:
    """Remove every mode's container for the task via [`Sandbox.rm`][terok_sandbox.Sandbox.rm].

    Returns ``True`` only when every container was removed cleanly;
    a raised error or per-container failure is collected into *warnings*
    and yields ``False``.
    """
    names = [container_name(project_name, mode, str(task_id)) for mode in CONTAINER_MODES]
    project = load_project(project_name)
    runtime = _rt.resolve_runtime(project)
    try:
        rm_results = Sandbox(make_sandbox_config(), runtime=runtime).rm(names)
    except Exception as exc:
        _log_debug(f"task_delete: stop_task_containers raised: {exc}")
        warnings.append(f"Container removal failed: {exc}")
        return False
    removed = True
    for r in rm_results:
        if not r.removed:
            _log_debug(f"task_delete: container {r.name} not removed: {r.error}")
            warnings.append(f"Container {r.name}: {r.error}")
            removed = False
    return removed


def _remove_workspace(workspace: Path, warnings: list[str]) -> None:
    """Remove the task workspace directory; record a warning on failure."""
    if not workspace.is_dir():
        return
    _log_debug("task_delete: removing workspace directory")
    try:
        shutil.rmtree(workspace)
        _log_debug("task_delete: workspace directory removed")
    except Exception as exc:
        _log_debug(f"task_delete: workspace removal failed: {exc}")
        warnings.append(f"Workspace removal failed: {exc}")


def _remove_meta_files(paths: tuple[Path, ...], warnings: list[str]) -> None:
    """Unlink the on-disk meta-file pair.

    Each path is unlinked independently so a failure on one (e.g. a
    permission error) still lets the rest be cleaned up.
    """
    if not any(p.is_file() for p in paths):
        return
    _log_debug("task_delete: removing metadata files")
    all_removed = True
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except Exception as exc:
            _log_debug(f"task_delete: metadata removal failed for {p}: {exc}")
            warnings.append(f"Metadata removal failed for {p}: {exc}")
            all_removed = False
    if all_removed:
        _log_debug("task_delete: metadata files removed")


def _sweep_container_state(project_name: str, task_id: str, warnings: list[str]) -> None:
    """Remove each mode-container's sidecar + per-container runtime dir.

    The supervisor sidecar deliberately survives container stops — a
    stopped container must come back supervised on ``podman start`` —
    so task delete is the real teardown point where it goes, via
    [`remove_container_state`][terok_sandbox.launch.remove_container_state].
    Best-effort like every other delete step; only called once the
    containers are gone (a live container still needs its sidecar).
    """
    cfg = make_sandbox_config()
    for mode in CONTAINER_MODES:
        cname = container_name(project_name, mode, str(task_id))
        try:
            remove_container_state(cname, cfg=cfg)
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            _log_debug(f"task_delete: container state sweep failed for {cname}: {exc}")
            warnings.append(f"Container state sweep failed for {cname}: {exc}")


def _release_task_web_port(
    project_name: str, task_id: str, containers_removed: bool, warnings: list[str]
) -> None:
    """Release the task's claimed web port — only safe once its containers are gone."""
    if not containers_removed:
        warnings.append("Web port kept claimed — containers may still be running")
        return
    _log_debug("task_delete: releasing web port")
    try:
        from ..ports import release_web_port

        release_web_port(project_name, task_id)
    except Exception as exc:  # noqa: BLE001 — best-effort cleanup
        _log_debug(f"task_delete: web port release failed: {exc}")
        warnings.append(f"Web port release failed: {exc}")


def _task_delete(project: ProjectConfig, task_id: str) -> TaskDeleteResult:
    """Delete a task's workspace, metadata, and associated containers.

    Always completes — each cleanup step is independent and best-effort,
    collecting any failure into the returned warnings rather than
    aborting the rest of the teardown.
    """
    _log_debug(f"task_delete: start project_name={project.name} task_id={task_id}")
    warnings: list[str] = []

    workspace = project.tasks_root / str(task_id)
    meta_dir = tasks_meta_dir(project.name)
    dossier_file = dossier_path(meta_dir, task_id)
    meta_file = meta_path(meta_dir, task_id)
    _log_debug(
        f"task_delete: workspace={workspace} dossier_file={dossier_file} meta_file={meta_file}"
    )

    meta = read_task_meta(meta_dir, task_id) or {}
    mode = meta.get("mode")

    if mode:
        _log_debug("task_delete: capturing container logs")
        capture_task_logs(project, task_id, mode)
    if meta:
        _log_debug("task_delete: archiving task")
        _archive_task(project, task_id, meta)

    # At request time the gate token is a stateless pair-match between
    # the container's env and the supervisor sidecar.  *Successful*
    # removal below kills both live ends (env + supervisor); the
    # sidecar's on-disk copy is swept right after — it must outlive mere
    # stops so restarts come back supervised, so delete is where it
    # actually goes.  The task meta's ``gate_token`` (the durable mint
    # record) leaves with the task metadata at the end of this delete.
    _log_debug("task_delete: removing task containers")
    containers_removed = _remove_task_containers(project.name, task_id, warnings)
    if containers_removed:
        _sweep_container_state(project.name, task_id, warnings)
    else:
        # Removal failed, so the supervisor — and the in-memory gate token it
        # holds — may still be live even as the rest of the delete proceeds.
        # No host-side store can revoke it (the task meta only records the
        # value for minting reuse; the gate consults the supervisor's copy),
        # so surface it loudly: the operator must stop the container manually
        # or run `terok panic` to invalidate the token.
        warnings.append(
            "Container removal failed — its supervisor may still be live and "
            "holding a valid gate token; stop the container manually or run "
            "`terok panic` to invalidate it."
        )

    if mode:
        from ..hooks import run_hook

        run_hook(
            "post_stop",
            project.hook_post_stop,
            project_name=project.name,
            task_id=task_id,
            mode=mode,
            cname=container_name(project.name, mode, task_id),
            task_dir=workspace,
            meta_path=meta_file,
        )

    _remove_workspace(workspace, warnings)
    _remove_meta_files((dossier_file, meta_file), warnings)
    _release_task_web_port(project.name, task_id, containers_removed, warnings)

    _log_debug("task_delete: finished")
    return TaskDeleteResult(task_id=task_id, warnings=warnings)


def task_delete(project_name: str, task_id: str) -> TaskDeleteResult:
    """Delete a task's workspace, metadata, and any associated containers.

    Before removal, captures container logs and archives the task metadata
    and logs to ``archive/<project_name>/tasks/``.  Containers are stopped
    best-effort via podman using the ``<project.name>-<mode>-<task_id>``
    naming scheme.  Returns a [`TaskDeleteResult`][terok.lib.orchestration.tasks.TaskDeleteResult] so the caller can
    present any warnings from cleanup steps that failed.
    """
    return _task_delete(load_project(project_name), task_id)


def _validate_login(project: ProjectConfig, task_id: str) -> tuple[str, str]:
    """Validate that a task exists and its container is running.

    Returns ``(container_name, mode)`` on success.
    Raises ``SystemExit`` with actionable messages on failure.
    """
    meta_dir = tasks_meta_dir(project.name)
    meta = read_task_meta(meta_dir, task_id)
    if meta is None:
        raise SystemExit(f"Unknown task {task_id}")

    mode = meta.get("mode")
    if not mode:
        raise SystemExit(
            f"Task {task_id} has never been run (no mode set).\n"
            f"  Start a fresh task: terok task run {project.name}\n"
            f"  Or run this stub:   terokctl task attach {project.name} {task_id} --mode cli"
        )

    cname = container_name(project.name, mode, task_id)
    state = _rt.resolve_runtime(project).container(cname).state
    if state is None:
        raise SystemExit(
            f"Container {cname} does not exist. "
            f"Run 'terok task restart {project.name} {task_id}' first."
        )
    if state != "running":
        raise SystemExit(
            f"Container {cname} is not running (state: {state}). "
            f"Run 'terok task restart {project.name} {task_id}' first."
        )
    return cname, mode


def _get_login_command(project: ProjectConfig, task_id: str) -> list[str]:
    """Return the command to interactively log into a task container.

    Routes through the per-project runtime so the returned argv matches
    the backend that booted the container — under crun it's the
    familiar ``podman exec -it`` form; under krun it's an SSH invocation
    (``ssh -tt -p <host_port> -i … dev@127.0.0.1``) over the per-task
    TCP port podman's passt has forwarded into the guest, because
    ``podman exec`` can't enter a krun guest.
    """
    cname, _mode = _validate_login(project, task_id)
    return _rt.resolve_runtime(project).container(cname).login_command()


def _task_login(project: ProjectConfig, task_id: str) -> None:
    """Open an interactive shell in a running task container."""
    cmd = _get_login_command(project, task_id)
    try:
        os.execvp(cmd[0], cmd)
    except FileNotFoundError:
        raise SystemExit(
            f"'{cmd[0]}' not found on PATH. Please install podman or add it to your PATH."
        )


def _task_stop(project: ProjectConfig, task_id: str, *, timeout: int | None = None) -> None:
    """Gracefully stop a running task container."""
    effective_timeout = timeout if timeout is not None else project.shutdown_timeout
    meta_dir = tasks_meta_dir(project.name)
    meta = read_task_meta(meta_dir, task_id)
    if meta is None:
        raise SystemExit(f"Unknown task {task_id}")
    meta_file = meta_path(meta_dir, task_id)

    mode = meta.get("mode")
    if not mode:
        raise SystemExit(f"Task {task_id} has never been run (no mode set)")

    cname = container_name(project.name, mode, task_id)
    runtime = _rt.resolve_runtime(project)

    state = runtime.container(cname).state
    if state is None:
        raise SystemExit(f"Task {task_id} container does not exist")
    if state not in ("running", "paused"):
        raise SystemExit(f"Task {task_id} container is not stoppable (state: {state})")

    try:
        runtime.container(cname).stop(timeout=effective_timeout)
    except FileNotFoundError:
        raise SystemExit("podman not found; please install podman")
    except RuntimeError as exc:
        raise SystemExit(f"Failed to stop container: {exc}")

    try:
        from ..ports import release_web_port

        release_web_port(project.name, task_id)
    except Exception:  # noqa: BLE001 — best-effort; container is already stopped
        pass

    from ..hooks import run_hook

    run_hook(
        "post_stop",
        project.hook_post_stop,
        project_name=project.name,
        task_id=task_id,
        mode=mode,
        cname=cname,
        task_dir=project.tasks_root / str(task_id),
        meta_path=meta_file,
    )

    color_enabled = _supports_color()
    print(f"Stopped task {task_id}: {_green(cname, color_enabled)}")
    print(f"Restart with: terok task restart {project.name} {task_id}")


def get_login_command(project_name: str, task_id: str) -> list[str]:
    """Return the podman exec command to log into a task container."""
    return _get_login_command(load_project(project_name), task_id)


def task_login(project_name: str, task_id: str) -> None:
    """Open an interactive shell in a running task container."""
    _task_login(load_project(project_name), task_id)


def task_stop(project_name: str, task_id: str, *, timeout: int | None = None) -> None:
    """Gracefully stop a running task container.

    Uses ``podman stop --time <N>`` to give the container *timeout* seconds
    before SIGKILL.  When *timeout* is ``None`` the project's
    ``run.shutdown_timeout`` setting is used (default 10 s).
    """
    _task_stop(load_project(project_name), task_id, timeout=timeout)


def task_status(project_name: str, task_id: str, *, verbose: bool = False) -> None:
    """Show live task status with container state diagnostics.

    With *verbose*, append the on-host debug locations — container ID,
    bind mounts, and the supervisor log / wrapper / PID / sidecar paths
    — so an operator can point a human at the right file to send back
    instead of hand-rolling a ``podman inspect`` incantation.
    """
    project = load_project(project_name)
    meta_dir = tasks_meta_dir(project.name)
    meta = read_task_meta(meta_dir, task_id)
    if meta is None:
        raise SystemExit(f"Unknown task {task_id}")

    mode = meta.get("mode")
    web_port = meta.get("web_port")
    exit_code = meta.get("exit_code")

    color_enabled = _supports_color()

    # Query live container state
    cname = None
    container = None
    cs = None
    if mode:
        cname = container_name(project.name, mode, task_id)
        container = _rt.resolve_runtime(project).container(cname)
        cs = container.state

    # Build TaskMeta for effective_status / mode_emoji computation
    task = TaskMeta(
        task_id=task_id,
        mode=mode,
        workspace=meta.get("workspace", ""),
        web_port=web_port,
        web_token=meta.get("web_token"),
        backend=meta.get("backend"),
        exit_code=exit_code,
        deleting=bool(meta.get("deleting")),
        initialized=_is_initialized(meta),
        container_state=cs,
        name=meta["name"],
        agent=meta.get("agent"),
        unrestricted=meta.get("unrestricted"),
        created_at=meta.get("created_at"),
    )
    status = effective_status(task)
    info = STATUS_DISPLAY.get(status, STATUS_DISPLAY["created"])

    status_color = {"green": _green, "yellow": _yellow, "red": _red}.get(info.color, _yellow)
    m = mode_info(task.mode)
    m_emoji = render_emoji(m)

    print(f"Task {task_id}:")
    print(f"  Name:            {task.name}")
    print(f"  Status:          {render_emoji(info)} {status_color(info.label, color_enabled)}")
    print(f"  Mode:            {m_emoji} {m.label or 'not set'}")
    if cname:
        print(f"  Container:       {cname}")
    if cs:
        state_color = _green if cs == "running" else _yellow
        print(f"  Container state: {state_color(cs, color_enabled)}")
    elif mode:
        print(f"  Container state: {_red('not found', color_enabled)}")
    if task.unrestricted is not None:
        perm_label = "unrestricted" if task.unrestricted else "restricted"
        print(f"  Permissions:     {perm_label}")
    if exit_code is not None:
        print(f"  Exit code:       {exit_code}")
    if web_port:
        print(f"  Web port:        {web_port}")
    # Work status from agent
    tasks_root = project.tasks_root
    agent_cfg = tasks_root / task_id / "agent-config"
    ws = read_work_status(agent_cfg)
    if ws.status:
        print(f"  Work status:     {ws.status}")
        if ws.message:
            print(f"  Work message:    {ws.message}")

    if verbose:
        cid = container.id if container is not None else None
        mounts = container.mounts if container is not None else []
        _print_status_diagnostics(project_name, task_id, cname, cid, mounts)


def _print_status_diagnostics(
    project_name: str,
    task_id: str,
    cname: str | None,
    cid: str | None,
    mounts: list[tuple[str, str]],
) -> None:
    """Append the on-host debug locations for ``task status --verbose``.

    Resolves the supervisor/sidecar artifact paths through sandbox's
    [`container_diagnostics`][terok_sandbox.diagnostics.container_diagnostics]
    so the layout stays sandbox-owned.  The log and PID file key on the
    podman container *ID* (read live); the sidecar keys on the container
    *name*, so it — and the install-global wrapper — resolve even when
    the container has been removed and no ID is available.
    """

    def row(label: str, value: str) -> None:
        """Print one ``label: value`` line, 17-wide to match the main status body."""
        print(f"  {label + ':':<17}{value}")

    print()
    print("  ── Debug locations ──")
    if cname is None:
        print("  (no container — task has no run mode recorded)")
        return

    # ``Container`` itself is already printed in the main body above; start
    # the debug block with what's new (the full ID).
    row("Container ID", cid or "(not found — container removed)")
    if mounts:
        print("  Mounts:")
        for src, dest in mounts:
            print(f"    {src} → {dest}")

    diag = container_diagnostics(cid or "", cname)
    row("Sidecar", _path_with_presence(diag.sidecar))
    row("Wrapper", str(diag.wrapper))
    if cid:
        row("Supervisor log", _path_with_presence(diag.log))
        row("Supervisor PID", _path_with_presence(diag.pid))
    else:
        row("Supervisor log", "(needs a live or exited container to resolve the ID)")

    row("Live logs", f"terok task logs {project_name} {task_id}")


def _path_with_presence(path: Path) -> str:
    """Render *path*, flagging when the file isn't on disk yet."""
    return str(path) if path.exists() else f"{path}  (not present)"


def wait_for_container_exit(
    container_name: str,
    project_name: str,
    task_id: str,
    timeout: int = 7200,
) -> tuple[int | None, str | None]:
    """Wait for *container_name* to exit and record its code in task metadata.

    Returns ``(exit_code, error_message)``.  On a successful wait
    *error_message* is ``None`` and the real exit code is persisted
    — including a legitimate exit code of 124, which is no longer
    conflated with the watcher's own timeout.  On timeout *exit_code*
    is ``None`` and the error message describes it.
    """
    from .meta import update_task_exit_code

    try:
        exit_code = AgentRunner().wait_for_exit(container_name, timeout=float(timeout))
    except TimeoutError:
        return None, "Watcher timed out"
    except Exception as e:
        return None, str(e)

    update_task_exit_code(project_name, task_id, exit_code)
    return exit_code, None


__all__ = [
    "TaskDeleteResult",
    "capture_task_logs",
    "get_login_command",
    "task_delete",
    "task_login",
    "task_new",
    "task_rename",
    "task_status",
    "task_stop",
    "wait_for_container_exit",
]
