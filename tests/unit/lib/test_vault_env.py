# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for terok-specific vault env/policy selection.

Generic vault plumbing (phantom tokens, socket transport, SSH signer) is
tested in terok-executor (test_env_builder.py).  These tests cover the
terok-only Claude OAuth env override, shared config-patch selection, and
leaked-credential scan with exposed-token filtering.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


class TestClaudeOAuthOverrides:
    """Verify _apply_claude_oauth_overrides mode selection."""

    def test_proxied_removes_token_keeps_base_url(self) -> None:
        """Claude OAuth proxied → remove phantom token, keep base URL."""
        from terok.lib.orchestration.environment import _apply_claude_oauth_overrides

        env = {
            "CLAUDE_CODE_OAUTH_TOKEN": "terok-p-abc",
            "ANTHROPIC_BASE_URL": "http://host.containers.internal:18731",
            "ANTHROPIC_UNIX_SOCKET": "/tmp/terok-claude-proxy.sock",
            "TEROK_TOKEN_BROKER_PORT": "18731",
        }
        with patch("terok.lib.core.config.is_claude_oauth_proxied", return_value=True):
            _apply_claude_oauth_overrides(env)

        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        assert "ANTHROPIC_BASE_URL" in env
        # Socket and proxy port are unrelated to Claude tier — untouched
        assert "ANTHROPIC_UNIX_SOCKET" in env
        assert "TEROK_TOKEN_BROKER_PORT" in env

    def test_skipped_removes_all_claude_vars(self) -> None:
        """Claude OAuth skipped (default) → remove all Claude proxy env vars."""
        from terok.lib.orchestration.environment import _apply_claude_oauth_overrides

        env = {
            "CLAUDE_CODE_OAUTH_TOKEN": "terok-p-abc",
            "ANTHROPIC_BASE_URL": "http://host.containers.internal:18731",
            "ANTHROPIC_UNIX_SOCKET": "/tmp/terok-claude-proxy.sock",
            "TEROK_TOKEN_BROKER_PORT": "18731",
            "MISTRAL_API_KEY": "terok-p-vibe",
        }
        with patch("terok.lib.core.config.is_claude_oauth_proxied", return_value=False):
            _apply_claude_oauth_overrides(env)

        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        assert "ANTHROPIC_BASE_URL" not in env
        assert "ANTHROPIC_UNIX_SOCKET" not in env
        # Non-Claude vars untouched
        assert "MISTRAL_API_KEY" in env
        assert "TEROK_TOKEN_BROKER_PORT" in env

    def test_noop_when_no_claude_oauth(self) -> None:
        """No-op when executor didn't inject Claude OAuth token (API key or no Claude)."""
        from terok.lib.orchestration.environment import _apply_claude_oauth_overrides

        env = {
            "ANTHROPIC_API_KEY": "terok-p-abc",
            "ANTHROPIC_BASE_URL": "http://host.containers.internal:18731",
        }
        original = dict(env)
        _apply_claude_oauth_overrides(env)
        assert env == original


class TestVaultPatchProviderSets:
    """Verify Codex shared-config patch selection from terok config."""

    def _roster(self) -> SimpleNamespace:
        # Post-keystone shape: auth providers are keyed by agent/tool name and
        # carry the provider they resolve to (credential_provider); vault routes
        # are keyed by provider name. The patch-provider set is the auth-provider
        # names whose resolved route carries a shared config patch.
        return SimpleNamespace(
            auth_providers={
                "codex": SimpleNamespace(credential_provider="openai"),
                "vibe": SimpleNamespace(credential_provider="mistral"),
                "gh": SimpleNamespace(credential_provider="github"),
                "claude": SimpleNamespace(credential_provider="anthropic"),
            },
            vault_routes={
                "openai": SimpleNamespace(shared_config_patch={"file": "config.toml"}),
                "mistral": SimpleNamespace(shared_config_patch={"file": "config.toml"}),
                "github": SimpleNamespace(shared_config_patch={"file": "config.yml"}),
                "anthropic": SimpleNamespace(shared_config_patch=None),
            },
        )

    def test_proxied_keeps_codex_patch(self) -> None:
        """Codex shared config patch is enabled only in proxied mode."""
        from terok.lib.orchestration.environment import _vault_patch_provider_sets

        with patch("terok.lib.core.config.is_codex_oauth_proxied", return_value=True):
            enabled, disabled = _vault_patch_provider_sets(self._roster())

        assert enabled == frozenset({"codex", "gh", "vibe"})
        assert disabled == frozenset()

    def test_skipped_omits_codex_patch(self) -> None:
        """Default/exposed modes disable Codex's shared config rewrite."""
        from terok.lib.orchestration.environment import _vault_patch_provider_sets

        with patch("terok.lib.core.config.is_codex_oauth_proxied", return_value=False):
            enabled, disabled = _vault_patch_provider_sets(self._roster())

        assert enabled == frozenset({"gh", "vibe"})
        assert disabled == frozenset({"codex"})

    def test_vault_bypass_disables_all_shared_patches(self) -> None:
        """Vault bypass removes stale managed config for every patch provider."""
        from terok.lib.orchestration.environment import _vault_patch_provider_sets

        enabled, disabled = _vault_patch_provider_sets(self._roster(), vault_bypass=True)

        assert enabled == frozenset()
        assert disabled == frozenset({"codex", "gh", "vibe"})


