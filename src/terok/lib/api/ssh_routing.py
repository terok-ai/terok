# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Linking vault SSH keys to project scopes — public API surface.

A deploy key in the vault grants git access to every project it is
*assigned* to; one key can serve many projects (a derived project reuses
its source's key) and one project can hold several keys.  This module
exposes that many-to-many relationship as an editable whole — the row
axis (every stored key), the column axis (every project, plus any scope
that still holds an assignment after its project is gone), and the set of
links between them — so a frontend can render and re-wire it directly.

Mutations are granular — one link or one key — so an editor toggles a
single connection in place.  Removing a key's last link deletes the
keypair: the vault keeps no unassigned keys, so "no longer needed
anywhere" and "unlink its final project" are the same act.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from terok.lib.core.projects import discover_projects
from terok.lib.domain.project import get_project
from terok.lib.domain.vault import vault_db

if TYPE_CHECKING:
    from terok.lib.integrations.sandbox import SSHInitResult, SSHKeyRow

#: Sandbox-reserved scopes (the host krun keypair and future ``%name``
#: slots) carry this sigil.  They are never operator-editable here, so
#: they are kept off the project axis.
_INFRA_SCOPE_SIGIL = "%"


@dataclass(frozen=True)
class KeyRouting:
    """A snapshot of which SSH keys are linked to which project scopes.

    The two axes plus the links between them are everything a routing
    view needs: ``keys`` are the rows, ``projects`` the columns, and a
    cell ``(project, key_id)`` is wired exactly when it appears in
    ``links``.  Immutable — re-fetch with
    [`load_key_routing`][terok.lib.api.ssh_routing.load_key_routing]
    after a mutation rather than editing in place.
    """

    keys: tuple[SSHKeyRow, ...]
    """Every keypair stored in the vault, ordered by id — the row axis."""

    projects: tuple[str, ...]
    """Every project scope a key may be linked to, sorted — the column
    axis.  Includes orphaned scopes (an assignment outlived its project)
    so a stale link stays visible and removable."""

    links: frozenset[tuple[str, int]]
    """The ``(scope, key_id)`` pairs that are currently wired."""


def load_key_routing() -> KeyRouting:
    """Assemble the current key↔project routing from the vault.

    Raises whatever [`vault_db`][terok.lib.domain.vault.vault_db] raises
    when the store is locked — the caller decides how to surface that.
    """
    valid, _broken = discover_projects()
    project_names = {p.name for p in valid}
    with vault_db() as db:
        keys = tuple(db.list_all_ssh_keys())
        links = frozenset(db.list_ssh_key_assignments())
    orphaned = {scope for scope, _ in links if not scope.startswith(_INFRA_SCOPE_SIGIL)}
    projects = tuple(sorted(project_names | orphaned))
    return KeyRouting(keys=keys, projects=projects, links=links)


def link_key(scope: str, key_id: int) -> None:
    """Grant project *scope* access to *key_id* (idempotent)."""
    with vault_db() as db:
        db.assign_ssh_key(scope, key_id)


def unlink_key(scope: str, key_id: int) -> None:
    """Revoke project *scope*'s access to *key_id*.

    If this was the key's last link the keypair is dropped from the vault
    — there is no such thing as an unassigned key.  Check
    [`is_last_link`][terok.lib.api.ssh_routing.is_last_link] first when
    that distinction matters to the operator.
    """
    with vault_db() as db:
        db.unassign_ssh_key(scope, key_id)


def delete_key(key_id: int) -> None:
    """Remove *key_id* from every project at once, deleting the keypair."""
    with vault_db() as db:
        db.delete_ssh_key(key_id)


def mint_key(
    project_name: str, *, key_type: str = "ed25519", comment: str | None = None
) -> SSHInitResult:
    """Generate a fresh keypair already linked to *project_name*.

    A key is born attached to a project (the vault holds no unlinked
    keys), so minting always names the column it lands in.
    """
    return get_project(project_name).provision_ssh_key(key_type=key_type, comment=comment)


def rename_key(fingerprint: str, comment: str) -> bool:
    """Set a key's comment — the label shown in listings and ``ssh-add -L``.

    Returns ``True`` when a key with *fingerprint* was updated.  Raises
    when *comment* carries control characters or is over-long: the vault
    validates it at the storage boundary, and the caller surfaces that.
    """
    with vault_db() as db:
        return db.set_ssh_key_comment(fingerprint, comment)


def is_last_link(routing: KeyRouting, scope: str, key_id: int) -> bool:
    """Return ``True`` when *scope* is the only project holding *key_id*.

    Unlinking such a cell deletes the keypair, so an editor can confirm
    before crossing that threshold rather than after.
    """
    return [s for s, k in routing.links if k == key_id] == [scope]


__all__ = [
    "KeyRouting",
    "delete_key",
    "is_last_link",
    "link_key",
    "load_key_routing",
    "mint_key",
    "rename_key",
    "unlink_key",
]
