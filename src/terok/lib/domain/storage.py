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
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from terok.lib.integrations.executor import (
    SharedMountStorageInfo,
    TaskStorageInfo,
)

from ..core.config import sandbox_live_mounts_dir
from ..core.projects import list_projects
from .image_cleanup import ImageInfo, list_images

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
    """Format bytes as a right-aligned human-readable string.

    Uses SI units (1000-based) to match podman's output convention.
    Always returns a fixed-width string suitable for column alignment.
    """
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


def get_storage_overview() -> StorageOverview:
    """Gather global summary — fast, no per-container podman queries.

    Iterates all projects, sums workspace sizes via terok-executor, and
    classifies images into global vs per-project.
    """
    all_images = list_images()
    global_images = [img for img in all_images if _is_global_image(img)]

    shared_mounts = SharedMountStorageInfo.measure_all(sandbox_live_mounts_dir())

    # Per-project: sum image sizes + workspace sizes
    projects_conf = list_projects()
    project_image_bytes: dict[str, int] = {}
    for img in all_images:
        pid = _image_project_name(img)
        if pid:
            project_image_bytes[pid] = project_image_bytes.get(pid, 0) + parse_image_size(img.size)

    summaries = []
    for proj in projects_conf:
        tasks = TaskStorageInfo.measure_all(proj.tasks_root)
        # Per-project credential mounts are billed to the project they
        # belong to.  Shared-credential projects (``credentials_scope ==
        # "shared"``) leave their bytes in ``shared_mounts`` to avoid
        # double-counting the single global tree across every project.
        cred_mounts_bytes = 0
        if proj.credentials_scope == "project" and proj.project_mounts_dir.is_dir():
            cred_mounts_bytes = sum(
                m.bytes for m in SharedMountStorageInfo.measure_all(proj.project_mounts_dir)
            )
        summaries.append(
            ProjectSummary(
                project_name=proj.name,
                image_bytes=project_image_bytes.get(proj.name, 0),
                workspace_bytes=sum(t.workspace_bytes for t in tasks),
                task_count=len(tasks),
                credential_mounts_bytes=cred_mounts_bytes,
            )
        )

    # Surface L2 images whose project is no longer configured so they roll up
    # into the grand total instead of vanishing from the overview.
    known_ids = {p.name for p in projects_conf}
    orphan_bytes = sum(b for pid, b in project_image_bytes.items() if pid not in known_ids)
    if orphan_bytes:
        summaries.append(
            ProjectSummary(
                project_name=ORPHAN_PROJECT_NAME,
                image_bytes=orphan_bytes,
                workspace_bytes=0,
                task_count=0,
            )
        )

    return StorageOverview(
        global_images=global_images,
        shared_mounts=shared_mounts,
        projects=summaries,
    )


def get_project_storage_detail(project_name: str) -> ProjectDetail:
    """Detailed view for one project, including overlay sizes.

    This triggers ``podman ps --size`` for the project's containers —
    expect a brief pause while podman computes overlay diffs.
    """
    from ..core import runtime as _rt
    from ..core.projects import load_project

    project = load_project(project_name)
    project_images = [img for img in list_images(project_name) if not _is_global_image(img)]
    tasks = TaskStorageInfo.measure_all(project.tasks_root)
    runtime = _rt.resolve_runtime(project)
    # ``container_rw_sizes`` is podman-specific; not every backend exposes it.
    overlays = (
        runtime.container_rw_sizes(project_name) if hasattr(runtime, "container_rw_sizes") else {}
    )

    # Per-project credential mounts (only present when scope=project).
    # Keep the calculation in lockstep with ``get_storage_overview`` so the
    # two views can't disagree on the same project's bytes.
    cred_mounts_bytes = 0
    if project.credentials_scope == "project" and project.project_mounts_dir.is_dir():
        cred_mounts_bytes = sum(
            m.bytes for m in SharedMountStorageInfo.measure_all(project.project_mounts_dir)
        )

    return ProjectDetail(
        project_name=project_name,
        images=project_images,
        tasks=tasks,
        overlays=overlays,
        credential_mounts_bytes=cred_mounts_bytes,
    )
