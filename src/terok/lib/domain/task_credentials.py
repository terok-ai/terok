# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Operator-facing credential management for live tasks.

Two verbs ride alongside the rest of ``terok task ...``:

* [`revoke_credentials`][terok.lib.domain.task_credentials.revoke_credentials]
  — nuke every phantom token bound to a ``(project, task)`` pair.  The
  in-flight request that already reached the broker still completes;
  every subsequent call from the container's agent gets a 401.
  Operator response to "audit shows this task is misbehaving"
  without killing the container itself, so the forensic state stays
  intact for inspection.
* [`audit_credentials`][terok.lib.domain.task_credentials.audit_credentials]
  — filter the broker's append-only credential-audit JSONL by the
  task's ``(scope, subject)`` pair, with optional provider / time /
  tail filters.

Both translate the operator's project/task identifiers into the opaque
``(scope, subject)`` pair the sandbox actually keys on.  ``scope`` is
the project id; ``subject`` is the task id.  The mapping lives here
rather than in ``terok-sandbox`` because the sandbox makes no claim
about what those labels identify.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


def revoke_credentials(project_id: str, task_id: str) -> int:
    """Revoke every phantom token bound to ``(project_id, task_id)``.

    The DB-side delete takes effect on the next request the agent
    issues — the broker has no in-memory token cache, so an in-flight
    proxy call already past
    [`lookup_token`][terok_sandbox.credentials.db.CredentialDB.lookup_token]
    completes on its real credential.  That's the intended semantics:
    block further use, don't kill in-flight TCP.

    Args:
        project_id: Project id (becomes the token's ``scope``).
        task_id: Task id (becomes the token's ``subject``).

    Returns:
        Number of phantom tokens removed (zero if there were none —
        idempotent).
    """
    from .vault import vault_db

    with vault_db() as db:
        return db.revoke_tokens(project_id, task_id)


def audit_credentials(
    project_id: str,
    task_id: str,
    *,
    provider: str | None = None,
    since: datetime | None = None,
    tail: int | None = None,
) -> Iterator[dict]:
    """Yield audit lines for ``(project_id, task_id)`` after filtering.

    Reads the broker's
    [`credential_audit_log_path`][terok_sandbox.SandboxConfig.credential_audit_log_path]
    JSONL, drops lines whose ``scope`` / ``subject`` don't match the
    target task, applies the optional provider / time filters, and
    keeps only the last ``tail`` survivors when set.

    Args:
        project_id: Project id (matched against the line's ``scope``).
        task_id: Task id (matched against the line's ``subject``).
        provider: When set, drop lines whose ``provider`` field differs.
        since: When set, drop lines whose ``ts`` parses earlier than
            this datetime.  Lines with malformed timestamps are kept
            (the sanitiser invariant guarantees printable ASCII, but
            mismatched ISO-8601 shapes are still possible from
            future schema changes).
        tail: When set, yield only the last ``N`` matching lines.

    Yields:
        Decoded JSON dicts, one per surviving line.
    """
    from ..core.config import make_sandbox_config

    audit_path = make_sandbox_config().credential_audit_log_path
    matches: Iterable[dict] = _filtered_lines(
        audit_path, project_id, task_id, provider=provider, since=since
    )
    if tail is not None and tail > 0:
        # Materialise to slice from the tail; full-history scans rarely
        # outpace the operator's terminal so the cost is negligible.
        matches = list(matches)[-tail:]
    yield from matches


def _filtered_lines(
    audit_path: Path,
    project_id: str,
    task_id: str,
    *,
    provider: str | None,
    since: datetime | None,
) -> Iterator[dict]:
    """Yield audit-line dicts that pass every active filter.

    Soft-fails on the file side: a missing audit log yields an empty
    iterator (the broker may not have written anything yet, or the
    operator's local install hasn't seen credential traffic), and a
    malformed JSON line is skipped with no exception so one bad write
    can't poison the whole stream.
    """
    if not audit_path.is_file():
        return
    with audit_path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            entry = _parse_line(raw)
            if entry is None:
                continue
            if entry.get("scope") != project_id or entry.get("subject") != task_id:
                continue
            if provider is not None and entry.get("provider") != provider:
                continue
            if since is not None and not _on_or_after(entry.get("ts", ""), since):
                continue
            yield entry


def _parse_line(raw: str) -> dict | None:
    """Decode one JSONL line, ``None`` on malformed input."""
    text = raw.strip()
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _on_or_after(ts_value: str, threshold: datetime) -> bool:
    """Compare an audit-line timestamp against *threshold*.

    Lines with malformed / missing timestamps round to ``True`` —
    forensically a "we don't know when this happened, surface it" is
    safer than dropping it during a ``--since`` filter.

    *threshold* must be offset-aware.  Audit lines record UTC, so
    comparing an aware line timestamp to a naive threshold raises
    ``TypeError`` at runtime; the CLI rejects naive inputs at parse
    time, and this assert keeps programmatic callers honest.
    """
    from datetime import datetime as _dt

    if threshold.tzinfo is None:
        raise ValueError("audit_credentials threshold must be offset-aware")
    try:
        when = _dt.fromisoformat(ts_value)
    except ValueError:
        return True
    return when >= threshold


__all__ = ["audit_credentials", "revoke_credentials"]
