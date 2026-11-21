# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Files that carry a task's tokens or a project's configuration are private whatever the umask."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from terok.lib.orchestration.tasks.meta import _atomic_write
from terok.lib.util.fs import create_archive_file


@pytest.fixture
def permissive_umask() -> Iterator[None]:
    """Run under the common ``022`` umask, which would leave new files world-readable."""
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_task_metadata_is_written_private(tmp_path: Path, permissive_umask: None) -> None:
    """The staged metadata, and so the renamed file, is owner-only."""
    target = tmp_path / "task.yml"
    _atomic_write(target, "gate_token: secret\n")
    assert target.read_text() == "gate_token: secret\n"
    assert _mode(target) == 0o600


def test_a_stale_loose_stage_is_tightened_before_reuse(
    tmp_path: Path, permissive_umask: None
) -> None:
    """A crash's leftover ``.tmp`` with loose permissions cannot carry the next write."""
    target = tmp_path / "task.yml"
    stale = tmp_path / "task.yml.tmp"
    stale.write_text("old")
    stale.chmod(0o644)
    _atomic_write(target, "fresh\n")
    assert target.read_text() == "fresh\n"
    assert _mode(target) == 0o600
    assert not stale.exists()


def test_project_archive_file_is_private(tmp_path: Path, permissive_umask: None) -> None:
    """The archive that will hold project configuration is owner-only from creation."""
    archive = create_archive_file(tmp_path / "archive", "proj")
    assert archive.is_file()
    assert _mode(archive) == 0o600
