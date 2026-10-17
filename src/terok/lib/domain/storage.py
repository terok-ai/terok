# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Storage usage aggregation across the package stack.

Orchestrates queries from three layers:
- **terok-sandbox** — container overlay sizes (podman)
- **terok-executor** — task workspace and shared mount sizes (filesystem)
- **terok** itself — image knowledge (L0/L1/L2 classification)

Two entry points mirror two levels of detail:

- [`get_storage_overview`][terok.lib.domain.storage.get_storage_overview] — fast global summary with per-project
  one-liners (no per-container podman ``--size`` queries)
- [`get_project_storage_detail`][terok.lib.domain.storage.get_project_storage_detail] — per-task breakdown for one
  project, including the expensive overlay size computation

The discovery queries expose named, deferred measurements so text reports
can identify each row before its filesystem walk or overlay inspection starts.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from terok.lib.integrations.executor import (
    SharedMountStorageInfo,
    TaskStorageInfo,
)

from ..core.config import sandbox_live_mounts_dir
from ..core.projects import list_projects
from ..core.task_state import CONTAINER_MODES, container_name
from .image_cleanup import ImageInfo, list_images

if TYPE_CHECKING:
    from ..core.project_model import ProjectConfig
    from ..integrations.sandbox import Container

# ---------------------------------------------------------------------------
# Size parsing — translate podman's human-readable strings to bytes
# ---------------------------------------------------------------------------

_SIZE_RE = re.compile(r"([\d.]+)\s*([a-zA-Z]+)")
_UNITS: dict[str, int] = {
    "B": 1,
    "KB": 1_000,
    "MB": 1_000_000,
    "GB": 1_000_000_000,
    "TB": 1_000_000_000_000,
    "KIB": 1 << 10,
    "MIB": 1 << 20,
    "GIB": 1 << 30,
    "TIB": 1 << 40,
}


def parse_image_size(text: str) -> int:
    """Best-effort parse of podman's human-readable image size strings.

    Returns 0 for unparseable input — storage reporting should never crash
    on a formatting surprise from podman.
    """
    m = _SIZE_RE.search(text)
    if not m:
        return 0
    try:
        multiplier = _UNITS.get(m.group(2).upper())
        if multiplier is None:
            return 0
        return int(float(m.group(1)) * multiplier)
    except (ValueError, OverflowError):
        return 0


def format_bytes(n: int) -> str:
    """Format bytes in SI units (1000-based), leaving column padding to the view."""
    for unit, threshold in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= threshold:
            return f"{n / threshold:.1f} {unit}"
    return f"{n} B"


# ---------------------------------------------------------------------------
# Image classification — terok's layering knowledge
# ---------------------------------------------------------------------------

_GLOBAL_PREFIXES = ("terok-l0", "terok-l1-cli")

ORPHAN_PROJECT_NAME = "(orphans)"
"""Synthetic project name for the overview row that collects L2 images whose
project no longer exists in the config.

Parentheses are forbidden in real project names (validated against
``[a-z0-9][a-z0-9_-]*`` in ``project_model``), so this value can never
collide with a configured project."""


def _is_global_image(img: ImageInfo) -> bool:
    """L0/L1 base images and dangling images belong to the global section."""
    if img.repository == "<none>":
        return True
    return img.project_key.startswith(_GLOBAL_PREFIXES)


def _image_project_name(img: ImageInfo) -> str | None:
    """Extract the project name from an L2 image, or None for global images."""
    if _is_global_image(img):
        return None
    if img.tag in ("l2-cli", "l2-dev"):
        return img.project_key
    return None


# ---------------------------------------------------------------------------
# Overview dataclasses — the fast global view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectSummary:
    """One-line digest of a project's storage footprint."""

    project_name: str
    image_bytes: int
    workspace_bytes: int
    task_count: int
    credential_mounts_bytes: int = 0
    """Bytes occupied by per-project agent-config mounts (``_claude-config``
    etc.) when [`credentials_scope`][terok.lib.core.project_model.ProjectConfig.credentials_scope]
    is ``"project"``.  Zero for shared-credential projects — their bytes
    already roll up into [`StorageOverview.shared_mounts`][terok.lib.domain.storage.StorageOverview.shared_mounts]."""

    @property
    def total_bytes(self) -> int:
        """Sum of images, workspaces, and per-project credential mounts."""
        return self.image_bytes + self.workspace_bytes + self.credential_mounts_bytes


