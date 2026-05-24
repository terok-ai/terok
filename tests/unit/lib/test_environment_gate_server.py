# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for environment.py gate-server integration."""

from __future__ import annotations

from unittest.mock import PropertyMock, patch

import pytest

from terok.lib.core.projects import ProjectConfig, load_project
from terok.lib.orchestration.environment import _security_mode_env_and_volumes
from tests.test_utils import mock_git_config, project_env
from tests.testnet import GATE_PORT, gate_repo_url

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


def gate_mounts(volumes: list[str]) -> list[str]:
    """Return any gate-related volume mounts from the generated volume list."""
    return [volume for volume in volumes if "git-gate" in volume or "gate" in volume.split(":")[0]]


def resolve_security_env(
    yaml_text: str,
    *,
    project_id: str,
    with_gate: bool,
    token: str | None = None,
    ensure_side_effect: BaseException | None = None,
) -> tuple[ProjectConfig, dict[str, str], list[str]]:
    """Load a project and evaluate gate-related env/volume settings."""
    with (
        mock_git_config(),
        project_env(yaml_text, project_id=project_id, with_gate=with_gate) as ctx,
        patch(
            "terok.lib.orchestration.environment.GateServerManager.ensure_reachable",
            side_effect=ensure_side_effect,
        ),
        patch(
            "terok.lib.orchestration.environment.GateServerManager.server_port",
            new_callable=PropertyMock,
            return_value=GATE_PORT,
        ),
        patch(
            "terok.lib.orchestration.environment.GateServerManager.gate_base_path",
            new_callable=PropertyMock,
            return_value=ctx.base / "sandbox-state" / "gate",
        ),
        patch("terok.lib.orchestration.environment.TokenStore.create", return_value=token),
    ):
        from unittest.mock import MagicMock

        project = load_project(project_id)
        env, volumes = _security_mode_env_and_volumes(project, "1", MagicMock())
    return project, env, volumes


@pytest.mark.parametrize(
    ("yaml_text", "project_id", "token", "env_key"),
    [
        pytest.param(_GATEKEEPING_YAML, "gk-proj", "deadbeef" * 4, "CODE_REPO", id="gatekeeping"),
        pytest.param(_ONLINE_YAML, "online-proj", "cafebabe" * 4, "CLONE_FROM", id="online"),
    ],
)
def test_gate_projects_use_http_urls_with_tokens(
    yaml_text: str,
    project_id: str,
    token: str,
    env_key: str,
) -> None:
    """Gate-backed project modes generate token-authenticated HTTP URLs."""
    project, env, volumes = resolve_security_env(
        yaml_text,
        project_id=project_id,
        with_gate=True,
        token=token,
    )

    assert env[env_key] == gate_repo_url(project_id, token)
    assert gate_mounts(volumes) == []

    if project.security_class == "gatekeeping":
        assert env["GIT_BRANCH"] == "main"
    else:
        assert env["CODE_REPO"] == "https://example.com/repo.git"


def test_gatekeeping_missing_gate_raises() -> None:
    """Gatekeeping mode requires a synced gate mirror before task startup."""
    from unittest.mock import MagicMock

    with mock_git_config(), project_env(_GATEKEEPING_YAML, project_id="gk-proj", with_gate=False):
        project = load_project("gk-proj")
        with pytest.raises(SystemExit, match="gate-sync"):
            _security_mode_env_and_volumes(project, "1", MagicMock())


def test_gatekeeping_server_not_running_raises() -> None:
    """Gatekeeping mode fails when the gate server cannot be reached."""
    with pytest.raises(SystemExit, match="Gate server"):
        resolve_security_env(
            _GATEKEEPING_YAML,
            project_id="gk-proj",
            with_gate=True,
            ensure_side_effect=SystemExit("Gate server unavailable"),
        )


