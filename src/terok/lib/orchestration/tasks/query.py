# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task read models and queries — the ``TaskMeta`` value object plus the
functions that hydrate it from disk and live container state.
"""

from dataclasses import dataclass

from ...core import runtime as _rt
from ...core.projects import load_project
from ...core.task_state import TaskState, container_name, effective_status
from ...core.work_status import read_work_status
from ..container_exec import container_git_diff
from .identity import is_task_id
from .meta import iter_task_ids, read_task_meta, tasks_meta_dir


def get_task_container_state(project_id: str, task_id: str, mode: str | None) -> str | None:
    """Get actual container state for a task (TUI helper)."""
    if not mode:
        return None
    cname = container_name(project_id, mode, task_id)
    return _rt.get_runtime().container(cname).state


def lookup_container_by_pt(project_id: str, task_id: str) -> str | None:
    """Resolve a `project/task` pair to the current container name, or `None`.

    Powers the slash-form identity acceptance on per-container
    ``terok executor *`` verbs (``stop``, future ``exec`` / ``logs`` /
    ``state`` / ``login``).  Reads the recorded mode from the task's
    meta file; returns ``None`` when the task is unknown or has never
    been launched (no ``mode`` recorded).  The caller decides whether
    to treat ``None`` as "pass the input through verbatim" (raw
    container id) or "fail with an actionable error" (unknown
    project/task).
    """
    meta_dir = tasks_meta_dir(project_id)
    raw = read_task_meta(meta_dir, task_id)
    if raw is None:
        return None
    mode = raw.get("mode")
    if not mode:
        return None
    return container_name(project_id, mode, task_id)


@dataclass(kw_only=True)
class TaskMeta(TaskState):
    """Lightweight metadata snapshot for a single task.

    Inherits lifecycle fields (``container_state``, ``exit_code``,
    ``deleting``, ``initialized``) from [`TaskState`][terok.lib.core.task_state.TaskState].
    """

    task_id: str
    project_id: str = ""
    """Project the task belongs to.

    Carried in the meta JSON so consumers that don't already know the
    project (e.g. terok-shield's host-side dossier resolver, which only
    has the meta-path pointer) can render full ``project/task`` identity
    without re-deriving it from the on-disk path.  Empty string for
    pre-this-release meta files; the lifecycle backfills it on the
    next mutation.
    """
    mode: str | None
    workspace: str
    web_port: int | None
    web_token: str | None = None
    backend: str | None = None
    preset: str | None = None
    name: str = ""
    provider: str | None = None
    unrestricted: bool | None = None
    work_status: str | None = None
    work_message: str | None = None
    shield_state: str | None = None
    created_at: str | None = None

    @property
    def status(self) -> str:
        """Compute effective status from live container state + metadata."""
        return effective_status(self)


def _is_initialized(meta: dict) -> bool:
    """Return True if the task has completed first-boot initialisation."""
    return "ready_at" in meta


def get_task_meta(project_id: str, task_id: str) -> TaskMeta:
    """Return metadata for a single task with live container state.

    Hydrates ``container_state`` from the running container so that
    ``TaskMeta.status`` reflects current reality rather than stale YAML.
    Raises ``SystemExit`` if the task metadata file is not found.
    """
    meta_dir = tasks_meta_dir(project_id)
    raw = read_task_meta(meta_dir, task_id)
    if raw is None:
        raise SystemExit(f"Unknown task {task_id}")
    mode = raw.get("mode")
    # Fall back to the caller-known task_id rather than a blank identity
    # when the on-disk record predates the field.
    tid = str(raw.get("task_id") or task_id)
    # Hydrate live container state only for tasks that have actually been started
    live_state: str | None = None
    if mode is not None:
        try:
            cname = container_name(project_id, mode, task_id)
            live_state = _rt.get_runtime().container(cname).state
        except Exception:
            pass
    # Hydrate work status from agent-config (same logic as _get_tasks)
    ws_status: str | None = None
    ws_message: str | None = None
    if tid:
        project = load_project(project_id)
        try:
            agent_cfg = project.tasks_root / tid / "agent-config"
            ws = read_work_status(agent_cfg)
            ws_status = ws.status
            ws_message = ws.message
        except Exception:  # noqa: BLE001 — best-effort; agent-config may not exist yet
            pass
    return TaskMeta(
        task_id=tid,
        # ``or`` (not the dict default) so a migrated record carrying an
        # empty string still falls back to the path-derived project_id.
        project_id=raw.get("project_id") or project_id,
        mode=mode,
        workspace=raw.get("workspace", ""),
        web_port=raw.get("web_port"),
        web_token=raw.get("web_token"),
        backend=raw.get("backend"),
        container_state=live_state,
        exit_code=raw.get("exit_code"),
        deleting=bool(raw.get("deleting")),
        initialized=_is_initialized(raw),
        preset=raw.get("preset"),
        name=raw["name"],
        provider=raw.get("provider"),
        unrestricted=raw.get("unrestricted"),
        work_status=ws_status,
        work_message=ws_message,
        created_at=raw.get("created_at"),
    )


def get_workspace_git_diff(project_id: str, task_id: str, against: str = "HEAD") -> str | None:
    """Get git diff from a task's workspace via container exec.

    Runs ``git diff`` **inside** the task container rather than on the host,
    so that even poisoned git hooks only execute within the container sandbox.

    Args:
        project_id: The project ID
        task_id: The task ID
        against: What to diff against (``"HEAD"`` or ``"PREV"``)

    Returns:
        The git diff output as a string, or ``None`` if failed
    """
    try:
        load_project(project_id)  # validate project exists
        meta_dir = tasks_meta_dir(project_id)
        meta = read_task_meta(meta_dir, task_id)
        if meta is None:
            return None
        mode = meta.get("mode")
        if not mode:
            return None

        if against == "PREV":
            return container_git_diff(project_id, task_id, mode, "HEAD~1", "HEAD")
        return container_git_diff(project_id, task_id, mode, "HEAD")

    except (Exception, SystemExit):
        return None


def _get_tasks(project_id: str, reverse: bool = False) -> list[TaskMeta]:
    """Return all task metadata for *project_id*, sorted by task ID."""
    meta_dir = tasks_meta_dir(project_id)
    tasks: list[TaskMeta] = []
    if not meta_dir.is_dir():
        return tasks
    try:
        project = load_project(project_id)
        tasks_root = project.tasks_root
    except SystemExit:
        tasks_root = None
    for tid_stem in iter_task_ids(meta_dir):
        if not is_task_id(tid_stem):
            continue
        try:
            meta = read_task_meta(meta_dir, tid_stem)
            if meta is None:
                continue
            # Fall back to the meta-dir-derived stem rather than a blank
            # identity when the on-disk record predates the field.
            tid = str(meta.get("task_id") or tid_stem)
            ws_status = None
            ws_message = None
            if tasks_root and tid:
                agent_cfg = tasks_root / tid / "agent-config"
                ws = read_work_status(agent_cfg)
                ws_status = ws.status
                ws_message = ws.message
            mode = meta.get("mode")
            tasks.append(
                TaskMeta(
                    task_id=tid,
                    project_id=meta.get("project_id") or project_id,
                    mode=mode,
                    workspace=meta.get("workspace", ""),
                    web_port=meta.get("web_port"),
                    web_token=meta.get("web_token"),
                    backend=meta.get("backend"),
                    exit_code=meta.get("exit_code"),
                    deleting=bool(meta.get("deleting")),
                    initialized=_is_initialized(meta),
                    preset=meta.get("preset"),
                    name=meta["name"],
                    provider=meta.get("provider"),
                    unrestricted=meta.get("unrestricted"),
                    work_status=ws_status,
                    work_message=ws_message,
                    created_at=meta.get("created_at"),
                )
            )
        except Exception as exc:
            from ...util.logging_utils import log_warning

            log_warning(f"Skipping malformed task metadata file for {tid_stem}: {exc}")
            continue

    tasks.sort(key=lambda t: t.task_id or "", reverse=reverse)
    return tasks


def get_tasks(project_id: str, reverse: bool = False) -> list[TaskMeta]:
    """Return all task metadata for *project_id*, sorted by task ID."""
    return _get_tasks(project_id, reverse=reverse)


def get_all_task_states(
    project_id: str,
    tasks: list[TaskMeta],
) -> dict[str, str | None]:
    """Map each task to its live container state via a single batch query.

    Args:
        project_id: The project whose containers to query.
        tasks: List of ``TaskMeta`` instances (must have ``task_id`` and ``mode``).

    Returns:
        ``{task_id: container_state_or_None}`` dict.
    """
    # container_states isn't on the ContainerRuntime Protocol (only the
    # concrete podman impl) — upstream surface gap; the call works at runtime.
    container_states = _rt.get_runtime().container_states(project_id)  # type: ignore[attr-defined]
    result: dict[str, str | None] = {}
    for t in tasks:
        if t.mode:
            cname = container_name(project_id, t.mode, str(t.task_id))
            result[str(t.task_id)] = container_states.get(cname)
        else:
            result[str(t.task_id)] = None
    return result


def task_list(
    project_id: str,
    *,
    status: str | None = None,
    mode: str | None = None,
    agent: str | None = None,
) -> None:
    """List tasks for a project, optionally filtered by status, mode, or agent preset.

    Status is computed live from podman container state + task metadata.
    """
    tasks = get_tasks(project_id)

    # Pre-filter by mode/agent before the podman query to reduce work
    if mode:
        tasks = [t for t in tasks if t.mode == mode]
    if agent:
        tasks = [t for t in tasks if t.preset == agent]

    if not tasks:
        print("No tasks found")
        return

    # Batch-query podman for all container states in one call
    live_states = get_all_task_states(project_id, tasks)
    for t in tasks:
        t.container_state = live_states.get(t.task_id)

    # Filter by effective status (computed live)
    if status:
        tasks = [t for t in tasks if effective_status(t) == status]

    if not tasks:
        print("No tasks found")
        return

    for t in tasks:
        t_status = effective_status(t)
        extra = []
        if t.mode:
            extra.append(f"mode={t.mode}")
        if t.web_port:
            extra.append(f"port={t.web_port}")
        if t.work_status:
            extra.append(f"work={t.work_status}")
        extra_s = f" [{'; '.join(extra)}]" if extra else ""
        print(f"- {t.task_id}: {t.name} {t_status}{extra_s}")


__all__ = [
    "TaskMeta",
    "get_all_task_states",
    "get_task_container_state",
    "get_task_meta",
    "get_tasks",
    "get_workspace_git_diff",
    "lookup_container_by_pt",
    "task_list",
]