@dataclass(frozen=True)
class StorageOverview:
    """Fast global summary: globals expanded, projects as one-liners."""

    global_images: list[ImageInfo]
    shared_mounts: list[SharedMountStorageInfo]
    projects: list[ProjectSummary]

    @property
    def global_images_bytes(self) -> int:
        """Total size of global images."""
        return sum(parse_image_size(img.size) for img in self.global_images)

    @property
    def shared_mounts_bytes(self) -> int:
        """Total size of shared mount directories."""
        return sum(m.bytes for m in self.shared_mounts)

    @property
    def projects_bytes(self) -> int:
        """Total size across all projects."""
        return sum(p.total_bytes for p in self.projects)

    @property
    def grand_total(self) -> int:
        """Everything combined."""
        return self.global_images_bytes + self.shared_mounts_bytes + self.projects_bytes


# ---------------------------------------------------------------------------
# Detail dataclass — per-task breakdown for one project
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectDetail:
    """Full per-task storage breakdown for a single project."""

    project_name: str
    images: list[ImageInfo]
    tasks: list[TaskStorageInfo]
    overlays: dict[str, int]
    """Writable-layer bytes keyed by task ID, summed across container modes."""
    credential_mounts_bytes: int = 0
    """Bytes occupied by per-project agent-config mounts when
    ``credentials_scope == "project"``; zero otherwise (the shared-tree
    bytes appear once in [`StorageOverview.shared_mounts`][terok.lib.domain.storage.StorageOverview.shared_mounts])."""

    @property
    def images_bytes(self) -> int:
        """Total size of project images."""
        return sum(parse_image_size(img.size) for img in self.images)

    @property
    def workspace_bytes(self) -> int:
        """Total workspace size across all tasks."""
        return sum(t.workspace_bytes for t in self.tasks)

    @property
    def overlay_bytes(self) -> int:
        """Total overlay size across all running containers."""
        return sum(self.overlays.values())

    @property
    def total_bytes(self) -> int:
        """Everything for this project."""
        return (
            self.images_bytes
            + self.workspace_bytes
            + self.overlay_bytes
            + self.credential_mounts_bytes
        )


# ---------------------------------------------------------------------------
# Public queries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StorageMeasurement[T]:
    """A named storage row whose expensive measurement has not started."""

    name: str
    measure: Callable[[], T]


@dataclass(frozen=True)
class OverviewMeasurements:
    """Global images and pending mount/project rows, in report order."""

    global_images: list[ImageInfo]
    shared_mounts: list[StorageMeasurement[SharedMountStorageInfo]]
    projects: list[StorageMeasurement[ProjectSummary]]


@dataclass(frozen=True)
class ProjectMeasurements:
    """Project images and pending task rows, including their overlay bytes."""

    images: list[ImageInfo]
    tasks: list[StorageMeasurement[tuple[TaskStorageInfo, int]]]
    measure_credentials: Callable[[], int]


def get_storage_overview() -> StorageOverview:
    """Collect the global storage report without presentation side effects."""
    pending = discover_storage_overview()
    return StorageOverview(
        global_images=pending.global_images,
        shared_mounts=[row.measure() for row in pending.shared_mounts],
        projects=[row.measure() for row in pending.projects],
    )