@pytest.mark.parametrize(
    "server_reachable",
    [pytest.param(True, id="server-up"), pytest.param(False, id="server-down")],
)
def test_online_gate_server_fallback(server_reachable: bool) -> None:
    """Online mode uses CLONE_FROM only when the gate server is reachable."""
    _project, env, volumes = resolve_security_env(
        _ONLINE_YAML,
        project_id="online-proj",
        with_gate=True,
        token="cafebabe" * 4,
        ensure_side_effect=None if server_reachable else SystemExit("server down"),
    )

    if server_reachable:
        expected_url = gate_repo_url("online-proj", "cafebabe" * 4)
        assert env["CLONE_FROM"] == expected_url
        # Gate is also surfaced as a named "gate" remote in online mode
        # so the agent can push WIP host-locally without going upstream.
        assert env["GATE_REMOTE_URL"] == expected_url
    else:
        assert "CLONE_FROM" not in env
        assert "GATE_REMOTE_URL" not in env
    assert env["CODE_REPO"] == "https://example.com/repo.git"
    assert gate_mounts(volumes) == []


def test_online_without_gate_has_no_clone_from() -> None:
    """Online mode without a gate mirror clones directly from upstream only."""
    _project, env, volumes = resolve_security_env(
        _ONLINE_YAML,
        project_id="online-proj",
        with_gate=False,
    )
    assert "CLONE_FROM" not in env
    assert "GATE_REMOTE_URL" not in env
    assert env["CODE_REPO"] == "https://example.com/repo.git"
    assert gate_mounts(volumes) == []


def test_gatekeeping_does_not_set_gate_remote_url() -> None:
    """Gatekeeping mode keeps the gate as origin — no separate "gate" remote."""
    _project, env, _volumes = resolve_security_env(
        _GATEKEEPING_YAML,
        project_id="gk-proj",
        with_gate=True,
        token="deadbeef" * 4,
    )
    assert "GATE_REMOTE_URL" not in env


def test_tcp_mode_without_gate_port_raises() -> None:
    """Regression: TCP mode with a ``None`` gate port now fails fast.

    Previously the function silently built ``http://...@host:None/repo``,
    which only surfaced as an opaque clone failure inside the container.
    """
    from unittest.mock import MagicMock

    from terok.lib.orchestration.environment import _security_mode_env_and_volumes

    with (
        mock_git_config(),
        project_env(_GATEKEEPING_YAML, project_id="gk-proj", with_gate=True),
        patch("terok.lib.orchestration.environment.GateServerManager.ensure_reachable"),
        patch(
            "terok.lib.orchestration.environment.GateServerManager.server_port",
            new_callable=PropertyMock,
            return_value=None,
        ),
        patch(
            "terok.lib.orchestration.environment.GateServerManager.gate_base_path",
            new_callable=PropertyMock,
        ),
        patch("terok.lib.orchestration.environment.TokenStore.create", return_value="t" * 32),
    ):
        project = load_project("gk-proj")
        with pytest.raises(SystemExit, match="Gate server port"):
            _security_mode_env_and_volumes(project, "1", MagicMock(), use_socket=False)


def test_project_mounts_dir_shared_returns_global() -> None:
    """``credentials.scope: shared`` (default) returns the host-wide mount tree."""
    from terok.lib.core.config import sandbox_live_mounts_dir
    from terok.lib.orchestration.environment import project_mounts_dir

    with mock_git_config(), project_env(_ONLINE_YAML, project_id="online-proj"):
        project = load_project("online-proj")
        assert project_mounts_dir(project) == sandbox_live_mounts_dir()


def test_project_mounts_dir_project_returns_per_project_subtree() -> None:
    """``credentials.scope: project`` returns ``project.root / "mounts"``."""
    from terok.lib.orchestration.environment import project_mounts_dir

    yaml_text = _ONLINE_YAML + "credentials:\n  scope: project\n"
    with mock_git_config(), project_env(yaml_text, project_id="online-proj"):
        project = load_project("online-proj")
        assert project_mounts_dir(project) == project.root / "mounts"


def test_check_project_credentials_present_shared_is_noop() -> None:
    """Shared-scope projects skip the check — host-wide bucket may be empty by design."""
    from terok.lib.orchestration.environment import _check_project_credentials_present

    with mock_git_config(), project_env(_ONLINE_YAML, project_id="online-proj"):
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
    with mock_git_config(), project_env(yaml_text, project_id="online-proj"):
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
    with mock_git_config(), project_env(yaml_text, project_id="online-proj"):
        project = load_project("online-proj")
        with patch(
            "terok.lib.integrations.executor.list_authenticated_agents",
            return_value=["claude"],
        ) as mock_list:
            _check_project_credentials_present(project)  # must not raise
        # The scope passed in is the project's credential_set, not "default".
        mock_list.assert_called_once_with(scope="online-proj")
