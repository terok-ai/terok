# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Render helpers + overview/detail pipelines for ``terok image usage``.

Extracted from the former ``storage.py`` so ``image.py`` can stay lean.
Pure view layer — no argparse registration, no dispatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...lib.util.ansi import blue, bold, color, supports_color

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...lib.domain.storage import ProjectDetail, StorageOverview

# ---------------------------------------------------------------------------
# Column formatting helpers
# ---------------------------------------------------------------------------

_TABLE_WIDTH = 50
_SIZE_WIDTH = 10
_TASK_COUNT_WIDTH = 5


def _c(enabled: bool) -> bool:
    """Alias for the color-enabled flag threading."""
    return enabled


def _section(title: str, enabled: bool) -> str:
    """Render a section header: ``═══ Title ═════...``."""
    pad = max(0, _TABLE_WIDTH - len(title) - 5)
    bar = f"═══ {title} {'═' * pad}"
    return bold(color(bar, "35", enabled), enabled)


def _sub_header(text: str, enabled: bool) -> str:
    """Render a sub-section header like ``Images:``."""
    return bold(text, enabled)


def _size(text: str, enabled: bool) -> str:
    """Render a size value in blue."""
    return blue(text, enabled)


def _dim(text: str, enabled: bool) -> str:
    """Render secondary text in gray."""
    return color(text, "90", enabled)


def _separator(width: int, enabled: bool) -> str:
    """Render a thin separator line."""
    return _dim("─" * width, enabled)


def _heavy_separator(enabled: bool) -> str:
    """Render a heavy separator for grand totals."""
    return _dim("═" * _TABLE_WIDTH, enabled)


# ---------------------------------------------------------------------------
# Overview mode — the fast, wide-angle view
# ---------------------------------------------------------------------------


