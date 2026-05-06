# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the operator-facing credential management helpers.

Covers
[`revoke_credentials`][terok.lib.domain.task_credentials.revoke_credentials]
and
[`audit_credentials`][terok.lib.domain.task_credentials.audit_credentials]
without standing up a real broker — both paths talk to the credential
DB and JSONL file directly, so the test surface is honest about the
sandbox boundary.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from terok.lib.domain.task_credentials import (
    audit_credentials,
    revoke_credentials,
)

# ── Shared fixtures ────────────────────────────────────────────────────


@pytest.fixture
def vault_db_path(tmp_path: Path) -> Path:
    """Spin up a real CredentialDB at a tmp path and return the file path."""
    from terok_sandbox import CredentialDB

    db = CredentialDB(tmp_path / "credentials.db")
    db.close()
    return tmp_path / "credentials.db"


@pytest.fixture
def patched_sandbox_config(tmp_path: Path, vault_db_path: Path):
    """Stub ``make_sandbox_config()`` so the helpers see *tmp_path*'s vault.

    Both [`vault_db`][terok.lib.domain.vault.vault_db] and
    [`audit_credentials`][terok.lib.domain.task_credentials.audit_credentials]
    import ``make_sandbox_config`` lazily inside the function body, so
    the patch target is the canonical definition site rather than the
    callers' module namespace.
    """
    from types import SimpleNamespace

    audit_path = tmp_path / "credential_audit.jsonl"
    cfg = SimpleNamespace(db_path=vault_db_path, credential_audit_log_path=audit_path)
    with patch("terok.lib.core.config.make_sandbox_config", return_value=cfg):
        yield cfg


def _seed_audit_lines(audit_path: Path, entries: list[dict]) -> None:
    """Write *entries* as JSONL to *audit_path*."""
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


# ── revoke_credentials ─────────────────────────────────────────────────


class TestRevokeCredentials:
    """The CLI verb's underlying call drops every phantom for a (project, task) pair."""

    def test_returns_count_of_revoked_tokens(self, patched_sandbox_config) -> None:
        """Two minted tokens for the same task → two revoked."""
        from terok_sandbox import CredentialDB

        db = CredentialDB(patched_sandbox_config.db_path)
        try:
            db.create_token("proj", "task-1", "default", "claude")
            db.create_token("proj", "task-1", "default", "openai")
            db.create_token("proj", "task-2", "default", "claude")  # different task
        finally:
            db.close()

        assert revoke_credentials("proj", "task-1") == 2
        # Other task survived untouched — verify by re-revoking; should
        # find exactly one.
        assert revoke_credentials("proj", "task-2") == 1

    def test_idempotent_on_unknown_task(self, patched_sandbox_config) -> None:
        """Revoking a task with no tokens yields zero — no error."""
        assert revoke_credentials("proj", "ghost") == 0


# ── audit_credentials ──────────────────────────────────────────────────


