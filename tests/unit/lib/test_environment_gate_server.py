# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for environment.py per-container gate integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from terok.lib.core.projects import ProjectConfig, load_project
from terok.lib.orchestration.environment import _security_mode_env_and_volumes
from tests.test_utils import mock_git_config, project_env
from tests.testnet import gate_repo_url

_GATEKEEPING_YAML = """\
project:
  id: gk-proj
  security_class: gatekeeping
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""

_ONLINE_YAML = """\
project:
  id: online-proj
  security_class: online
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""


def gate_mounts(volumes: list) -> list:
    """Return any gate-related volume mounts from the generated volume list."""
    return [
        vol
        for vol in volumes
        if "gate" in str(getattr(vol, "container_path", "")) or "gate" in str(vol)
    ]


def resolve_security_env(
    yaml_text: str,
    *,
    project_name: str,
    with_gate: bool,
    token: str | None = None,
    use_socket: bool = False,
) -> tuple[ProjectConfig, dict[str, str], list]:
    """Load a project and evaluate gate-related env/volume settings.

    Patches ``mint_gate_token`` so the embedded token is deterministic;
    the gate base path is read straight off ``cfg.gate_base_path`` (a
    ``SandboxConfig`` property), so no manager patching is needed.
    """
    with (
        mock_git_config(),
        project_env(yaml_text, project_name=project_name, with_gate=with_gate) as ctx,
        patch(
            "terok.lib.orchestration.environment.mint_gate_token",
            return_value=token or "tok" * 10 + "ab",
        ),
        patch(
            "terok.lib.orchestration.environment.SandboxConfig.gate_base_path",
            new_callable=lambda: property(lambda self: ctx.base / "sandbox-state" / "gate"),
        ),
    ):
        from terok.lib.integrations.sandbox import SandboxConfig

        project = load_project(project_name)
        env, volumes = _security_mode_env_and_volumes(
            project, SandboxConfig(), use_socket=use_socket
        )
    return project, env, volumes


@pytest.mark.parametrize(
    ("yaml_text", "project_name", "token", "env_key"),
    [
        pytest.param(_GATEKEEPING_YAML, "gk-proj", "deadbeef" * 4, "CODE_REPO", id="gatekeeping"),
        pytest.param(_ONLINE_YAML, "online-proj", "cafebabe" * 4, "CLONE_FROM", id="online"),
    ],
)
def test_gate_projects_use_http_urls_with_tokens(
    yaml_text: str,
    project_name: str,
    token: str,
    env_key: str,
) -> None:
    """Gate-backed project modes generate token-authenticated localhost HTTP URLs."""
    project, env, volumes = resolve_security_env(
        yaml_text,
        project_name=project_name,
        with_gate=True,
        token=token,
    )

    assert env[env_key] == gate_repo_url(project_name, token)
    # The minted token is also surfaced for the supervisor to validate.
    assert env["TEROK_GATE_TOKEN"] == token
    # The gate runs in the per-container supervisor — no host bind-mount.
    assert gate_mounts(volumes) == []

    if project.security_class == "gatekeeping":
        assert env["GIT_BRANCH"] == "main"
    else:
        assert env["CODE_REPO"] == "https://example.com/repo.git"


def test_gatekeeping_missing_gate_raises() -> None:
    """Gatekeeping mode requires a synced gate mirror before task startup."""
    with mock_git_config(), project_env(_GATEKEEPING_YAML, project_name="gk-proj", with_gate=False):
        project = load_project("gk-proj")
        with pytest.raises(SystemExit, match="gate-sync"):
            _security_mode_env_and_volumes(project, MagicMock())


def test_online_gate_uses_clone_from_and_gate_remote() -> None:
    """Online mode with a gate mirror uses CLONE_FROM + a named gate remote."""
    _project, env, volumes = resolve_security_env(
        _ONLINE_YAML,
        project_name="online-proj",
        with_gate=True,
        token="cafebabe" * 4,
    )

    expected_url = gate_repo_url("online-proj", "cafebabe" * 4)
    assert env["CLONE_FROM"] == expected_url
    # Gate is also surfaced as a named "gate" remote in online mode
    # so the agent can push WIP host-locally without going upstream.
    assert env["GATE_REMOTE_URL"] == expected_url
    assert env["TEROK_GATE_TOKEN"] == "cafebabe" * 4
    assert env["CODE_REPO"] == "https://example.com/repo.git"
    assert gate_mounts(volumes) == []


def test_online_without_gate_has_no_clone_from() -> None:
    """Online mode without a gate mirror clones directly from upstream only."""
    _project, env, volumes = resolve_security_env(
        _ONLINE_YAML,
        project_name="online-proj",
        with_gate=False,
    )
    assert "CLONE_FROM" not in env
    assert "GATE_REMOTE_URL" not in env
    assert "TEROK_GATE_TOKEN" not in env
    assert env["CODE_REPO"] == "https://example.com/repo.git"
    assert gate_mounts(volumes) == []


