# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Filesystem helpers for directory creation and writability checks."""

import os
from datetime import UTC, datetime
from pathlib import Path


def ensure_dir(path: Path) -> None:
    """Create a directory (and parents) if it doesn't exist."""
    path.mkdir(parents=True, exist_ok=True)


def ensure_dir_writable(path: Path, label: str) -> None:
    """Create *path* if needed and verify it is writable, or exit with an error."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise SystemExit(f"{label} directory is not writable: {path} ({e})")
    if not path.is_dir():
        raise SystemExit(f"{label} path is not a directory: {path}")
    if not os.access(path, os.W_OK | os.X_OK):
        uid = os.getuid()
        gid = os.getgid()
        raise SystemExit(
            f"{label} directory is not writable: {path}\n"
            f"Fix permissions for the user running terok (uid={uid}, gid={gid}). "
            f"Example: sudo chown -R {uid}:{gid} {path}"
        )


def archive_timestamp() -> str:
    """Generate a UTC timestamp string suitable for archive filenames."""
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")


def unique_archive_path(root: Path, base_name: str, suffix: str = "") -> Path:
    """Return a collision-safe path under *root* for an archive entry.

    Appends *suffix* (e.g. ``".tar.gz"``) to *base_name*.  If the resulting
    path already exists, appends ``_1``, ``_2``, … before the suffix until a
    free name is found.
    """
    candidate = root / f"{base_name}{suffix}"
    counter = 0
    while candidate.exists():
        counter += 1
        candidate = root / f"{base_name}_{counter}{suffix}"
    return candidate
