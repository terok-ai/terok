# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task lifecycle state — pure domain, no presentation.

Holds the [`TaskState`][terok.lib.core.task_state.TaskState] value object,
the [`effective_status`][terok.lib.core.task_state.effective_status]
computation, container-name conventions, and project-level capability
queries that ``orchestration`` and ``domain`` modules need without
pulling in display tables.

The presentation-layer lookup tables (emoji, colors, labels) live in
[`task_display`][terok.lib.core.task_display].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..util.yaml import YAMLError, load as _yaml_load

# ── Value object ───────────────────────────────────────────────────────


@dataclass
class TaskState:
    """Container lifecycle state used for display status computation.

    Orchestration-level ``TaskMeta`` inherits from this to add identity,
    configuration, and runtime metadata fields.
    """

    container_state: str | None = None
    exit_code: int | None = None
    deleting: bool = False
    initialized: bool = False
    # UI-only flag the TUI flips while a launch worker is in flight but
    # podman has not yet created the container.  Bridges the gap between
    # "task created" and "container running (init)" so users see ⏳
    # instead of an ambiguous 🆕.
    starting: bool = False


# ── Effective status ───────────────────────────────────────────────────


def effective_status(task: TaskState) -> str:
    """Compute the display status from task lifecycle state.

    Reads the following fields from a ``TaskState`` instance:

    - ``container_state`` (str | None): live podman state, or None
    - ``exit_code`` (int | None): process exit code, or None
    - ``deleting`` (bool): persisted to YAML before deletion starts
    - ``initialized`` (bool): True once ``ready_at`` is persisted to YAML

    Returns one of: ``"deleting"``, ``"running"``, ``"init"``,
    ``"starting"``, ``"stopped"``, ``"completed"``, ``"failed"``,
    ``"created"``, ``"not found"``.
    """
    if task.deleting:
        return "deleting"

    cs = task.container_state

    if cs == "running":
        return "running" if task.initialized else "init"

    if cs is not None:
        return _exit_code_status(task.exit_code) or "stopped"

    # No container yet — ``starting`` fills the launch-worker gap
    # before podman has created the container.  Once it's up, the
    # ``cs == "running"`` branch above takes over with ``init``.
    if task.starting:
        return "starting"
    if not task.initialized:
        return "created"
    return _exit_code_status(task.exit_code) or "not found"


def _exit_code_status(exit_code: int | None) -> str | None:
    """Map an exit code to a terminal status, or ``None`` if not terminal."""
    if exit_code is None:
        return None
    return "completed" if exit_code == 0 else "failed"


# ── Container naming ───────────────────────────────────────────────────

CONTAINER_MODES = ("cli", "web", "run", "toad")
"""All valid container mode suffixes used in container naming."""


def container_name(project_name: str, mode: str, task_id: str) -> str:
    """Return the canonical container name for a task."""
    return f"{project_name}-{mode}-{task_id}"


# ── Project queries ────────────────────────────────────────────────────


def has_gpu(project: Any) -> bool:
    """True when the project's ``project.yml`` opts into GPU passthrough.

    Accepts any object with a ``root`` attribute pointing to the project
    directory (typically a ``Project`` instance).  Returns ``False`` on
    any I/O or parse error.
    """
    root = getattr(project, "root", None)
    if root is None:
        return False
    try:
        cfg = _yaml_load((root / "project.yml").read_text()) or {}
    except (OSError, TypeError, AttributeError, YAMLError):
        return False
    gpus = (cfg.get("run") or {}).get("gpus")
    if isinstance(gpus, str):
        return gpus.lower() == "all"
    if isinstance(gpus, bool):
        return gpus
    return False
