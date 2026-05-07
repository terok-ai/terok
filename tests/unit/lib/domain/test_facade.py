# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the domain.facade thin-wrapper factories."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestGetProject:
    """get_project loads config and wraps it in a Project aggregate."""

    def test_returns_project_wrapping_loaded_config(self) -> None:
        from terok.lib.domain import facade
        from terok.lib.domain.project import Project

        fake_cfg = MagicMock()
        fake_cfg.id = "myproj"
        with patch("terok.lib.domain.facade.load_project", return_value=fake_cfg) as loader:
            result = facade.get_project("myproj")
        loader.assert_called_once_with("myproj")
        assert isinstance(result, Project)


class TestListProjects:
    """list_projects lifts every core config into a Project aggregate."""

    def test_wraps_each_core_config(self) -> None:
        from terok.lib.domain import facade
        from terok.lib.domain.project import Project

        a, b = MagicMock(id="a"), MagicMock(id="b")
        with patch("terok.lib.core.projects.list_projects", return_value=[a, b]) as lister:
            result = facade.list_projects()
        lister.assert_called_once()
        assert len(result) == 2
        assert all(isinstance(p, Project) for p in result)

    def test_empty_list_returns_empty(self) -> None:
        from terok.lib.domain import facade

        with patch("terok.lib.core.projects.list_projects", return_value=[]):
            assert facade.list_projects() == []


class TestDeriveProject:
    """derive_project composes the three domain steps and returns a Project."""

    def test_delegates_and_wraps_result(self) -> None:
        from terok.lib.domain import facade
        from terok.lib.domain.project import Project

        derived_cfg = MagicMock(id="derived")
        with (
            patch("terok.lib.domain.facade._derive_project") as derive,
            patch("terok.lib.domain.facade._share_ssh_key_assignments") as share,
            patch("terok.lib.domain.facade.load_project", return_value=derived_cfg) as loader,
        ):
            result = facade.derive_project("source", "derived")
        derive.assert_called_once_with("source", "derived")
        share.assert_called_once_with("source", "derived")
        loader.assert_called_once_with("derived")
        assert isinstance(result, Project)


def _patch_vault_db(db):
    """Patch ``facade.vault_db`` to yield *db* — returns the ``patch`` context."""
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        yield db

    return patch("terok.lib.domain.facade.vault_db", _cm)


class TestShareSshKeyAssignments:
    """Copy every SSH key assignment from the source scope to the new scope."""

    def test_delegates_to_db_assign_for_each_row(self) -> None:
        from terok.lib.domain import facade

        row_a = MagicMock(id=1)
        row_b = MagicMock(id=2)
        db = MagicMock()
        db.list_ssh_keys_for_scope.return_value = [row_a, row_b]
        with _patch_vault_db(db):
            facade._share_ssh_key_assignments("src", "new")
        db.list_ssh_keys_for_scope.assert_called_once_with("src")
        assert db.assign_ssh_key.call_args_list == [
            (("new", 1),),
            (("new", 2),),
        ]

    def test_silent_noop_when_source_has_no_keys(self) -> None:
        from terok.lib.domain import facade

        db = MagicMock()
        db.list_ssh_keys_for_scope.return_value = []
        with _patch_vault_db(db):
            facade._share_ssh_key_assignments("src", "new")
        db.assign_ssh_key.assert_not_called()


class TestRegisterSshKey:
    """register_ssh_key assigns a key_id to a scope via the vault DB."""

    def test_assigns_key_to_scope(self) -> None:
        from terok.lib.domain import facade

        db = MagicMock()
        with _patch_vault_db(db):
            facade.register_ssh_key("myproj", 42)
        db.assign_ssh_key.assert_called_once_with("myproj", 42)


class TestProvisionSshKey:
    """provision_ssh_key mints via SSHManager and binds the fresh key_id."""

    def test_mints_and_binds(self) -> None:
        from terok.lib.domain import facade

        init_result = {
            "key_id": 7,
            "key_type": "ed25519",
            "fingerprint": "deadbeef",
            "comment": "tk-main:myproj",
            "public_line": "ssh-ed25519 AAAA… tk-main:myproj",
        }
        ssh_manager = MagicMock()
        ssh_manager.__enter__ = MagicMock(return_value=ssh_manager)
        ssh_manager.__exit__ = MagicMock(return_value=False)
        ssh_manager.init.return_value = init_result

        db = MagicMock()
        with (
            patch("terok.lib.domain.facade.load_project", return_value=MagicMock(id="myproj")),
            patch("terok.lib.domain.project.make_ssh_manager", return_value=ssh_manager),
            _patch_vault_db(db),
        ):
            result = facade.provision_ssh_key("myproj", key_type="ed25519", force=True)

        ssh_manager.init.assert_called_once_with(key_type="ed25519", comment=None, force=True)
        db.assign_ssh_key.assert_called_once_with("myproj", 7)
        assert result is init_result


