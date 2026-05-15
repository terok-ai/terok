# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""TaskMeta on-disk I/O — the persistence boundary for task state.

On disk each task is two sibling files keyed on its ID:

* ``<task_id>_dossier.json`` — the wire-dossier file shield consumers
  read, in wire shape (``{project, task, name}``).
* ``<task_id>_meta.yml`` — terok's internal bookkeeping (mode,
  workspace, web port, hooks_fired, exit_code, lifecycle state).

Format-as-contract: the JSON file's filename says "dossier" and its
audience is "anyone consuming shield events", so its shape *is* the
wire dossier — no projection, no key translation.  The YAML file's
filename says "meta" and its audience is terok-internal; ruamel keeps
comments and round-trip ergonomics cheap.

The split is reconciled at the I/O boundary: ``read_task_meta``
returns one merged dict in internal-storage shape (``project_id``,
``task_id``, …), ``write_task_meta`` splits a single dict back to
both files atomically.  Pre-self-describing names (``<id>.json`` /
``<id>.yml``) are migrated to the new layout in place on first read.
"""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ...core.config import archive_dir
from ...core.paths import core_state_dir
from ...core.projects import load_project
from ...util.logging_utils import _log_debug
from ...util.yaml import dump as _yaml_dump, load as _yaml_load

#: Suffix of the wire-dossier JSON file (full name: ``<task_id>_dossier.json``).
_DOSSIER_SUFFIX = "_dossier.json"

#: Suffix of the orchestrator-bookkeeping YAML file (full name: ``<task_id>_meta.yml``).
_META_SUFFIX = "_meta.yml"

# Subset of in-memory meta keys that belong on the wire (and thus in
# the JSON dossier file).  Stored under internal names — translated
# at the I/O boundary by ``_DOSSIER_TO_WIRE`` / ``_DOSSIER_FROM_WIRE``.
_DOSSIER_INTERNAL_KEYS = ("project_id", "task_id", "name")
_DOSSIER_TO_WIRE = {"project_id": "project", "task_id": "task", "name": "name"}
_DOSSIER_FROM_WIRE = {v: k for k, v in _DOSSIER_TO_WIRE.items()}

CONTAINER_TEROK_CONFIG = "/home/dev/.terok"
"""In-container mount point for the per-task agent-config dir."""


def _is_safe_id_segment(value: str) -> bool:
    """Return True if *value* is safe to use as a single path component.

    Rejects empty, ``.``, ``..``, anything containing ``/`` or ``\\``,
    and anything starting with ``..``.  The path-builders in this
    module compose values via ``Path / value``, which treats embedded
    separators as nested components and ``..`` as a parent reference;
    this predicate is the defense-in-depth guard at that composition
    point.
    """
    return (
        bool(value)
        and value not in (".", "..")
        and "/" not in value
        and "\\" not in value
        and not value.startswith("..")
    )


def _reject_unsafe_id(value: str, what: str) -> None:
    """Raise ``SystemExit`` if *value* would escape the task store.

    Used at every path-builder boundary in this module so any caller
    — internal or otherwise — that hands an unvetted identifier in
    gets a loud error instead of a silent traversal.  The CLI entry
    points (``_normalize_pt`` slash branch, ``lookup_container_by_pt``)
    catch the obvious cases earlier; this layer catches the rest.
    """
    if not _is_safe_id_segment(value):
        raise SystemExit(f"Refusing path-unsafe {what}: {value!r}")


def dossier_path(meta_dir: Path, task_id: str) -> Path:
    """Path to the wire-dossier JSON file — what shield consumers read.

    The OCI ``dossier.meta_path`` annotation points operators at *this*
    file.  Companion bookkeeping lives at the ``_meta.yml`` sibling.
    """
    _reject_unsafe_id(task_id, "task_id")
    return meta_dir / f"{task_id}{_DOSSIER_SUFFIX}"


def meta_path(meta_dir: Path, task_id: str) -> Path:
    """Path to the orchestrator-bookkeeping YAML — terok-internal state.

    Holds everything except the wire-dossier triple.  Single consumer
    (terok itself), so ruamel round-tripping is fine.
    """
    _reject_unsafe_id(task_id, "task_id")
    return meta_dir / f"{task_id}{_META_SUFFIX}"


def _migrate_legacy_filenames(meta_dir: Path, task_id: str) -> None:
    """Rename pre-self-describing meta files in place — one-shot, idempotent.

    Earlier iterations stored the dossier and meta as ``<task_id>.json``
    and ``<task_id>.yml``; the new names spell out their audience.
    Rename eagerly so every other read/write/iter helper deals with the
    canonical layout exclusively.  Idempotent: a second call is a no-op.
    """
    pairs = (
        (meta_dir / f"{task_id}.json", dossier_path(meta_dir, task_id)),
        (meta_dir / f"{task_id}.yml", meta_path(meta_dir, task_id)),
    )
    for old, new in pairs:
        if old.is_file() and not new.is_file():
            old.rename(new)


def read_task_meta(meta_dir: Path, task_id: str) -> dict | None:
    """Compose the orchestrator's logical task-meta dict from the on-disk pair.

    Reads the wire-dossier JSON and the internal YAML, translates the
    JSON's wire keys back to internal storage names, and returns the
    union.  Either file may be absent.  Returns ``None`` only when
    neither file is on disk.

    A pre-self-describing layout (``<task_id>.json`` / ``<task_id>.yml``)
    is migrated to the new names in place before the read so callers
    never see the legacy paths.
    """
    _migrate_legacy_filenames(meta_dir, task_id)
    json_path = dossier_path(meta_dir, task_id)
    yml_path = meta_path(meta_dir, task_id)

    if not (json_path.is_file() or yml_path.is_file()):
        return None

    merged = _merge_dossier_into(_read_yml_meta(yml_path), _read_json_meta(json_path))
    if _backfill_project_id(merged, meta_dir):
        # Normalise on disk so the backfilled project_id persists.
        write_task_meta(dossier_path(meta_dir, task_id), merged)

    return merged


def _read_json_meta(json_path: Path) -> dict:
    """Read the wire-dossier JSON file — ``{}`` when absent or empty."""
    if not json_path.is_file():
        return {}
    text = json_path.read_text(encoding="utf-8")
    return json.loads(text) if text.strip() else {}


def _read_yml_meta(yml_path: Path) -> dict:
    """Read the bookkeeping YAML file as a plain dict — ``{}`` when absent."""
    if not yml_path.is_file():
        return {}
    return _to_plain(_yaml_load(yml_path.read_text(encoding="utf-8")) or {})


def _merge_dossier_into(yml_data: dict, dossier_data: dict) -> dict:
    """Return *yml_data* merged with the wire-key-translated *dossier_data*.

    Only recognised wire keys (``project`` / ``task`` / ``name``) are
    translated and merged; any other key in the dossier file is ignored
    rather than crashing this persistence boundary on an unexpected shape.
    """
    merged = dict(yml_data)
    for wire_key, value in dossier_data.items():
        internal_key = _DOSSIER_FROM_WIRE.get(wire_key)
        if internal_key is not None:
            merged[internal_key] = value
    return merged


def _backfill_project_id(merged: dict, meta_dir: Path) -> bool:
    """Backfill ``project_id`` from the meta-dir path for legacy records.

    Records that predate the field landing in TaskMeta carry no
    ``project_id``; without it a task-rename lands a half-populated
    dossier (no ``project``) on the wire.  Path layout is
    ``<state>/projects/<project_id>/tasks``.  Returns ``True`` when a
    value was filled in, so the caller knows the record needs
    re-normalising on disk.
    """
    if merged.get("project_id"):
        return False
    if meta_dir.parent.parent.name != "projects":
        return False
    merged["project_id"] = meta_dir.parent.name
    return True


def write_task_meta(dossier_handle: Path, meta: dict) -> None:
    """Atomic split-write: ``_dossier.json`` + ``_meta.yml`` in one atomic step each.

    *dossier_handle* is a dossier-file handle — what
    [`dossier_path`][terok.lib.orchestration.tasks.dossier_path] returns
    and what [`load_task_meta`][terok.lib.orchestration.tasks.load_task_meta]
    hands back.  Pre-self-describing handles (``<id>.json``) are accepted
    too and canonicalized on write.  The companion bookkeeping path is
    derived by swapping the suffix.  Atomic-rename writes (``.tmp`` +
    ``os.replace``) on each file mean a partial write under EINTR /
    power loss can leave one stale and the other fresh, but never a
    half-written file — readers always get a parseable shape.
    """
    meta_dir, task_id = _dossier_handle_to_dir_and_id(dossier_handle)
    meta_dir.mkdir(parents=True, exist_ok=True)

    dossier = {
        wire_key: str(meta[internal_key])
        for internal_key, wire_key in _DOSSIER_TO_WIRE.items()
        if meta.get(internal_key)
    }
    state = {k: v for k, v in meta.items() if k not in _DOSSIER_INTERNAL_KEYS}

    _atomic_write(
        dossier_path(meta_dir, task_id),
        json.dumps(dossier, indent=2, ensure_ascii=False, default=str) + "\n",
    )
    _atomic_write(meta_path(meta_dir, task_id), _yaml_dump(state))


def _dossier_handle_to_dir_and_id(path: Path) -> tuple[Path, str]:
    """Decompose a dossier-file handle into ``(meta_dir, task_id)``.

    Accepts both the canonical ``<task_id>_dossier.json`` and the
    pre-self-describing ``<task_id>.json`` so callers caching a handle
    across the migration boundary keep working.  Raises on anything
    else — silently inferring a task_id from a foreign filename would
    let bugs pass undetected.
    """
    name = path.name
    if name.endswith(_DOSSIER_SUFFIX):
        return path.parent, name[: -len(_DOSSIER_SUFFIX)]
    if name.endswith(".json"):
        return path.parent, name[: -len(".json")]
    raise ValueError(f"not a dossier-file handle: {path}")


def _atomic_write(path: Path, text: str) -> None:
    """Stage to ``<path>.tmp`` and rename — POSIX-atomic on the same filesystem."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _to_plain(obj: Any) -> Any:
    """Recursively unwrap ruamel ``CommentedMap`` / ``CommentedSeq`` to plain types.

    Round-trip-mode YAML hands back commented containers that are dict/list
    subclasses.  ``json.dumps`` accepts them but the merged dict should be
    fully plain so downstream readers don't inadvertently inherit metadata
    that no longer applies.
    """
    if isinstance(obj, dict):
        return {str(k): _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain(v) for v in obj]
    return obj


