# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""SSH provisioning workflow — mint, bind, render, and pause for deploy-key registration.

The four public verbs form the user-facing ``ssh-init`` story: mint the
keypair, bind it to the project's vault scope, render the result for the
human, and (when the upstream is SSH-scheme) pause so the user can register
the deploy key upstream before gate-sync.

This module hosts the workflow; the lower-level
[`SSHManager`][terok_sandbox.SSHManager] factory lives in
[`make_ssh_manager`][terok.lib.domain.project.make_ssh_manager].
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.projects import load_project
from .vault import vault_db

if TYPE_CHECKING:
    from terok_sandbox.credentials.ssh import SSHInitResult


def provision_ssh_key(
    project_id: str,
    *,
    key_type: str = "ed25519",
    comment: str | None = None,
    force: bool = False,
) -> SSHInitResult:
    """Mint a vault-backed keypair for *project_id* and bind it to the project (scope).

    Single entry point for both the CLI and the TUI.  Rendering the
    result for the user is the caller's job — see
    [`summarize_ssh_init`][terok.lib.domain.ssh.summarize_ssh_init].
    """
    from .project import make_ssh_manager

    project = load_project(project_id)
    with make_ssh_manager(project) as ssh:
        result = ssh.init(key_type=key_type, comment=comment, force=force)
    register_ssh_key(project_id, result["key_id"])
    return result


def register_ssh_key(project_id: str, key_id: int) -> None:
    """Bind an already-minted *key_id* to *project_id* (idempotent)."""
    with vault_db() as db:
        db.assign_ssh_key(project_id, key_id)


def summarize_ssh_init(result: SSHInitResult) -> None:
    """Render an ``ssh-init`` result for the terminal."""
    print(f"  id:          {result['key_id']}")
    print(f"  type:        {result['key_type']}")
    print(f"  fingerprint: {result['fingerprint']}")
    print(f"  comment:     {result['comment']}")
    print("Public key (register as a deploy key on the remote):")
    print(f"  {result['public_line']}")


def project_needs_key_registration(project_id: str) -> bool:
    """Return True when the project's upstream is SSH-scheme, so a deploy key must be added.

    Shared predicate used by the CLI's pause helper below and the TUI
    wizard's mid-flow "continue" gate — keeps the rule (SSH URLs need
    registration, HTTPS and no-upstream projects don't) in one place.
    """
    from terok_sandbox import is_ssh_url

    try:
        project = load_project(project_id)
    except SystemExit:
        return False
    return bool(project.upstream_url) and is_ssh_url(project.upstream_url)


def maybe_pause_for_ssh_key_registration(project_id: str) -> None:
    """Pause so the user can register the deploy key, but only for SSH upstreams."""
    if project_needs_key_registration(project_id):
        print("\n" + "=" * 60)
        print("ACTION REQUIRED: Add the public key shown above as a")
        print("deploy key (or to your SSH keys) on the git remote.")
        print("=" * 60)
        input("Press Enter once the key is registered... ")


__all__ = [
    "provision_ssh_key",
    "register_ssh_key",
    "summarize_ssh_init",
    "project_needs_key_registration",
    "maybe_pause_for_ssh_key_registration",
]
