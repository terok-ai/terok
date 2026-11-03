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

from terok.lib.integrations.executor import CLAUDE_OAUTH_UNROUTED_ENV, CLAUDE_OAUTH_UNROUTED_ON
from tests.testnet import VAULT_LOOPBACK_URL


def _roster() -> SimpleNamespace:
    """A roster double: claude → anthropic, codex → openai, openrouter its own provider."""
    return SimpleNamespace(
        auth_providers={
            "claude": SimpleNamespace(credential_provider="anthropic"),
            "codex": SimpleNamespace(credential_provider="openai"),
            "openrouter": SimpleNamespace(credential_provider=None),
        },
        agents={
            "claude": SimpleNamespace(provider_binding=SimpleNamespace(default="anthropic")),
            "codex": SimpleNamespace(provider_binding=SimpleNamespace(default="openai")),
            "opencode": SimpleNamespace(provider_binding=None),
        },
    )


_ANTHROPIC_HANDLE = {
    "TEROK_PROVIDER_ANTHROPIC_TOKEN": "terok-p-abc",
    "TEROK_PROVIDER_ANTHROPIC_LABEL": "Anthropic",
    "TEROK_PROVIDER_ANTHROPIC_MODELS": "{}",
    "TEROK_PROVIDER_ANTHROPIC_BASE_ANTHROPIC_MESSAGES": VAULT_LOOPBACK_URL,
}
"""The handle the executor materializes for a vault-routed anthropic login."""


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
            **_ANTHROPIC_HANDLE,
        }
        with patch("terok.lib.core.config.is_claude_oauth_proxied", return_value=True):
            assert _apply_claude_oauth_overrides(env) is False

        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        assert "ANTHROPIC_BASE_URL" in env
        # Socket and proxy port are unrelated to Claude tier — untouched
        assert "ANTHROPIC_UNIX_SOCKET" in env
        assert "TEROK_TOKEN_BROKER_PORT" in env
        # The login is routed, so the handle that says so stays — and claude
        # is told nothing is wrong
        assert _ANTHROPIC_HANDLE.items() <= env.items()
        assert CLAUDE_OAUTH_UNROUTED_ENV not in env

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
        with (
            patch("terok.lib.core.config.is_claude_oauth_proxied", return_value=False),
            patch("terok.lib.core.config.is_claude_oauth_exposed", return_value=False),
        ):
            assert _apply_claude_oauth_overrides(env) is True

        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        assert "ANTHROPIC_BASE_URL" not in env
        assert "ANTHROPIC_UNIX_SOCKET" not in env
        # Non-Claude vars untouched
        assert "MISTRAL_API_KEY" in env
        assert "TEROK_TOKEN_BROKER_PORT" in env
        # claude's wrapper learns that its login is not routed here
        assert env[CLAUDE_OAUTH_UNROUTED_ENV] == CLAUDE_OAUTH_UNROUTED_ON

    def test_skipped_keeps_the_tasks_provider_handle(self) -> None:
        """The handle is the whole task's, not claude's: skipping claude's routing leaves it.

        Every agent that reaches the provider through the vault reads the
        handle, so stripping it for claude's sake would take the provider from
        all of them.  claude learns of its own trouble from the claude-only
        signal instead, which no other agent reads.
        """
        from terok.lib.orchestration.environment import _apply_claude_oauth_overrides

        env = {"CLAUDE_CODE_OAUTH_TOKEN": "terok-p-abc", **_ANTHROPIC_HANDLE}
        with (
            patch("terok.lib.core.config.is_claude_oauth_proxied", return_value=False),
            patch("terok.lib.core.config.is_claude_oauth_exposed", return_value=False),
        ):
            assert _apply_claude_oauth_overrides(env) is True

        assert env == {**_ANTHROPIC_HANDLE, CLAUDE_OAUTH_UNROUTED_ENV: CLAUDE_OAUTH_UNROUTED_ON}

    def test_exposed_keeps_the_handle(self) -> None:
        """Exposed mode points the handle at the real upstream on purpose — it stays."""
        from terok.lib.orchestration.environment import _apply_claude_oauth_overrides

        env = {"CLAUDE_CODE_OAUTH_TOKEN": "terok-p-abc", **_ANTHROPIC_HANDLE}
        with (
            patch("terok.lib.core.config.is_claude_oauth_proxied", return_value=False),
            patch("terok.lib.core.config.is_claude_oauth_exposed", return_value=True),
        ):
            assert _apply_claude_oauth_overrides(env) is False

        assert env == _ANTHROPIC_HANDLE

    def test_noop_when_no_claude_oauth(self) -> None:
        """No-op when executor didn't inject Claude OAuth token (API key or no Claude)."""
        from terok.lib.orchestration.environment import _apply_claude_oauth_overrides

        env = {
            "ANTHROPIC_API_KEY": "terok-p-abc",
            "ANTHROPIC_BASE_URL": "http://host.containers.internal:18731",
            **_ANTHROPIC_HANDLE,
        }
        original = dict(env)
        assert _apply_claude_oauth_overrides(env) is False
        assert env == original


