# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Structured audit logging for shield (JSON-lines format)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from .config import shield_logs_dir


def _log_path(container: str) -> Path:
    """Return the audit log path for a container."""
    logs_dir = shield_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir / f"{container}.jsonl"


def log_event(
    container: str,
    action: str,
    *,
    dest: str | None = None,
    detail: str | None = None,
) -> None:
    """Write a single audit event to the container's log file.

    Args:
        container: Container name.
        action: Event type (setup, teardown, allowed, denied).
        dest: Destination IP/domain (optional).
        detail: Additional detail string (optional).
    """
    entry: dict = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "container": container,
        "action": action,
    }
    if dest is not None:
        entry["dest"] = dest
    if detail is not None:
        entry["detail"] = detail

    try:
        with _log_path(container).open("a") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except OSError:
        pass  # audit logging is best-effort


def tail_log(container: str, n: int = 50) -> Iterator[dict]:
    """Yield the last *n* audit events for a container.

    Args:
        container: Container name.
        n: Number of recent events to yield.
    """
    path = _log_path(container)
    if not path.is_file():
        return

    lines = path.read_text().splitlines()
    for line in lines[-n:]:
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def list_log_files() -> list[str]:
    """Return container names that have audit logs."""
    logs_dir = shield_logs_dir()
    if not logs_dir.is_dir():
        return []
    return sorted(f.stem for f in logs_dir.glob("*.jsonl"))