def cmd_overview(*, json_output: bool = False) -> None:
    """Print each overview row as it is measured, or collect a JSON report."""
    from ...lib.domain.storage import (
        StorageOverview,
        discover_storage_overview,
        format_bytes,
        get_storage_overview,
    )

    if json_output:
        _json_overview(get_storage_overview())
        return

    c = _c(supports_color())
    print(_section("Global", c), flush=True)
    pending = discover_storage_overview()
    overview = StorageOverview(pending.global_images, [], [])

    # Images
    if overview.global_images:
        print(_sub_header("Images:", c), flush=True)
        name_w = max(len("NAME"), *(len(img.full_name) for img in overview.global_images))
        total = format_bytes(overview.global_images_bytes)
        size_w = max(len("SIZE"), len(total), *(len(img.size) for img in overview.global_images))
        print(
            f"  {bold(f'{"NAME":<{name_w}}', c)}  "
            f"{bold(f'{"SIZE":>{size_w}}', c)}  {bold('CREATED', c)}",
            flush=True,
        )
        for img in overview.global_images:
            print(
                f"  {img.full_name:<{name_w}}  "
                f"{_size(f'{img.size:>{size_w}}', c)}  "
                f"{_dim(img.created, c)}",
                flush=True,
            )
        print(f"  {'':<{name_w}}  {_separator(size_w, c)}", flush=True)
        print(f"  {'':<{name_w}}  {_size(bold(f'{total:>{size_w}}', c), c)}", flush=True)

    # Shared mounts
    if pending.shared_mounts:
        print(_sub_header("Shared mounts:", c), flush=True)
        label_w = max(len(row.name) for row in pending.shared_mounts)
        size_w = _SIZE_WIDTH
        for mount_row in pending.shared_mounts:
            mount = _measure(f"  {mount_row.name:<{label_w}}  ", mount_row.measure)
            overview.shared_mounts.append(mount)
            size = format_bytes(mount.bytes)
            print(_size(f"{size:>{size_w}}", c), flush=True)
        total = format_bytes(overview.shared_mounts_bytes)
        print(f"  {'':<{label_w}}  {_separator(size_w, c)}", flush=True)
        print(f"  {'':<{label_w}}  {_size(bold(f'{total:>{size_w}}', c), c)}", flush=True)

    # ── Projects section ──
    if pending.projects:
        print(_section("Projects", c), flush=True)
        pid_w = max(len("PROJECT"), *(len(row.name) for row in pending.projects))
        img_w = ws_w = _SIZE_WIDTH
        task_w = _TASK_COUNT_WIDTH

        print(
            f"  {bold(f'{"PROJECT":<{pid_w}}', c)}  "
            f"{bold(f'{"IMAGES":>{img_w}}', c)}  "
            f"{bold(f'{"WORKSPACES":>{ws_w}}', c)}  "
            f"{bold(f'{"TASKS":>{task_w}}', c)}",
            flush=True,
        )
        for project_row in pending.projects:
            project = _measure(f"  {project_row.name:<{pid_w}}  ", project_row.measure)
            overview.projects.append(project)
            img_s = format_bytes(project.image_bytes)
            ws_s = format_bytes(project.workspace_bytes)
            print(
                f"{_size(f'{img_s:>{img_w}}', c)}  "
                f"{_size(f'{ws_s:>{ws_w}}', c)}  "
                f"{project.task_count:>{task_w}}",
                flush=True,
            )

        total_img = format_bytes(sum(p.image_bytes for p in overview.projects))
        total_ws = format_bytes(sum(p.workspace_bytes for p in overview.projects))
        total_tasks = sum(p.task_count for p in overview.projects)
        print(f"  {'':<{pid_w}}  {_separator(img_w, c)}  {_separator(ws_w, c)}", flush=True)
        print(
            f"  {'':<{pid_w}}  "
            f"{_size(bold(f'{total_img:>{img_w}}', c), c)}  "
            f"{_size(bold(f'{total_ws:>{ws_w}}', c), c)}  "
            f"{bold(f'{total_tasks:>{task_w}}', c)}",
            flush=True,
        )

    # ── Grand total ──
    print(_heavy_separator(c), flush=True)
    gt = format_bytes(overview.grand_total)
    label = "Grand total"
    print(
        f"{bold(label, c)}  {_size(bold(f'{gt:>{_TABLE_WIDTH - len(label) - 2}}', c), c)}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Detail mode — zooming into one project
# ---------------------------------------------------------------------------


def cmd_detail(project_name: str, *, json_output: bool = False) -> None:
    """Print each task's name before measuring its workspace and overlays."""
    from ...lib.domain.storage import (
        ProjectDetail,
        discover_project_storage,
        format_bytes,
        get_project_storage_detail,
    )

    if json_output:
        _json_detail(get_project_storage_detail(project_name))
        return

    c = _c(supports_color())
    print(_section(project_name, c), flush=True)
    pending = discover_project_storage(project_name)
    detail = ProjectDetail(project_name, pending.images, [], {})

    # Images
    if detail.images:
        print(_sub_header("Images:", c), flush=True)
        name_w = max(len(img.full_name) for img in detail.images)
        size_w = max(len(img.size) for img in detail.images)
        for img in detail.images:
            print(f"  {img.full_name:<{name_w}}  {_size(f'{img.size:>{size_w}}', c)}", flush=True)

    # Tasks with overlay sizes
    if pending.tasks:
        print(_sub_header("Tasks:", c), flush=True)
        tid_w = max(len("ID"), *(len(row.name) for row in pending.tasks))
        ws_w = ov_w = _SIZE_WIDTH

        print(
            f"  {bold(f'{"ID":<{tid_w}}', c)}  "
            f"{bold(f'{"WORKSPACE":>{ws_w}}', c)}  "
            f"{bold(f'{"OVERLAY":>{ov_w}}', c)}",
            flush=True,
        )
        for row in pending.tasks:
            task, overlay = _measure(f"  {row.name:<{tid_w}}  ", row.measure)
            detail.tasks.append(task)
            detail.overlays[task.task_id] = overlay
            ws_s = format_bytes(task.workspace_bytes)
            ov_s = format_bytes(overlay)
            print(
                f"{_size(f'{ws_s:>{ws_w}}', c)}  {_size(f'{ov_s:>{ov_w}}', c)}",
                flush=True,
            )

        total_ws = format_bytes(detail.workspace_bytes)
        total_ov = format_bytes(detail.overlay_bytes)
        print(f"  {'':<{tid_w}}  {_separator(ws_w, c)}  {_separator(ov_w, c)}", flush=True)
        print(
            f"  {'':<{tid_w}}  "
            f"{_size(bold(f'{total_ws:>{ws_w}}', c), c)}  "
            f"{_size(bold(f'{total_ov:>{ov_w}}', c), c)}",
            flush=True,
        )

    # Project total
    print(_separator(_TABLE_WIDTH, c), flush=True)
    label = "Project total"
    credentials = _measure(f"{bold(label, c)}  ", pending.measure_credentials)
    gt = format_bytes(detail.total_bytes + credentials)
    print(_size(bold(f"{gt:>{_TABLE_WIDTH - len(label) - 2}}", c), c), flush=True)


def _measure[T](prefix: str, measure: Callable[[], T]) -> T:
    """Flush the row label before counting; terminate partial lines on failure."""
    print(prefix, end="", flush=True)
    try:
        return measure()
    except BaseException:
        print(flush=True)
        raise


# ---------------------------------------------------------------------------
# JSON output — machine-readable alternative
# ---------------------------------------------------------------------------


def _json_overview(overview: StorageOverview) -> None:
    """Emit the overview as JSON."""
    import json

    from ...lib.domain.storage import parse_image_size

    data = {
        "global": {
            "images": [
                {"name": img.full_name, "size": img.size, "bytes": parse_image_size(img.size)}
                for img in overview.global_images
            ],
            "shared_mounts": [
                {"name": m.name, "label": m.label, "bytes": m.bytes} for m in overview.shared_mounts
            ],
        },
        "projects": [
            {
                "project_name": p.project_name,
                "image_bytes": p.image_bytes,
                "workspace_bytes": p.workspace_bytes,
                "task_count": p.task_count,
                "total_bytes": p.total_bytes,
            }
            for p in overview.projects
        ],
        "grand_total_bytes": overview.grand_total,
    }
    print(json.dumps(data, indent=2))


def _json_detail(detail: ProjectDetail) -> None:
    """Emit the project detail as JSON."""
    import json

    from ...lib.domain.storage import parse_image_size

    data = {
        "project_name": detail.project_name,
        "images": [
            {"name": img.full_name, "size": img.size, "bytes": parse_image_size(img.size)}
            for img in detail.images
        ],
        "tasks": [
            {
                "task_id": t.task_id,
                "workspace_bytes": t.workspace_bytes,
                "agent_config_bytes": t.agent_config_bytes,
                "overlay_bytes": detail.overlays.get(t.task_id, 0),
            }
            for t in detail.tasks
        ],
        "total_bytes": detail.total_bytes,
    }
    print(json.dumps(data, indent=2))
