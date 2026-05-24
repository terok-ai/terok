# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for SSH project initialization helpers (DB-backed)."""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.test_utils import patch_vault_db


def _patch_vault_db(db):
    """Patch the Project module's ``vault_db`` alias to yield *db*."""
    return patch_vault_db(db, module="project")


def _make_project(project_id: str) -> object:
    """Build a minimal ``Project`` whose only requirement is ``self._config.id``."""
    from terok.lib.domain.project import Project

    config = MagicMock()
    config.id = project_id
    return Project(config)


class TestRegisterSshKey:
    """Tests for ``Project.register_ssh_key``."""

    def test_assigns_key_to_scope(self) -> None:
        """register_ssh_key delegates to ``CredentialDB.assign_ssh_key``."""
        db = MagicMock()
        with _patch_vault_db(db):
            _make_project("myproj").register_ssh_key(7)
        db.assign_ssh_key.assert_called_once_with("myproj", 7)

    def test_propagates_errors_from_db(self) -> None:
        """Errors from the DB layer propagate (no silent swallowing)."""
        import pytest

        db = MagicMock()
        db.assign_ssh_key.side_effect = RuntimeError("disk full")
        with _patch_vault_db(db), pytest.raises(RuntimeError, match="disk full"):
            _make_project("proj").register_ssh_key(1)