class TestSummarizeSshInit:
    """summarize_ssh_init prints every field from the SSHInitResult."""

    def test_prints_all_metadata_and_public_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        from terok.lib.domain import facade

        facade.summarize_ssh_init(
            {
                "key_id": 3,
                "key_type": "rsa",
                "fingerprint": "SHA256:abc123",
                "comment": "tk-main:proj",
                "public_line": "ssh-rsa AAAA… tk-main:proj",
            }
        )
        out = capsys.readouterr().out
        assert "id:          3" in out
        assert "type:        rsa" in out
        assert "fingerprint: SHA256:abc123" in out
        assert "comment:     tk-main:proj" in out
        assert "ssh-rsa AAAA… tk-main:proj" in out


class TestMaybePauseForSshKeyRegistration:
    """maybe_pause_for_ssh_key_registration only pauses for SSH upstreams."""

    def test_pauses_for_git_at_upstream(self, capsys: pytest.CaptureFixture[str]) -> None:
        from terok.lib.domain import facade

        project = MagicMock(upstream_url="git@example.com:org/repo.git")
        with (
            patch("terok.lib.domain.facade.load_project", return_value=project),
            patch("builtins.input", return_value=""),
        ):
            facade.maybe_pause_for_ssh_key_registration("myproj")
        assert "ACTION REQUIRED" in capsys.readouterr().out

    def test_pauses_for_ssh_scheme_upstream(self, capsys: pytest.CaptureFixture[str]) -> None:
        from terok.lib.domain import facade

        project = MagicMock(upstream_url="ssh://git@example.com/org/repo.git")
        with (
            patch("terok.lib.domain.facade.load_project", return_value=project),
            patch("builtins.input", return_value=""),
        ):
            facade.maybe_pause_for_ssh_key_registration("myproj")
        assert "ACTION REQUIRED" in capsys.readouterr().out

    def test_noop_for_https_upstream(self, capsys: pytest.CaptureFixture[str]) -> None:
        from terok.lib.domain import facade

        project = MagicMock(upstream_url="https://github.com/org/repo.git")
        with patch("terok.lib.domain.facade.load_project", return_value=project):
            facade.maybe_pause_for_ssh_key_registration("myproj")
        assert "ACTION REQUIRED" not in capsys.readouterr().out

    def test_noop_for_empty_upstream(self, capsys: pytest.CaptureFixture[str]) -> None:
        from terok.lib.domain import facade

        project = MagicMock(upstream_url=None)
        with patch("terok.lib.domain.facade.load_project", return_value=project):
            facade.maybe_pause_for_ssh_key_registration("myproj")
        assert "ACTION REQUIRED" not in capsys.readouterr().out


