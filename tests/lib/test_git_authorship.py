# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for terok's shared Git authorship helper script."""

import json
import shlex
import subprocess
import unittest
from importlib import resources


class GitAuthorshipHelperTests(unittest.TestCase):
    """Verify the shared shell helper applies the configured authorship modes."""

    def _apply_mode(self, mode: str) -> dict[str, str | None]:
        helper = resources.files("terok") / "resources" / "scripts" / "terok-git-identity.sh"
        with resources.as_file(helper) as helper_path:
            shell = f"""
set -euo pipefail
. {shlex.quote(str(helper_path))}
export HUMAN_GIT_NAME="Alice Example"
export HUMAN_GIT_EMAIL="alice@example.com"
export TEROK_GIT_AUTHORSHIP={shlex.quote(mode)}
export GIT_COMMITTER_NAME="stale"
export GIT_COMMITTER_EMAIL="stale@example.com"
_terok_apply_git_identity "Codex" "noreply@openai.com"
python3 - <<'PY'
import json
import os

keys = (
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)
print(json.dumps({{key: os.environ.get(key) for key in keys}}))
PY
"""
            result = subprocess.run(
                ["bash", "-lc", shell],
                check=True,
                capture_output=True,
                text=True,
            )
        return json.loads(result.stdout)

    def test_agent_human_mode(self) -> None:
        env = self._apply_mode("agent-human")
        self.assertEqual(env["GIT_AUTHOR_NAME"], "Codex")
        self.assertEqual(env["GIT_AUTHOR_EMAIL"], "noreply@openai.com")
        self.assertEqual(env["GIT_COMMITTER_NAME"], "Alice Example")
        self.assertEqual(env["GIT_COMMITTER_EMAIL"], "alice@example.com")

    def test_human_agent_mode(self) -> None:
        env = self._apply_mode("human-agent")
        self.assertEqual(env["GIT_AUTHOR_NAME"], "Alice Example")
        self.assertEqual(env["GIT_AUTHOR_EMAIL"], "alice@example.com")
        self.assertEqual(env["GIT_COMMITTER_NAME"], "Codex")
        self.assertEqual(env["GIT_COMMITTER_EMAIL"], "noreply@openai.com")

    def test_human_mode_unsets_committer(self) -> None:
        env = self._apply_mode("human")
        self.assertEqual(env["GIT_AUTHOR_NAME"], "Alice Example")
        self.assertEqual(env["GIT_AUTHOR_EMAIL"], "alice@example.com")
        self.assertIsNone(env["GIT_COMMITTER_NAME"])
        self.assertIsNone(env["GIT_COMMITTER_EMAIL"])

    def test_agent_mode_unsets_committer(self) -> None:
        env = self._apply_mode("agent")
        self.assertEqual(env["GIT_AUTHOR_NAME"], "Codex")
        self.assertEqual(env["GIT_AUTHOR_EMAIL"], "noreply@openai.com")
        self.assertIsNone(env["GIT_COMMITTER_NAME"])
        self.assertIsNone(env["GIT_COMMITTER_EMAIL"])

    def test_invalid_mode_falls_back_to_agent_human(self) -> None:
        env = self._apply_mode("invalid-mode")
        self.assertEqual(env["GIT_AUTHOR_NAME"], "Codex")
        self.assertEqual(env["GIT_COMMITTER_NAME"], "Alice Example")