class TestUnreadyDefaultAgentHint:
    """terok#1273: the launch says, on the host, when the default agent cannot authenticate."""

    def _project(self, **overrides: object) -> SimpleNamespace:
        """A shared-scope project defaulting to claude, with no provider override."""
        fields: dict[str, object] = {
            "name": "proj",
            "default_agent": None,
            "credentials_scope": "shared",
        }
        fields.update(overrides)
        return SimpleNamespace(**fields)

    def _warn(
        self,
        env: dict[str, str],
        capsys: pytest.CaptureFixture[str],
        *,
        selected: str | None = None,
        oauth_skipped: bool = False,
        **project: object,
    ) -> str:
        """Run the hint and return what it printed to stderr."""
        from terok.lib.orchestration.environment import _warn_unready_default_agent

        _warn_unready_default_agent(
            env, _roster(), self._project(**project), selected, oauth_skipped=oauth_skipped
        )
        return capsys.readouterr().err

    def test_unauthenticated_default_agent_gets_the_auth_command(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No anthropic handle → claude will refuse; the hint names ``terok auth claude``."""
        err = self._warn({}, capsys)

        assert "claude has no credential for its provider 'anthropic'" in err
        assert "terok auth claude" in err
        assert "egress firewall" in err

    def test_a_ready_provider_prints_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The handle token is what the wrapper checks, and its presence is enough."""
        assert self._warn(dict(_ANTHROPIC_HANDLE), capsys) == ""

    def test_a_selected_provider_wins_over_the_agents_default(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With ``default_provider`` set, that provider's credential is the one needed."""
        err = self._warn(dict(_ANTHROPIC_HANDLE), capsys, selected="openrouter")

        assert "provider 'openrouter'" in err
        assert "terok auth openrouter" in err

    def test_project_scope_names_the_project_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A project-scoped bucket is filled with ``--project``."""
        err = self._warn({}, capsys, credentials_scope="project")

        assert "terok auth claude --project proj" in err

    def test_a_provider_no_auth_entry_stores_is_named_as_itself(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An agent with no auth entry, a provider no entry stores: the hint names the provider."""
        err = self._warn({}, capsys, selected="mistral", default_agent="opencode")

        assert "terok auth mistral" in err

    def test_a_harness_without_a_default_provider_is_not_checked(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An agent that picks its provider at run time has nothing to check up front."""
        assert self._warn({}, capsys, default_agent="opencode") == ""

    def test_a_skipped_oauth_login_is_named_although_the_handle_is_set(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The handle stays for the task's other agents, so it cannot speak for claude.

        claude's wrapper passes its check on that handle and a bare ``claude``
        fails to connect; the launch says why, and how to fix it on the host.
        """
        err = self._warn(dict(_ANTHROPIC_HANDLE), capsys, oauth_skipped=True)

        assert "claude cannot use the Claude OAuth login" in err
        assert "allow_oauth" in err
        assert "terok auth claude" in err
        assert "refuse to start" not in err  # the wrapper does not refuse: claude fails to connect

    @pytest.mark.parametrize(
        ("selected", "agent"),
        [
            pytest.param("anthropic", None, id="provider-selected"),
            pytest.param(None, "codex", id="another-default-agent"),
        ],
    )
    def test_a_skipped_oauth_login_concerns_claude_on_its_default_endpoint_only(
        self, capsys: pytest.CaptureFixture[str], selected: str | None, agent: str | None
    ) -> None:
        """A selected provider routes claude through the vault, and other agents are not claude."""
        env = {**_ANTHROPIC_HANDLE, "TEROK_PROVIDER_OPENAI_TOKEN": "terok-p-oai"}

        err = self._warn(env, capsys, selected=selected, oauth_skipped=True, default_agent=agent)

        assert err == ""

    def test_a_skipped_oauth_login_without_a_handle_still_names_the_login(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No handle at all and a skipped login: the login is the likelier story, and is told."""
        err = self._warn({}, capsys, oauth_skipped=True)

        assert "Claude OAuth login" in err
        assert "terok auth claude" in err


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


def test_materialize_uses_one_fresh_roster_snapshot(tmp_path: Path) -> None:
    """Fresh roster snapshot: one load supplies patch selection and assembly."""
    from terok.lib.orchestration.environment import TaskEnvironment

    project = SimpleNamespace(
        name="example",
        is_sealed=False,
        tasks_root=tmp_path / "tasks",
        services_mode="socket",
        default_agent="opencode",
        default_provider="example",
        human_name="Example User",
        human_email="user@example.com",
        git_authorship="human",
        credential_set="default",
        credentials_scope="project",
        project_mounts_dir=tmp_path / "mounts",
        shared_dir=tmp_path / "shared",
        timezone=None,
    )
    roster = object()
    egress = object()
    assembled = SimpleNamespace(env={}, volumes=(), egress=egress, broker_context=object())

    with (
        patch("terok.lib.orchestration.environment.make_sandbox_config"),
        patch(
            "terok.lib.orchestration.environment._security_mode_env_and_volumes",
            return_value=({}, []),
        ),
        patch("terok.lib.core.config.get_vault_bypass", return_value=True),
        patch("terok.lib.integrations.executor.AgentRoster.load", return_value=roster) as load,
        patch(
            "terok.lib.orchestration.environment._vault_patch_provider_sets",
            return_value=(frozenset(), frozenset()),
        ) as patch_sets,
        patch(
            "terok.lib.integrations.executor.assemble_container_env", return_value=assembled
        ) as assemble,
        patch(
            "terok.lib.orchestration.environment.exposed_credential_providers",
            return_value=frozenset(),
        ),
    ):
        _env, _volumes, actual_egress, actual_context = TaskEnvironment(project, "1").materialize()

    load.assert_called_once_with()
    assert patch_sets.call_args.args[0] is roster
    assert assemble.call_args.args[1] is roster
    assert actual_egress is egress
    assert actual_context is assembled.broker_context


def test_materialize_with_the_vault_on_warns_about_an_unrouted_claude_login(tmp_path: Path) -> None:
    """With the vault in use, the OAuth overrides decide whether the launch names the login."""
    from terok.lib.orchestration.environment import TaskEnvironment

    project = SimpleNamespace(
        name="example",
        is_sealed=False,
        tasks_root=tmp_path / "tasks",
        services_mode="socket",
        default_agent=None,
        default_provider=None,
        human_name="Example User",
        human_email="user@example.com",
        git_authorship="human",
        credential_set="default",
        credentials_scope="shared",
        project_mounts_dir=tmp_path / "mounts",
        shared_dir=tmp_path / "shared",
        timezone=None,
    )
    roster = object()
    assembled = SimpleNamespace(env={}, volumes=(), egress=object(), broker_context=object())
    env_module = "terok.lib.orchestration.environment"

    with (
        patch(f"{env_module}.make_sandbox_config"),
        patch(f"{env_module}._security_mode_env_and_volumes", return_value=({}, [])),
        patch("terok.lib.core.config.get_vault_bypass", return_value=False),
        patch(f"{env_module}._check_project_credentials_present"),
        patch("terok.lib.integrations.executor.AgentRoster.load", return_value=roster),
        patch(f"{env_module}._vault_patch_provider_sets", return_value=(frozenset(), frozenset())),
        patch("terok.lib.integrations.executor.assemble_container_env", return_value=assembled),
        patch(f"{env_module}.exposed_credential_providers", return_value=frozenset()),
        patch(f"{env_module}._apply_claude_oauth_overrides", return_value=True),
        patch(f"{env_module}._report_leaked_credentials") as leaked,
        patch(f"{env_module}._warn_unready_default_agent") as warn,
    ):
        TaskEnvironment(project, "1").materialize()

    leaked.assert_called_once()
    (env, warned_roster, warned_project, selected), kwargs = warn.call_args
    assert (warned_roster, warned_project, selected) == (roster, project, None)
    assert kwargs == {"oauth_skipped": True}


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