class TestAuthenticate:
    """authenticate dispatches to the raw executor call with the right image+scope."""

    def test_project_scoped_uses_l2_image(self) -> None:
        """``authenticate(provider, project_id)`` reuses the project's L2 image."""
        from terok.lib.domain import facade

        # sandbox_live_mounts_dir and the expose-token predicates are
        # lazy-imported inside the function body, so patching happens
        # at their definition modules rather than on the facade.
        with (
            patch(
                "terok.lib.domain.facade.project_cli_image", return_value="terok-p1:latest"
            ) as mock_l2,
            patch("terok.lib.core.config.sandbox_live_mounts_dir", return_value="/mnt"),
            patch("terok.lib.core.config.is_claude_oauth_exposed", return_value=False),
            patch("terok.lib.core.config.is_codex_oauth_exposed", return_value=False),
            patch("terok.lib.domain.facade._authenticate_raw") as mock_auth,
        ):
            facade.authenticate("claude", project_id="p1")

        mock_l2.assert_called_once_with("p1")
        mock_auth.assert_called_once()
        # Positional call arg 0 is the container-scope string: the project id.
        assert mock_auth.call_args.args[0] == "p1"
        assert mock_auth.call_args.kwargs["image"] == "terok-p1:latest"
        assert mock_auth.call_args.kwargs["expose_token"] is False

    def test_host_wide_passes_lazy_resolver_as_image(self) -> None:
        """``authenticate(provider)`` passes a callable so the L1 build defers
        past the OAuth-vs-API-key prompt — picking API key never triggers it."""
        from terok.lib.domain import facade

        with (
            patch(
                "terok.lib.domain.facade._resolve_host_auth_image",
                return_value="terok-l1-cli:ubuntu-24.04",
            ) as mock_resolve,
            patch("terok.lib.core.config.sandbox_live_mounts_dir", return_value="/mnt"),
            patch("terok.lib.core.config.is_claude_oauth_exposed", return_value=False),
            patch("terok.lib.core.config.is_codex_oauth_exposed", return_value=False),
            patch("terok.lib.domain.facade._authenticate_raw") as mock_auth,
        ):
            facade.authenticate("claude")

            # Resolver isn't invoked here — `_authenticate_raw` is patched, so
            # the executor never reaches the OAuth branch that would call the
            # lambda.
            mock_resolve.assert_not_called()
            assert mock_auth.call_args.args[0] is None
            passed_image = mock_auth.call_args.kwargs["image"]
            assert callable(passed_image), "host-wide auth must pass a lazy callable"
            # Calling it threads the provider into _resolve_host_auth_image —
            # done inside the patch context so the mock is still in effect.
            assert passed_image() == "terok-l1-cli:ubuntu-24.04"
            mock_resolve.assert_called_once_with("claude")

    def test_codex_expose_flag_forwards_expose_token(self) -> None:
        """``is_codex_oauth_exposed()`` True → ``expose_token=True`` for codex."""
        from terok.lib.domain import facade

        with (
            patch(
                "terok.lib.domain.facade._resolve_host_auth_image",
                return_value="terok-l1-cli:ubuntu-24.04",
            ),
            patch("terok.lib.core.config.sandbox_live_mounts_dir", return_value="/mnt"),
            patch("terok.lib.core.config.is_claude_oauth_exposed", return_value=False),
            patch("terok.lib.core.config.is_codex_oauth_exposed", return_value=True),
            patch("terok.lib.domain.facade._authenticate_raw") as mock_auth,
        ):
            facade.authenticate("codex")

        assert mock_auth.call_args.kwargs["expose_token"] is True

    def test_codex_expose_flag_does_not_leak_to_claude(self) -> None:
        """Codex expose flag must not flip expose_token for a Claude auth flow."""
        from terok.lib.domain import facade

        with (
            patch(
                "terok.lib.domain.facade._resolve_host_auth_image",
                return_value="terok-l1-cli:ubuntu-24.04",
            ),
            patch("terok.lib.core.config.sandbox_live_mounts_dir", return_value="/mnt"),
            patch("terok.lib.core.config.is_claude_oauth_exposed", return_value=False),
            patch("terok.lib.core.config.is_codex_oauth_exposed", return_value=True),
            patch("terok.lib.domain.facade._authenticate_raw") as mock_auth,
        ):
            facade.authenticate("claude")

        assert mock_auth.call_args.kwargs["expose_token"] is False