def iter_task_ids(meta_dir: Path) -> Iterator[str]:
    """Yield every task ID with at least one meta file in *meta_dir*.

    Recognises both the canonical layout (``<id>_dossier.json`` /
    ``<id>_meta.yml``) and the pre-self-describing layout
    (``<id>.json`` / ``<id>.yml``) — the latter is migrated lazily on
    the next ``read_task_meta`` call, so transient mixed states are
    yielded under their canonical task ID without double-counting.
    """
    if not meta_dir.is_dir():
        return
    seen: set[str] = set()
    for path in meta_dir.iterdir():
        if not path.is_file() or path.name.endswith(".tmp"):
            continue
        tid = _task_id_from_filename(path.name)
        if tid and tid not in seen:
            seen.add(tid)
            yield tid


def _task_id_from_filename(name: str) -> str:
    """Strip the meta-file suffix to recover the task ID, ``""`` if unrecognised."""
    for suffix in (_DOSSIER_SUFFIX, _META_SUFFIX, ".json", ".yml"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return ""


def task_exists(project_id: str, task_id: str) -> bool:
    """Return ``True`` if any task-meta file exists for ``(project_id, task_id)``.

    Considers both the canonical (``<task_id>_dossier.json`` /
    ``<task_id>_meta.yml``) and legacy (``<task_id>.json`` /
    ``<task_id>.yml``) on-disk layouts.
    """
    meta_dir = tasks_meta_dir(project_id)
    return (
        dossier_path(meta_dir, task_id).is_file()
        or meta_path(meta_dir, task_id).is_file()
        or (meta_dir / f"{task_id}.json").is_file()
        or (meta_dir / f"{task_id}.yml").is_file()
    )


def tasks_meta_dir(project_id: str) -> Path:
    """Return the directory containing task metadata files for *project_id*."""
    _reject_unsafe_id(project_id, "project_id")
    return core_state_dir() / "projects" / project_id / "tasks"


def agent_config_dir(project_id: str, task_id: str) -> Path:
    """Host path of the agent-config dir bind-mounted at `CONTAINER_TEROK_CONFIG`."""
    _reject_unsafe_id(project_id, "project_id")
    _reject_unsafe_id(task_id, "task_id")
    return load_project(project_id).tasks_root / str(task_id) / "agent-config"


def tasks_archive_dir(project_id: str) -> Path:
    """Return the directory containing archived task data for *project_id*.

    Lives under the namespace archive tree (``archive/<pid>/tasks/``) so
    operators can find all archived data in one location.  On project
    deletion the entire ``archive/<pid>/`` subtree is bundled into the
    project snapshot and removed.
    """
    _reject_unsafe_id(project_id, "project_id")
    return archive_dir() / project_id / "tasks"


def update_task_exit_code(project_id: str, task_id: str, exit_code: int | None) -> None:
    """Update task metadata with exit code and final status.

    Args:
        project_id: The project ID
        task_id: The task ID
        exit_code: The exit code from the task, or None if unknown/failed
    """
    meta_dir = tasks_meta_dir(project_id)
    meta = read_task_meta(meta_dir, task_id)
    if meta is None:
        return
    meta["exit_code"] = exit_code
    write_task_meta(dossier_path(meta_dir, task_id), meta)


def _check_mode(meta: dict, expected: str) -> None:
    """Raise SystemExit if the task's mode conflicts with *expected*."""
    mode = meta.get("mode")
    if mode and mode != expected:
        raise SystemExit(f"Task already ran in mode '{mode}', cannot run in '{expected}'")


def load_task_meta(
    project_id: str, task_id: str, expected_mode: str | None = None
) -> tuple[dict, Path]:
    """Load task metadata and optionally validate mode.

    Returns ``(meta, dossier_path)``: the merged in-memory dict (in
    its internal storage shape — ``project_id`` / ``task_id`` / …)
    plus the canonical dossier-file handle for write-back via
    ``write_task_meta(dossier_path, meta)``.  Raises SystemExit if
    the task is unknown or its mode conflicts with *expected_mode*.
    """
    meta_dir = tasks_meta_dir(project_id)
    meta = read_task_meta(meta_dir, task_id)
    if meta is None:
        raise SystemExit(f"Unknown task {task_id}")
    if expected_mode is not None:
        _check_mode(meta, expected_mode)
    return meta, dossier_path(meta_dir, task_id)


def mark_task_deleting(project_id: str, task_id: str) -> None:
    """Persist ``deleting: true`` to the task's metadata file."""
    try:
        meta_dir = tasks_meta_dir(project_id)
        meta = read_task_meta(meta_dir, task_id)
        if meta is None:
            return
        meta["deleting"] = True
        write_task_meta(dossier_path(meta_dir, task_id), meta)
    except Exception as e:
        _log_debug(f"mark_task_deleting: failed project_id={project_id} task_id={task_id}: {e}")


__all__ = [
    "CONTAINER_TEROK_CONFIG",
    "agent_config_dir",
    "dossier_path",
    "iter_task_ids",
    "load_task_meta",
    "mark_task_deleting",
    "meta_path",
    "read_task_meta",
    "task_exists",
    "tasks_archive_dir",
    "tasks_meta_dir",
    "update_task_exit_code",
    "write_task_meta",
]