def test_gatekeeping_does_not_set_gate_remote_url() -> None:
    """Gatekeeping mode keeps the gate as origin — no separate "gate" remote."""
    _project, env, _volumes = resolve_security_env(
        _GATEKEEPING_YAML,
        project_name="gk-proj",
        with_gate=True,
        token="deadbeef" * 4,
    )
    assert "GATE_REMOTE_URL" not in env


def test_socket_mode_sets_gate_socket_env_without_mount() -> None:
    """Socket mode sets ``TEROK_GATE_SOCKET`` but no host bind-mount.

    The gate now runs inside the per-container supervisor, which binds
    ``gate-server.sock`` inside the per-container ``/run/terok`` dir
    (mounted by the executor's launch flow).  terok only tells the
    container-side socat bridge the well-known in-container path — it
    does not emit a host gate-socket VolumeSpec anymore.
    """
    from terok.lib.orchestration.environment import _CONTAINER_RUNTIME_DIR

    _project, env, volumes = resolve_security_env(
        _GATEKEEPING_YAML,
        project_name="gk-proj",
        with_gate=True,
        token="d" * 32,
        use_socket=True,
    )

    assert env["TEROK_GATE_SOCKET"] == f"{_CONTAINER_RUNTIME_DIR}/gate-server.sock"
    assert gate_mounts(volumes) == []


def test_resolve_gate_port_always_fixed_bridge_port() -> None:
    """The container reaches the gate on the fixed in-container bridge port.

    The gate runs in the per-container supervisor and is reached through the
    fixed ``TCP-LISTEN:9418`` socat bridge in both socket and TCP modes, so
    the resolver takes no mode argument.
    """
    from terok.lib.orchestration.environment import _CONTAINER_GATE_PORT, _resolve_gate_port

    assert _resolve_gate_port() == _CONTAINER_GATE_PORT == 9418


def test_project_mounts_dir_shared_returns_global() -> None:
    """``credentials.scope: shared`` (default) returns the host-wide mount tree."""
    from terok.lib.core.config import sandbox_live_mounts_dir
    from terok.lib.orchestration.environment import project_mounts_dir

    with mock_git_config(), project_env(_ONLINE_YAML, project_name="online-proj"):
        project = load_project("online-proj")
        assert project_mounts_dir(project) == sandbox_live_mounts_dir()


def test_project_mounts_dir_project_returns_per_project_subtree() -> None:
    """``credentials.scope: project`` returns ``project.root / "mounts"``."""
    from terok.lib.orchestration.environment import project_mounts_dir

    yaml_text = _ONLINE_YAML + "credentials:\n  scope: project\n"
    with mock_git_config(), project_env(yaml_text, project_name="online-proj"):
        project = load_project("online-proj")
        assert project_mounts_dir(project) == project.root / "mounts"


def test_check_project_credentials_present_shared_is_noop() -> None:
    """Shared-scope projects skip the check — host-wide bucket may be empty by design."""
    from terok.lib.orchestration.environment import _check_project_credentials_present

    with mock_git_config(), project_env(_ONLINE_YAML, project_name="online-proj"):
        project = load_project("online-proj")
        # Even with an empty vault, shared-scope projects must not raise —
        # the agent prompts for login on first turn (existing behaviour).
        # The DB lookup is also short-circuited (no point asking).
        with patch(
            "terok.lib.integrations.executor.list_authenticated_agents", return_value=[]
        ) as mock_list:
            _check_project_credentials_present(project)  # must not raise
        mock_list.assert_not_called()


def test_check_project_credentials_present_empty_bucket_raises() -> None:
    """Project-scope project with empty vault row fails fast with auth hint."""
    from terok.lib.orchestration.environment import _check_project_credentials_present

    yaml_text = _ONLINE_YAML + "credentials:\n  scope: project\n"
    with mock_git_config(), project_env(yaml_text, project_name="online-proj"):
        project = load_project("online-proj")
        with (
            patch("terok.lib.integrations.executor.list_authenticated_agents", return_value=[]),
            pytest.raises(SystemExit, match="terok auth"),
        ):
            _check_project_credentials_present(project)


def test_check_project_credentials_present_populated_bucket_passes() -> None:
    """Project-scope project with a credential in its set passes silently."""
    from terok.lib.orchestration.environment import _check_project_credentials_present

    yaml_text = _ONLINE_YAML + "credentials:\n  scope: project\n"
    with mock_git_config(), project_env(yaml_text, project_name="online-proj"):
        project = load_project("online-proj")
        with patch(
            "terok.lib.integrations.executor.list_authenticated_agents",
            return_value=["claude"],
        ) as mock_list:
            _check_project_credentials_present(project)  # must not raise
        # The scope passed in is the project's credential_set, not "default".
        mock_list.assert_called_once_with(scope="online-proj")