class TestResolveHostAuthImage:
    """_resolve_host_auth_image prefers an existing L1 and builds on demand."""

    def test_prefers_default_alias_when_provider_label_present(self) -> None:
        """Default alias wins iff the OCI label confirms it has the provider."""
        from terok.lib.domain import facade

        def exists(tag: str) -> bool:
            return tag == "terok-l1-cli:ubuntu-24.04"

        with (
            patch("terok.lib.domain.facade.image_exists", side_effect=exists),
            patch("terok_executor.image_agents", return_value={"claude", "codex", "gh"}),
        ):
            image = facade._resolve_host_auth_image("claude")
        assert image == "terok-l1-cli:ubuntu-24.04"

    def test_alias_present_but_label_missing_provider_falls_through(self) -> None:
        """Stale alias from a partial build must not silently route auth at it."""
        from terok.lib.domain import facade

        # Stale alias points at a claude-only image; per-agent for codex
        # was previously built too.  Asking for codex must skip the alias
        # (label says "claude" only) and pick up the per-agent codex tag.
        def exists(tag: str) -> bool:
            return tag in {
                "terok-l1-cli:ubuntu-24.04",
                "terok-l1-cli:ubuntu-24.04-codex",
            }

        with (
            patch("terok.lib.domain.facade.image_exists", side_effect=exists),
            patch("terok_executor.image_agents", return_value={"claude"}),
        ):
            image = facade._resolve_host_auth_image("codex")
        assert image == "terok-l1-cli:ubuntu-24.04-codex"

    def test_falls_back_to_per_agent_l1(self) -> None:
        from terok.lib.domain import facade

        def exists(tag: str) -> bool:
            return tag == "terok-l1-cli:ubuntu-24.04-claude"

        with (
            patch("terok.lib.domain.facade.image_exists", side_effect=exists),
            patch("terok_executor.image_agents", return_value=set()),
        ):
            image = facade._resolve_host_auth_image("claude")
        assert image == "terok-l1-cli:ubuntu-24.04-claude"

    def test_api_key_only_provider_skips_build_when_missing(self) -> None:
        """API-key-only providers never launch a container; any tag is fine."""
        from terok.lib.domain import facade

        with (
            patch("terok.lib.domain.facade.image_exists", return_value=False),
            patch("terok_executor.image_agents", return_value=set()),
        ):
            # sonar is api-key-only — no prompt, no build, just a tag.
            image = facade._resolve_host_auth_image("sonar")
        assert image.startswith("terok-l1-cli:")

    def test_oauth_provider_exits_on_non_tty_without_image(self) -> None:
        """Non-TTY OAuth run with no image exits with a build hint, no prompt."""
        from terok.lib.domain import facade

        with (
            patch("terok.lib.domain.facade.image_exists", return_value=False),
            patch("terok_executor.image_agents", return_value=set()),
            patch("sys.stdin.isatty", return_value=False),
            patch("sys.stdout.isatty", return_value=False),
            pytest.raises(SystemExit) as exc,
        ):
            facade._resolve_host_auth_image("claude")
        assert "terok project build" in str(exc.value) or "terok executor" in str(exc.value)

    def test_y_choice_builds_full_default(self) -> None:
        """[Y] (default) routes to ensure_default_l1 with the user's agent set."""
        from terok.lib.domain import facade

        with (
            patch("terok.lib.domain.facade.image_exists", return_value=False),
            patch("terok_executor.image_agents", return_value=set()),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("terok.lib.core.config.get_global_image_agents", return_value="all"),
            patch(
                "terok.lib.core.config.get_global_image_base_image",
                return_value="ubuntu:24.04",
            ),
            patch("builtins.input", return_value=""),  # empty → default Y
            patch(
                "terok_executor.ensure_default_l1",
                return_value="terok-l1-cli:ubuntu-24.04",
            ) as mock_ensure,
            patch("terok_executor.build_base_images") as mock_per_agent,
        ):
            tag = facade._resolve_host_auth_image("claude")
        assert tag == "terok-l1-cli:ubuntu-24.04"
        mock_ensure.assert_called_once()
        mock_per_agent.assert_not_called()

    def test_one_choice_builds_minimal_per_agent(self) -> None:
        """[1] builds only the per-agent L1, not the full default."""
        from terok.lib.domain import facade

        with (
            patch("terok.lib.domain.facade.image_exists", return_value=False),
            patch("terok_executor.image_agents", return_value=set()),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("terok.lib.core.config.get_global_image_agents", return_value="all"),
            patch(
                "terok.lib.core.config.get_global_image_base_image",
                return_value="ubuntu:24.04",
            ),
            patch("builtins.input", return_value="1"),
            patch("terok_executor.ensure_default_l1") as mock_ensure,
            patch("terok_executor.build_base_images") as mock_per_agent,
        ):
            tag = facade._resolve_host_auth_image("codex")
        assert tag == "terok-l1-cli:ubuntu-24.04-codex"
        mock_per_agent.assert_called_once()
        # Per-agent build with just the requested provider, no alias retag
        kwargs = mock_per_agent.call_args.kwargs
        assert kwargs.get("agents") == ("codex",) or mock_per_agent.call_args.args[1:] == (
            ("codex",),
        )
        mock_ensure.assert_not_called()

    def test_n_choice_aborts(self) -> None:
        """[n] exits with the build-hint message."""
        from terok.lib.domain import facade

        with (
            patch("terok.lib.domain.facade.image_exists", return_value=False),
            patch("terok_executor.image_agents", return_value=set()),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("terok.lib.core.config.get_global_image_agents", return_value="all"),
            patch(
                "terok.lib.core.config.get_global_image_base_image",
                return_value="ubuntu:24.04",
            ),
            patch("builtins.input", return_value="n"),
            pytest.raises(SystemExit) as exc,
        ):
            facade._resolve_host_auth_image("codex")
        assert "terok project build" in str(exc.value) or "terok executor" in str(exc.value)

    def test_uses_configured_base_image(self) -> None:
        """Honors ``image.base_image`` from config — not hardcoded ubuntu."""
        from terok.lib.domain import facade

        def exists(tag: str) -> bool:
            return tag == "terok-l1-cli:fedora-43"

        with (
            patch("terok.lib.domain.facade.image_exists", side_effect=exists),
            patch("terok_executor.image_agents", return_value={"claude", "codex", "gh"}),
            patch(
                "terok.lib.core.config.get_global_image_base_image",
                return_value="fedora:43",
            ),
            patch("terok.lib.core.config.get_global_image_agents", return_value="all"),
        ):
            image = facade._resolve_host_auth_image("claude")
        assert image == "terok-l1-cli:fedora-43"
