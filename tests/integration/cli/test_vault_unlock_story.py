# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""End-to-end story: locked vault → CLI hint → unlock via session-file → CLI succeeds.

Walks the full operator journey introduced in terok#877 / sandbox#278:

1. A real SQLCipher-encrypted credentials DB exists on disk with a
   known passphrase but no resolver tier has it (config tier blanked,
   keyring/systemd-creds disabled, no session-unlock file).
2. A real CLI verb (``terok project derive``) that opens the vault
   via [`vault_db`][terok.lib.domain.vault.vault_db] is exercised in a
   subprocess and surfaces the actionable hint installed by PR #936
   instead of crashing with a raw traceback.
3. The session-unlock tmpfs file is planted with the right
   passphrase.
4. The same CLI verb (different target name, since the previous
   half-derive succeeded for the filesystem step) succeeds — the
   vault opens and no vault-locked surfaces appear in the output.

Reaches all the way down to ``terok-sandbox.credentials.db`` — there
are no mocks in this path.
"""

from __future__ import annotations

import pytest

from tests.testnet import TEST_UPSTREAM_URL

from ..helpers import TerokIntegrationEnv

pytestmark = pytest.mark.needs_host_features


_SOURCE_PROJECT = f"""
project:
  id: alpha
  security_class: online
git:
  upstream_url: {TEST_UPSTREAM_URL}
  default_branch: main
ssh:
  use_personal: false
agent:
  provider: codex
"""


@pytest.mark.needs_vault
class TestVaultUnlockStory:
    """Walks the full lock → hint → unlock → success cycle through the real CLI."""

    _STORY_PASSPHRASE = "story-test-passphrase-9f7c"

    def _prime_locked_vault(self, terok_env: TerokIntegrationEnv) -> None:
        """Encrypt the credentials DB and strip every chain tier the harness seeded.

        The default ``terok_env`` fixture writes ``credentials.passphrase``
        to the user config so most integration tests can open the DB
        without a real daemon.  This story needs the *opposite* — a
        locked vault that no tier can unseal — so we have to undo that
        helper deliberately.
        """
        from terok_sandbox import CredentialDB

        db_path = terok_env.vault_dir / "credentials.db"
        CredentialDB(db_path, passphrase=self._STORY_PASSPHRASE).close()

        (terok_env.xdg_config_home / "terok" / "config.yml").write_text(
            "credentials: {}\n",
            encoding="utf-8",
        )
        (terok_env.system_config_root / "config.yml").write_text(
            "vault:\n  bypass_no_secret_protection: false\n",
            encoding="utf-8",
        )

    def test_locked_then_unlocked_via_session_file(
        self,
        terok_env: TerokIntegrationEnv,
        tmp_path,
    ) -> None:
        """The full story in one go — fails closed with the hint, then succeeds after unlock."""
        self._prime_locked_vault(terok_env)
        terok_env.write_project("alpha", _SOURCE_PROJECT)

        runtime_dir = tmp_path / "sandbox-runtime"
        runtime_dir.mkdir()
        sandbox_env = {"TEROK_SANDBOX_RUNTIME_DIR": str(runtime_dir)}

        # --- Act 1: locked vault → exit 2 + actionable hint ----------
        locked = terok_env.run_cli(
            "project",
            "derive",
            "alpha",
            "beta",
            extra_env=sandbox_env,
            check=False,
        )
        assert locked.returncode == 2, (
            f"expected exit 2 from locked-vault dispatch, got {locked.returncode}\n"
            f"stdout:\n{locked.stdout}\nstderr:\n{locked.stderr}"
        )
        assert "no SQLCipher passphrase" in locked.stderr
        assert "terok vault unlock" in locked.stderr

        # --- Act 2: drop the right passphrase into the session-unlock file
        passphrase_file = runtime_dir / "vault.passphrase"
        passphrase_file.write_text(self._STORY_PASSPHRASE + "\n", encoding="utf-8")
        passphrase_file.chmod(0o600)

        # --- Act 3: re-run with a fresh target — vault opens, derive succeeds
        # The previous run created ``beta`` on disk before failing on the
        # vault step, so a fresh target name (``gamma``) avoids the
        # "target already exists" SystemExit from ``derive_project``.
        unlocked = terok_env.run_cli(
            "project",
            "derive",
            "alpha",
            "gamma",
            extra_env=sandbox_env,
            check=False,
        )
        assert unlocked.returncode == 0, (
            f"expected derive to succeed after unlock, got {unlocked.returncode}\n"
            f"stdout:\n{unlocked.stdout}\nstderr:\n{unlocked.stderr}"
        )
        # The vault-locked surfaces from Act 1 must not reappear once the
        # session-file tier resolves cleanly.
        assert "no SQLCipher passphrase" not in unlocked.stderr
        assert "terok vault unlock" not in unlocked.stderr
        # The derived project lives where the CLI says it should.
        assert terok_env.project_root("gamma").is_dir()
