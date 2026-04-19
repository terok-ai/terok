# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for SSH bootstrap workflows (vault DB-backed)."""

from __future__ import annotations

import pytest

from ..helpers import TerokIntegrationEnv

pytestmark = pytest.mark.needs_host_features

PROJECT_TEMPLATE = """
project:
  id: {project_id}
  security_class: gatekeeping
git:
  upstream_url: https://example.com/{project_id}.git
"""


class TestSshInit:
    """Verify ``project ssh-init`` provisions a vault-backed key via the real CLI."""

    def test_ssh_init_is_additive(self, terok_env: TerokIntegrationEnv) -> None:
        """Re-running ``ssh-init`` on the same scope adds a second key.

        ``ssh-init`` is additive by default — each call produces a fresh
        keypair alongside any existing ones.  Only ``--force`` rotates.
        """
        terok_env.write_project(
            "demo",
            PROJECT_TEMPLATE.format(project_id="demo"),
        )

        first = terok_env.run_cli("project", "ssh-init", "demo")
        second = terok_env.run_cli("project", "ssh-init", "demo")

        fp1 = _extract_fingerprint(first.stdout)
        fp2 = _extract_fingerprint(second.stdout)
        assert fp1 is not None and fp2 is not None
        assert fp1 != fp2  # two independent keys

        # The second key gets a ``tk-side:`` comment so only the first
        # remains tk-main for the signer's promotion heuristic.
        assert "tk-main:demo" in first.stdout
        assert "tk-side:demo" in second.stdout

    def test_ssh_init_rotation_picks_new_key(self, terok_env: TerokIntegrationEnv) -> None:
        """``--force`` rotates: scope ends up with a fresh key, distinct fingerprint."""
        terok_env.write_project(
            "rot",
            PROJECT_TEMPLATE.format(project_id="rot"),
        )

        initial = terok_env.run_cli("project", "ssh-init", "rot")
        rotated = terok_env.run_cli("project", "ssh-init", "rot", "--force")

        fp_initial = _extract_fingerprint(initial.stdout)
        fp_rotated = _extract_fingerprint(rotated.stdout)
        assert fp_initial is not None and fp_rotated is not None
        assert fp_initial != fp_rotated

    def test_ssh_init_respects_comment(self, terok_env: TerokIntegrationEnv) -> None:
        """``--comment`` lands verbatim in the printed public key line."""
        terok_env.write_project(
            "commented",
            PROJECT_TEMPLATE.format(project_id="commented"),
        )

        result = terok_env.run_cli(
            "project", "ssh-init", "commented", "--comment", "deploy-key-for-commented"
        )

        assert "deploy-key-for-commented" in result.stdout
        assert "ssh-ed25519 " in result.stdout


def _extract_fingerprint(stdout: str) -> str | None:
    """Pick out the ``SHA256:<hex>`` digest from the CLI summary."""
    for line in stdout.splitlines():
        if "fingerprint" in line.lower() and "SHA256:" in line:
            return line.split("SHA256:", 1)[1].strip()
    return None
