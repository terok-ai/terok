# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Archived-task queries — reading the immutable snapshots ``_archive_task``
leaves behind on deletion.  Writing the archive is part of the deletion
choreography and lives in [`terok.lib.orchestration.tasks.lifecycle`][terok.lib.orchestration.tasks.lifecycle].
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .meta import tasks_archive_dir


@dataclass
class ArchivedTask:
    """Metadata snapshot of an archived (deleted) task."""

    archive_dir: Path
    archived_at: str
    task_id: str
    name: str
    mode: str | None
    exit_code: int | None


def _load_archived_task_meta(entry: Path) -> dict | None:
    """Load an archived task's ``task.json`` snapshot, or ``None`` on miss/error."""
    json_path = entry / "task.json"
    if not json_path.is_file():
        return None
    try:
        text = json_path.read_text(encoding="utf-8")
        return json.loads(text) if text.strip() else {}
    except (OSError, ValueError):
        return None


def list_archived_tasks(project_name: str) -> list[ArchivedTask]:
    """Return archived tasks for *project_name*, sorted newest-first."""
    archive_root = tasks_archive_dir(project_name)
    if not archive_root.is_dir():
        return []
    results: list[ArchivedTask] = []
    for entry in sorted(archive_root.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        meta = _load_archived_task_meta(entry)
        if meta is None:
            continue
        # Parse archive timestamp from directory name: <timestamp>_<task_id>[_<name>]
        parts = entry.name.split("_", 2)
        archived_at = parts[0] if parts else entry.name
        results.append(
            ArchivedTask(
                archive_dir=entry,
                archived_at=archived_at,
                task_id=str(meta.get("task_id", "")),
                name=meta.get("name", ""),
                mode=meta.get("mode"),
                exit_code=meta.get("exit_code"),
            )
        )
    return results


def task_archive_list(project_name: str) -> None:
    """Print archived tasks for *project_name*."""
    archived = list_archived_tasks(project_name)
    if not archived:
        print("No archived tasks found")
        return
    for a in archived:
        extra = []
        if a.mode:
            extra.append(f"mode={a.mode}")
        if a.exit_code is not None:
            extra.append(f"exit={a.exit_code}")
        extra_s = f" [{'; '.join(extra)}]" if extra else ""
        print(f"- {a.archived_at} #{a.task_id}: {a.name}{extra_s}")


def task_archive_logs(project_name: str, archive_id: str) -> Path | None:
    """Return the log file path for an archived task identified by *archive_id*.

    *archive_id* is matched against archive directory names (prefix match).
    Returns the log file path if found, or ``None``.
    """
    archive_root = tasks_archive_dir(project_name)
    if not archive_root.is_dir():
        return None
    for entry in sorted(archive_root.iterdir(), reverse=True):
        if entry.is_dir() and entry.name.startswith(archive_id):
            log_file = entry / "logs" / "container.log"
            if log_file.is_file():
                return log_file
    return None


__all__ = [
    "ArchivedTask",
    "list_archived_tasks",
    "task_archive_list",
    "task_archive_logs",
]
