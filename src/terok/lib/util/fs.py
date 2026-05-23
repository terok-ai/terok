# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Archive directory and filename helpers unique to terok.

Generic ``ensure_dir`` / ``ensure_dir_writable`` live in
[`terok_util.fs`][terok_util.fs]; this module composes them with
terok's own timestamp + collision-avoidance conventions for archived
projects and tasks.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

from terok_util import ensure_dir


def archive_timestamp() -> str:
    """Generate a UTC timestamp string suitable for archive filenames."""
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")


def unique_archive_path(root: Path, base_name: str, suffix: str = "") -> Path:
    """Return a collision-safe path under *root* for an archive entry.

    Appends *suffix* (e.g. ``".tar.gz"``) to *base_name*.  If the resulting
    path already exists, appends ``_1``, ``_2``, … before the suffix until a
    free name is found.

    .. note:: This only checks existence; it does **not** create the path.
       For atomic directory creation, use [`create_archive_dir`][terok.lib.util.fs.create_archive_dir].
    """
    candidate = root / f"{base_name}{suffix}"
    counter = 0
    while candidate.exists():
        counter += 1
        candidate = root / f"{base_name}_{counter}{suffix}"
    return candidate


def create_archive_dir(root: Path, base_name: str) -> Path:
    """Atomically create a uniquely-named archive directory under *root*.

    Combines [`unique_archive_path`][terok.lib.util.fs.unique_archive_path] with ``mkdir(exist_ok=False)``
    in a retry loop to guarantee the returned directory was freshly created
    by this call — safe against concurrent processes.

    *root* is created (with parents) if it does not already exist.
    """
    ensure_dir(root)
    while True:
        candidate = unique_archive_path(root, base_name)
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            continue


def create_archive_file(root: Path, base_name: str, suffix: str = ".tar.gz") -> Path:
    """Atomically create a uniquely-named archive file path under *root*.

    Uses ``os.open`` with ``O_CREAT | O_EXCL`` in a retry loop to
    guarantee the returned path was freshly claimed by this call —
    safe against concurrent processes.

    *root* is created (with parents) if it does not already exist.
    The file is created empty; the caller is responsible for writing content.
    """
    ensure_dir(root)
    while True:
        candidate = unique_archive_path(root, base_name, suffix=suffix)
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return candidate
        except FileExistsError:
            continue
