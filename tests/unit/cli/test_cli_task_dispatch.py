# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for ``terok task`` subcommand dispatch and credential handlers.

These pin the CLI command table — every task-scoped subcommand must forward the
*project name* (slug) and resolved task id to its service function — and exercise
the phantom-token / audit handlers' output branches.
"""

from __future__ import annotations

import argparse
from unittest import mock

import pytest

from terok.cli.commands import task as task_mod

_PROJ = "demoproj"
_TID = "swift-otter"


def _ns(**kw: object) -> argparse.Namespace:
    return argparse.Namespace(**kw)


# (task_cmd, extra args, patched symbol, expected call) — task-scoped routing.
_ROUTES = [
    (
        "new",
        {"name": "fresh"},
        "task_new",
        mock.call(_PROJ, name="fresh"),
    ),
    ("delete", {}, "task_delete", mock.call(_PROJ, _TID)),
    ("stop", {"timeout": 9}, "task_stop", mock.call(_PROJ, _TID, timeout=9)),
    ("rename", {"name": "renamed"}, "task_rename", mock.call(_PROJ, _TID, "renamed")),
    ("status", {}, "task_status", mock.call(_PROJ, _TID)),
]


@pytest.mark.parametrize(("cmd", "extra", "symbol", "expected"), _ROUTES)
def test_task_subcommand_routes_with_project_name(cmd, extra, symbol, expected) -> None:  # type: ignore[no-untyped-def]
    """Each task subcommand forwards the project name (+ task id) to its service."""
    args = _ns(task_cmd=cmd, project_name=_PROJ, task_id=_TID, **extra)
    with (
        mock.patch.object(task_mod, "require_project_exists"),
        mock.patch.object(task_mod, "resolve_task_id", return_value=_TID),
        mock.patch.object(task_mod, "task_delete", return_value=mock.Mock(warnings=[])),
        mock.patch.object(task_mod, symbol) as target,
    ):
        assert task_mod._dispatch_task_sub(args) is True
    if symbol != "task_delete":  # delete is pre-patched above for its result.warnings access
        assert target.call_args == expected


def test_new_skips_task_id_resolution() -> None:
    """``task new`` creates without resolving a task id (none exists yet)."""
    args = _ns(task_cmd="new", project_name=_PROJ, name=None)
    with (
        mock.patch.object(task_mod, "resolve_task_id") as resolve,
        mock.patch.object(task_mod, "task_new") as new,
    ):
        assert task_mod._dispatch_task_sub(args) is True
    new.assert_called_once_with(_PROJ, name=None)
    resolve.assert_not_called()


def test_archive_routes_to_archive_dispatch() -> None:
    args = _ns(task_cmd="archive", project_name=_PROJ, task_id=_TID, archive_cmd="list")
    with (
        mock.patch.object(task_mod, "require_project_exists"),
        mock.patch.object(task_mod, "resolve_task_id", return_value=_TID),
        mock.patch.object(task_mod, "task_archive_list") as lister,
    ):
        assert task_mod._dispatch_task_sub(args) is True
    lister.assert_called_once_with(_PROJ)


class TestArchiveDispatch:
    """``task archive`` subcommands forward the project name."""

    def test_list(self) -> None:
        args = _ns(archive_cmd="list", project_name=_PROJ)
        with mock.patch.object(task_mod, "task_archive_list") as m:
            assert task_mod._dispatch_archive_sub(args) is True
        m.assert_called_once_with(_PROJ)

    def test_logs_streams_resolved_file(self, tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
        log = tmp_path / "archive.log"
        log.write_text("hello from archive\n", encoding="utf-8")
        args = _ns(archive_cmd="logs", project_name=_PROJ, archive_id="42")
        with mock.patch.object(task_mod, "task_archive_logs", return_value=log) as m:
            assert task_mod._dispatch_archive_sub(args) is True
        m.assert_called_once_with(_PROJ, "42")
        assert "hello from archive" in capsys.readouterr().out

    def test_logs_missing_archive_exits(self) -> None:
        args = _ns(archive_cmd="logs", project_name=_PROJ, archive_id="42")
        with mock.patch.object(task_mod, "task_archive_logs", return_value=None):
            with pytest.raises(SystemExit, match="No archived logs"):
                task_mod._dispatch_archive_sub(args)


class TestRevokeCredentials:
    """``task revoke-credentials`` reports the phantom-token count."""

    def test_no_tokens(self, capsys: pytest.CaptureFixture[str]) -> None:
        with mock.patch("terok.lib.domain.task_credentials.revoke_credentials", return_value=0):
            task_mod._cmd_task_revoke_credentials(_PROJ, _TID)
        assert "No phantom tokens" in capsys.readouterr().out

    def test_pluralizes_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        with mock.patch("terok.lib.domain.task_credentials.revoke_credentials", return_value=2):
            task_mod._cmd_task_revoke_credentials(_PROJ, _TID)
        out = capsys.readouterr().out
        assert "Revoked 2 phantom tokens" in out and f"{_PROJ}/{_TID}" in out


class TestAuditCredentials:
    """``task audit-credentials`` renders or JSON-dumps broker audit rows."""

    def test_empty_prints_placeholder(self, capsys: pytest.CaptureFixture[str]) -> None:
        with mock.patch("terok.lib.domain.task_credentials.audit_credentials", return_value=[]):
            task_mod._cmd_task_audit_credentials(_PROJ, _TID, _ns(json_output=False))
        assert "No credential-audit entries" in capsys.readouterr().out

    def test_table_renders_rows(self, capsys: pytest.CaptureFixture[str]) -> None:
        row = {"ts": "2026-01-01", "provider": "claude", "method": "lookup", "path": "/x"}
        with mock.patch("terok.lib.domain.task_credentials.audit_credentials", return_value=[row]):
            task_mod._cmd_task_audit_credentials(_PROJ, _TID, _ns(json_output=False))
        out = capsys.readouterr().out
        assert "claude" in out and "lookup" in out

    def test_rejects_naive_since(self) -> None:
        with pytest.raises(SystemExit, match="naive"):
            task_mod._cmd_task_audit_credentials(
                _PROJ, _TID, _ns(json_output=False, since="2026-01-01T00:00:00")
            )