def discover_storage_overview() -> OverviewMeasurements:
    """Discover row names without walking workspaces or shared mount trees."""
    all_images = list_images()
    global_images = [img for img in all_images if _is_global_image(img)]
    projects_conf = list_projects()
    project_image_bytes: dict[str, int] = {}
    for img in all_images:
        pid = _image_project_name(img)
        if pid:
            project_image_bytes[pid] = project_image_bytes.get(pid, 0) + parse_image_size(img.size)

    projects = [
        StorageMeasurement(
            proj.name, partial(_measure_project, proj, project_image_bytes.get(proj.name, 0))
        )
        for proj in projects_conf
    ]

    # Surface L2 images whose project is no longer configured so they roll up
    # into the grand total instead of vanishing from the overview.
    known_ids = {p.name for p in projects_conf}
    orphan_bytes = sum(b for pid, b in project_image_bytes.items() if pid not in known_ids)
    if orphan_bytes:
        projects.append(
            StorageMeasurement(
                ORPHAN_PROJECT_NAME,
                partial(ProjectSummary, ORPHAN_PROJECT_NAME, orphan_bytes, 0, 0),
            )
        )

    return OverviewMeasurements(
        global_images=global_images,
        shared_mounts=[
            StorageMeasurement(
                SharedMountStorageInfo.label_for(path.name),
                partial(SharedMountStorageInfo.measure, path),
            )
            for path in _directories(sandbox_live_mounts_dir())
        ],
        projects=projects,
    )


def get_project_storage_detail(project_name: str) -> ProjectDetail:
    """Collect per-task filesystem and overlay sizes for one project."""
    pending = discover_project_storage(project_name)
    measured = [row.measure() for row in pending.tasks]
    return ProjectDetail(
        project_name=project_name,
        images=pending.images,
        tasks=[task for task, _ in measured],
        overlays={task.task_id: overlay for task, overlay in measured},
        credential_mounts_bytes=pending.measure_credentials(),
    )


def discover_project_storage(project_name: str) -> ProjectMeasurements:
    """Discover task rows without measuring their workspaces or overlays.

    Container-only tasks remain visible when their host directory is gone.
    Each row measures all of its container modes exactly once.
    """
    from ..core import runtime as _rt
    from ..core.projects import load_project

    project = load_project(project_name)
    project_images = [img for img in list_images(project_name) if not _is_global_image(img)]
    runtime = _rt.resolve_runtime(project)
    task_containers: dict[str, list[Container]] = {}
    for container in runtime.containers_with_prefix(project_name):
        task_id = container.name.rsplit("-", 1)[-1]
        if task_id.isalnum() and any(
            container.name == container_name(project_name, mode, task_id)
            for mode in CONTAINER_MODES
        ):
            task_containers.setdefault(task_id, []).append(container)
    task_ids = {path.name for path in _directories(project.tasks_root)} | task_containers.keys()
    return ProjectMeasurements(
        images=project_images,
        tasks=[
            StorageMeasurement(
                task_id,
                partial(
                    _measure_task, project.tasks_root / task_id, task_containers.get(task_id, [])
                ),
            )
            for task_id in sorted(task_ids)
        ],
        measure_credentials=partial(_credential_mount_bytes, project),
    )


def _directories(root: Path) -> list[Path]:
    """List immediate directories in name order without traversing their contents."""
    return sorted(path for path in root.iterdir() if path.is_dir()) if root.is_dir() else []


def _measure_project(project: ProjectConfig, image_bytes: int) -> ProjectSummary:
    """Measure one project's workspaces and private credential mounts."""
    tasks = TaskStorageInfo.measure_all(project.tasks_root)
    return ProjectSummary(
        project_name=project.name,
        image_bytes=image_bytes,
        workspace_bytes=sum(task.workspace_bytes for task in tasks),
        task_count=len(tasks),
        credential_mounts_bytes=_credential_mount_bytes(project),
    )


def _measure_task(task_dir: Path, containers: list[Container]) -> tuple[TaskStorageInfo, int]:
    """Measure one task's host directories and all of its container overlays."""
    return TaskStorageInfo.measure(task_dir), sum(
        container.rw_size or 0 for container in containers
    )


def _credential_mount_bytes(project: ProjectConfig) -> int:
    """Bill private credentials to their project, shared credentials only globally."""
    if project.credentials_scope != "project":
        return 0
    return sum(
        mount.bytes for mount in SharedMountStorageInfo.measure_all(project.project_mounts_dir)
    )