class TestLeakedCredentialsScan:
    """Verify _report_leaked_credentials with exposed-token filtering."""

    def test_reports_leaked_files_as_red_errors(
        self, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
    ) -> None:
        """Leaked credential files print a red ERROR block to stderr."""
        from terok.lib.orchestration.environment import _report_leaked_credentials

        mounts = Path("/tmp/terok-testing/mounts")
        with (
            caplog.at_level(logging.DEBUG, logger="terok.lib.orchestration.environment"),
            patch(
                "terok.lib.integrations.executor.scan_leaked_credentials",
                return_value=[("claude", Path("/tmp/terok-testing/m/.credentials.json"))],
            ) as mock_scan,
            patch("terok.lib.core.config.is_claude_oauth_exposed", return_value=False),
            patch("terok.lib.core.config.is_codex_oauth_exposed", return_value=False),
        ):
            _report_leaked_credentials(mounts)

        err = capsys.readouterr().err
        assert "ERROR" in err and "claude" in err
        assert "terok vault clean" in err
        # Full path must not appear on the console — only at DEBUG
        assert ".credentials.json" not in err
        assert any(".credentials.json" in r.message for r in caplog.records)
        # The mounts dir is forwarded verbatim — no implicit fallback to the
        # global path.  Pins the route from materialize() through to the scan.
        mock_scan.assert_called_once_with(mounts)

    def test_exposed_token_suppresses_claude_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exposed Claude OAuth token: Claude error suppressed, other providers still red."""
        from terok.lib.orchestration.environment import _report_leaked_credentials

        with (
            patch(
                "terok.lib.integrations.executor.scan_leaked_credentials",
                return_value=[
                    ("claude", Path("/tmp/terok-testing/m/.credentials.json")),
                    ("vibe", Path("/tmp/terok-testing/m/config.toml")),
                ],
            ),
            patch("terok.lib.core.config.is_claude_oauth_exposed", return_value=True),
            patch("terok.lib.core.config.is_codex_oauth_exposed", return_value=False),
        ):
            _report_leaked_credentials(Path("/tmp/terok-testing/mounts"))

        # Exposed-token warning printed to stderr
        err = capsys.readouterr().err
        assert "EXPOSED" in err
        # Claude filtered out of the error block, vibe still reported
        error_block = err[err.index("ERROR") :]
        assert "claude" not in error_block
        assert "vibe" in error_block

    def test_exposed_codex_token_suppresses_codex_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exposed Codex OAuth token: Codex error suppressed, banner printed."""
        from terok.lib.orchestration.environment import _report_leaked_credentials

        with (
            patch(
                "terok.lib.integrations.executor.scan_leaked_credentials",
                return_value=[
                    ("codex", Path("/tmp/terok-testing/m/_codex-config/auth.json")),
                    ("vibe", Path("/tmp/terok-testing/m/config.toml")),
                ],
            ),
            patch("terok.lib.core.config.is_claude_oauth_exposed", return_value=False),
            patch("terok.lib.core.config.is_codex_oauth_exposed", return_value=True),
        ):
            _report_leaked_credentials(Path("/tmp/terok-testing/mounts"))

        err = capsys.readouterr().err
        assert "Codex" in err and "EXPOSED" in err
        error_block = err[err.index("ERROR") :]
        assert "codex" not in error_block
        assert "vibe" in error_block

    def test_no_output_when_nothing_leaked(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A clean scan prints nothing — no empty ERROR block."""
        from terok.lib.orchestration.environment import _report_leaked_credentials

        with (
            patch(
                "terok.lib.integrations.executor.scan_leaked_credentials",
                return_value=[],
            ),
            patch("terok.lib.core.config.is_claude_oauth_exposed", return_value=False),
            patch("terok.lib.core.config.is_codex_oauth_exposed", return_value=False),
        ):
            _report_leaked_credentials(Path("/tmp/terok-testing/mounts"))

        assert capsys.readouterr().err == ""
