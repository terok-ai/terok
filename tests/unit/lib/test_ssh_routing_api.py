# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the SSH key ↔ project routing API.

The vault DB and project discovery are stubbed so the assembly logic
(row axis, column axis with orphan scopes, infra exclusion) and the
mutating verbs can be checked without an encrypted store on disk.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from terok.lib.api import ssh_routing


class _FakeDB:
    """A minimal stand-in for ``CredentialDB``'s SSH-routing surface."""

    def __init__(self, keys, assignments):
        """Seed the fake with key rows and ``(scope, key_id)`` assignments."""
        self.keys = list(keys)
        self.assignments = list(assignments)
        self.calls: list[tuple] = []

    def list_all_ssh_keys(self):
        """Return the stored key rows."""
        return self.keys

    def list_ssh_key_assignments(self):
        """Return the stored assignment edges."""
        return self.assignments

    def assign_ssh_key(self, scope, key_id):
        """Record a link."""
        self.calls.append(("assign", scope, key_id))

    def unassign_ssh_key(self, scope, key_id):
        """Record an unlink."""
        self.calls.append(("unassign", scope, key_id))

    def delete_ssh_key(self, key_id):
        """Record a delete."""
        self.calls.append(("delete", key_id))

    def set_ssh_key_comment(self, fingerprint, comment):
        """Record a comment edit and report success."""
        self.calls.append(("rename", fingerprint, comment))
        return True


def _key(key_id: int):
    """A throwaway key row carrying only the id the API reads."""
    return SimpleNamespace(id=key_id, key_type="ed25519", fingerprint="fp", comment="c")


@pytest.fixture()
def db(monkeypatch):
    """Install a fake vault DB and project list; hand the DB back for assertions."""
    fake = _FakeDB(
        keys=[_key(1), _key(2)],
        assignments=[("foo", 1), ("bar", 1), ("bar", 2), ("%host", 2)],
    )

    @contextmanager
    def _vault_db():
        yield fake

    monkeypatch.setattr(ssh_routing, "vault_db", _vault_db)
    monkeypatch.setattr(
        ssh_routing,
        "discover_projects",
        lambda: ([SimpleNamespace(name="foo"), SimpleNamespace(name="quux")], []),
    )
    return fake


class TestLoadKeyRouting:
    """Verify the routing snapshot assembled from the vault and project list."""

    def test_keys_are_the_row_axis(self, db):
        """Every stored key becomes a row, ids preserved."""
        routing = ssh_routing.load_key_routing()
        assert [k.id for k in routing.keys] == [1, 2]

    def test_columns_union_projects_and_orphan_scopes(self, db):
        """Columns = on-disk projects plus scopes that still hold a link."""
        routing = ssh_routing.load_key_routing()
        # foo, quux from disk; bar is an orphan scope kept because it has links.
        assert routing.projects == ("bar", "foo", "quux")

    def test_infra_scope_is_excluded_from_columns(self, db):
        """The ``%host`` infra scope never appears on the project axis."""
        routing = ssh_routing.load_key_routing()
        assert not any(p.startswith("%") for p in routing.projects)

    def test_links_carry_every_edge(self, db):
        """The full edge set — including the infra edge — is returned verbatim."""
        routing = ssh_routing.load_key_routing()
        assert routing.links == {("foo", 1), ("bar", 1), ("bar", 2), ("%host", 2)}


class TestMutations:
    """Verify the verbs delegate to the matching DB calls."""

    def test_link_assigns(self, db):
        """link_key assigns the scope→key pair."""
        ssh_routing.link_key("quux", 2)
        assert ("assign", "quux", 2) in db.calls

    def test_unlink_unassigns(self, db):
        """unlink_key unassigns the scope→key pair."""
        ssh_routing.unlink_key("foo", 1)
        assert ("unassign", "foo", 1) in db.calls

    def test_delete_removes_key(self, db):
        """delete_key drops the whole key."""
        ssh_routing.delete_key(1)
        assert ("delete", 1) in db.calls

    def test_rename_sets_comment(self, db):
        """rename_key edits the comment by fingerprint and returns the result."""
        assert ssh_routing.rename_key("fp", "new comment") is True
        assert ("rename", "fp", "new comment") in db.calls


class TestMint:
    """Verify minting routes through the project aggregate."""

    def test_mint_provisions_for_project(self, monkeypatch):
        """mint_key calls provision_ssh_key on the named project."""
        recorded = {}
        project = SimpleNamespace(
            provision_ssh_key=lambda **kw: recorded.update(kw) or {"key_id": 7}
        )

        def fake_get_project(name):
            recorded["name"] = name
            return project

        monkeypatch.setattr(ssh_routing, "get_project", fake_get_project)
        result = ssh_routing.mint_key("foo", key_type="rsa", comment="hi")
        assert recorded["name"] == "foo"
        assert recorded["key_type"] == "rsa"
        assert recorded["comment"] == "hi"
        assert result == {"key_id": 7}


class TestIsLastLink:
    """Verify the last-link predicate that gates destructive confirmation."""

    def test_true_when_sole_scope(self):
        """A key linked to exactly one scope reports that scope as last."""
        routing = ssh_routing.KeyRouting(keys=(), projects=(), links=frozenset({("foo", 1)}))
        assert ssh_routing.is_last_link(routing, "foo", 1) is True

    def test_false_when_shared(self):
        """A key shared across scopes has no last link from either side."""
        routing = ssh_routing.KeyRouting(
            keys=(), projects=(), links=frozenset({("foo", 1), ("bar", 1)})
        )
        assert ssh_routing.is_last_link(routing, "foo", 1) is False