class TestAuditCredentials:
    """Filter the broker's audit JSONL by project, task, and the optional flags."""

    def test_filters_by_scope_and_subject(self, patched_sandbox_config) -> None:
        _seed_audit_lines(
            patched_sandbox_config.credential_audit_log_path,
            [
                {
                    "ts": "2026-05-05T12:00:00+00:00",
                    "scope": "proj",
                    "subject": "task-1",
                    "provider": "claude",
                    "method": "POST",
                    "path": "/v1/messages",
                    "status": 200,
                    "outcome": "ok",
                    "duration_ms": 110,
                },
                {
                    "ts": "2026-05-05T12:00:01+00:00",
                    "scope": "proj",
                    "subject": "task-2",
                    "provider": "claude",
                    "method": "POST",
                    "path": "/v1/messages",
                    "status": 200,
                    "outcome": "ok",
                    "duration_ms": 120,
                },
                {
                    "ts": "2026-05-05T12:00:02+00:00",
                    "scope": "other",
                    "subject": "task-1",
                    "provider": "claude",
                    "method": "POST",
                    "path": "/v1/messages",
                    "status": 200,
                    "outcome": "ok",
                    "duration_ms": 130,
                },
            ],
        )
        results = list(audit_credentials("proj", "task-1"))
        assert len(results) == 1
        assert results[0]["subject"] == "task-1"
        assert results[0]["scope"] == "proj"

    def test_provider_filter(self, patched_sandbox_config) -> None:
        _seed_audit_lines(
            patched_sandbox_config.credential_audit_log_path,
            [
                {
                    "ts": "2026-05-05T12:00:00+00:00",
                    "scope": "p",
                    "subject": "t",
                    "provider": "claude",
                    "method": "GET",
                    "path": "/x",
                    "status": 200,
                    "outcome": "ok",
                    "duration_ms": 10,
                },
                {
                    "ts": "2026-05-05T12:00:01+00:00",
                    "scope": "p",
                    "subject": "t",
                    "provider": "openai",
                    "method": "GET",
                    "path": "/x",
                    "status": 200,
                    "outcome": "ok",
                    "duration_ms": 11,
                },
            ],
        )
        rows = list(audit_credentials("p", "t", provider="openai"))
        assert len(rows) == 1
        assert rows[0]["provider"] == "openai"

    def test_since_filter_drops_older_lines(self, patched_sandbox_config) -> None:
        _seed_audit_lines(
            patched_sandbox_config.credential_audit_log_path,
            [
                {
                    "ts": "2026-05-05T11:00:00+00:00",
                    "scope": "p",
                    "subject": "t",
                    "provider": "claude",
                    "method": "GET",
                    "path": "/x",
                    "status": 200,
                    "outcome": "ok",
                    "duration_ms": 10,
                },
                {
                    "ts": "2026-05-05T12:30:00+00:00",
                    "scope": "p",
                    "subject": "t",
                    "provider": "claude",
                    "method": "GET",
                    "path": "/x",
                    "status": 200,
                    "outcome": "ok",
                    "duration_ms": 11,
                },
            ],
        )
        rows = list(
            audit_credentials("p", "t", since=datetime.fromisoformat("2026-05-05T12:00:00+00:00"))
        )
        assert len(rows) == 1
        assert rows[0]["ts"] == "2026-05-05T12:30:00+00:00"

    def test_tail_keeps_only_last_n(self, patched_sandbox_config) -> None:
        _seed_audit_lines(
            patched_sandbox_config.credential_audit_log_path,
            [
                {
                    "ts": f"2026-05-05T12:00:0{i}+00:00",
                    "scope": "p",
                    "subject": "t",
                    "provider": "claude",
                    "method": "GET",
                    "path": "/x",
                    "status": 200,
                    "outcome": "ok",
                    "duration_ms": i,
                }
                for i in range(5)
            ],
        )
        rows = list(audit_credentials("p", "t", tail=2))
        assert len(rows) == 2
        assert rows[-1]["duration_ms"] == 4

    def test_missing_audit_file_yields_empty(self, patched_sandbox_config) -> None:
        """A broker that's never written audit yet returns an empty iterator."""
        assert list(audit_credentials("p", "t")) == []

    def test_malformed_lines_skipped(self, patched_sandbox_config) -> None:
        """One bad JSON line doesn't poison the rest of the stream."""
        path = patched_sandbox_config.credential_audit_log_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            fh.write("not json at all\n")
            fh.write(json.dumps({"scope": "p", "subject": "t", "provider": "claude"}) + "\n")
            fh.write("\n")  # empty line — also tolerated
            fh.write('"just a string"\n')  # valid JSON but not a dict — skipped
        rows = list(audit_credentials("p", "t"))
        assert len(rows) == 1
        assert rows[0]["provider"] == "claude"

    def test_naive_threshold_rejected_at_helper_boundary(self, patched_sandbox_config) -> None:
        """``audit_credentials`` defends against naive thresholds even in-process.

        The CLI rejects naive inputs at parse time, but the helper itself
        also asserts the invariant so programmatic callers can't slip
        an aware-vs-naive ``TypeError`` past the boundary.
        """
        _seed_audit_lines(
            patched_sandbox_config.credential_audit_log_path,
            [
                {
                    "ts": "2026-05-05T12:00:00+00:00",
                    "scope": "p",
                    "subject": "t",
                    "provider": "claude",
                    "method": "GET",
                    "path": "/x",
                    "status": 200,
                    "outcome": "ok",
                    "duration_ms": 1,
                },
            ],
        )
        with pytest.raises(ValueError, match="offset-aware"):
            list(audit_credentials("p", "t", since=datetime(2026, 5, 5)))

    def test_malformed_timestamp_kept_under_since_filter(self, patched_sandbox_config) -> None:
        """Lines whose ``ts`` doesn't parse round through — forensic conservatism."""
        _seed_audit_lines(
            patched_sandbox_config.credential_audit_log_path,
            [
                {
                    "ts": "not-a-timestamp",
                    "scope": "p",
                    "subject": "t",
                    "provider": "claude",
                    "method": "GET",
                    "path": "/x",
                    "status": 200,
                    "outcome": "ok",
                    "duration_ms": 10,
                },
            ],
        )
        rows = list(
            audit_credentials("p", "t", since=datetime.fromisoformat("2026-05-05T12:00:00+00:00"))
        )
        assert len(rows) == 1
