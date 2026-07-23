# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Persist build/run output — terok glue over the terok-util capture facility.

The capture mechanism (pty tee, journald/file sinks) lives in
[`terok_util.output_capture`][terok_util.output_capture]; this module adds
only the terok-specific bits: the ``TEROK_KIND``/``PROJECT``/``TASK``
journald fields and the file-fallback path under
[`core_state_dir`][terok.lib.core.paths.core_state_dir].  The CLI command
handlers wrap an operation with [`tee_output`][terok.lib.util.output_capture.tee_output];
everything below the seam is shared with the rest of the fleet.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from terok_util import tee_output as _util_tee_output

_IDENTIFIER = "terok"
"""``SYSLOG_IDENTIFIER`` for captured build/run output in journald."""


def _logs_dir(project: str | None) -> Path:
    """Return the log directory for *project* (or a global dir when None)."""
    from ..core.paths import core_state_dir

    base = core_state_dir()
    if project and os.sep not in project and ".." not in project:
        return base / "projects" / project / "logs"
    return base / "logs"


def _log_file_path(kind: str, project: str | None, task_id: str | None) -> Path:
    """Build a timestamped fallback log-file path for one *kind* of operation."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{kind}-{task_id}-{stamp}" if task_id else f"{kind}-{stamp}"
    return _logs_dir(project) / f"{stem}.log"


@contextlib.contextmanager
def tee_output(
    kind: str, *, project: str | None = None, task_id: str | None = None
) -> Iterator[None]:
    """Capture a build/run operation's output to journald or a log file.

    Thin wrapper over
    [`terok_util.output_capture.tee_output`][terok_util.output_capture.tee_output]:
    labels the stream with terok's journald fields and resolves the
    file-fallback path lazily under the core state dir.

    Args:
        kind: Operation label — ``"build"`` or ``"run"``.
        project: Owning project name, when known.
        task_id: Owning task id, when known.
    """
    fields = {"TEROK_KIND": kind}
    if project:
        fields["TEROK_PROJECT"] = project
    if task_id:
        fields["TEROK_TASK"] = task_id
    with _util_tee_output(
        _IDENTIFIER,
        fields=fields,
        file_path_fn=lambda: _log_file_path(kind, project, task_id),
    ):
        yield


__all__ = ["tee_output"]
