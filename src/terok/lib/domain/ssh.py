# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""SSH provisioning rendering helpers.

The mint / bind / pause workflow lives as methods on
[`Project`][terok.lib.domain.project.Project]:

- [`Project.provision_ssh_key`][terok.lib.domain.project.Project.provision_ssh_key]
- [`Project.register_ssh_key`][terok.lib.domain.project.Project.register_ssh_key]
- [`Project.needs_ssh_key_registration`][terok.lib.domain.project.Project.needs_ssh_key_registration]
- [`Project.pause_for_ssh_key_registration_if_needed`][terok.lib.domain.project.Project.pause_for_ssh_key_registration_if_needed]

This module keeps the one purely-presentational helper that doesn't
need project state — [`summarize_ssh_init`][terok.lib.domain.ssh.summarize_ssh_init]
prints an [`SSHInitResult`][terok_sandbox.SSHInitResult] for the CLI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terok.lib.integrations.sandbox import SSHInitResult


def summarize_ssh_init(result: SSHInitResult) -> None:
    """Render an ``ssh-init`` result for the terminal."""
    print(f"  id:          {result['key_id']}")
    print(f"  type:        {result['key_type']}")
    print(f"  fingerprint: {result['fingerprint']}")
    print(f"  comment:     {result['comment']}")
    print("Public key (register as a deploy key on the remote):")
    print(f"  {result['public_line']}")


__all__ = ["summarize_ssh_init"]
