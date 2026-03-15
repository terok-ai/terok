# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for project deletion helpers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from terok.lib.core.config import build_root, state_root
from terok.lib.core.projects import load_project
from terok.lib.facade import delete_project
from test_utils import project_env, write_project

EnvSetup = Callable[[SimpleNamespace, str], Path]


def project_yaml(project_id: str, *, upstream_url: str = "https://example.com/repo.git") -> str:
    """Build a minimal project config for deletion tests."""
    return f"project:\n  id: {project_id}\ngit:\n  upstream_url: {upstream_url}\n"


def project_root(_env: SimpleNamespace, project_id: str) -> Path:
    """Return the config-root directory for a loaded project."""
    return load_project(project_id).root


def build_dir(_env: SimpleNamespace, project_id: str) -> Path:
    """Create and return the project's build dir."""
    target = build_root() / project_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "L2.Dockerfile").write_text("FROM scratch", encoding="utf-8")
    return target


def ssh_dir(env: SimpleNamespace, project_id: str) -> Path:
    """Create and return the project's SSH config dir."""
    target = env.envs_dir / f"_ssh-config-{project_id}"
    target.mkdir(parents=True, exist_ok=True)
    (target / "config").write_text("# ssh config", encoding="utf-8")
    return target


def task_state_dir(_env: SimpleNamespace, project_id: str) -> Path:
    """Create and return the project's state metadata dir."""
    target = state_root() / "projects" / project_id
    tasks_dir = target / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "1.yml").write_text("task_id: '1'\n", encoding="utf-8")
    return target


def gate_dir(env: SimpleNamespace, _project_id: str) -> Path:
    """Return the gate mirror directory from ``project_env``."""
    assert env.gate_dir is not None
    return env.gate_dir


@pytest.mark.parametrize(
    ("project_id", "env_kwargs", "setup_target"),
    [
        ("del-proj", {"with_config_file": True}, project_root),
        ("del-build", {"with_config_file": True}, build_dir),
        ("del-ssh", {"with_config_file": True}, ssh_dir),
        ("del-meta", {}, task_state_dir),
        ("del-gate", {"with_gate": True}, gate_dir),
    ],
    ids=["config-dir", "build-dir", "ssh-dir", "task-metadata-dir", "gate-dir"],
)
def test_delete_project_removes_managed_directories(
    project_id: str,
    env_kwargs: dict[str, bool],
    setup_target: EnvSetup,
) -> None:
    with project_env(project_yaml(project_id), project_id=project_id, **env_kwargs) as env:
        target = setup_target(env, project_id)
        assert target.is_dir()
        delete_project(project_id)
        assert not target.exists()


def test_delete_project_skips_shared_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_root = tmp_path / "config"
    state_dir = tmp_path / "state"
    gate_path = state_dir / "gate" / "shared.git"
    gate_path.mkdir(parents=True, exist_ok=True)
    config_root.mkdir(parents=True, exist_ok=True)

    for project_id, upstream in (("proj-a", "a"), ("proj-b", "b")):
        write_project(
            config_root,
            project_id,
            project_yaml(project_id, upstream_url=f"https://example.com/{upstream}.git")
            + f"gate:\n  path: {gate_path}\n",
        )

    monkeypatch.setenv("TEROK_CONFIG_DIR", str(config_root))
    monkeypatch.setenv("TEROK_STATE_DIR", str(state_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))

    result = delete_project("proj-a")
    assert gate_path.is_dir()
    assert any("proj-b" in entry for entry in result["skipped"])


def test_delete_project_returns_deleted_paths() -> None:
    project_id = "del-ret"
    with project_env(project_yaml(project_id), project_id=project_id):
        result = delete_project(project_id)
        assert isinstance(result["deleted"], list)
        assert isinstance(result["skipped"], list)
        assert result["archive"] is not None
        assert Path(result["archive"]).is_file()
        assert any(project_id in path for path in result["deleted"])
