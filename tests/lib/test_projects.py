# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for project loading and listing helpers."""

from __future__ import annotations

import os
import tempfile
import unittest.mock
from pathlib import Path

import pytest

from terok.lib.containers.project_state import get_project_state
from terok.lib.core.config import build_root, state_root
from terok.lib.core.projects import list_projects, load_project
from test_utils import project_env, write_project


def project_yaml(
    project_id: str,
    *,
    security_class: str | None = None,
    authorship: str | None = None,
    shield_drop_on_task_start: bool | None = None,
) -> str:
    """Build project YAML for tests with optional sections."""
    lines = ["project:", f"  id: {project_id}"]
    if security_class is not None:
        lines.append(f"  security_class: {security_class}")
    lines += ["git:", "  upstream_url: https://example.com/repo.git"]
    if authorship is not None:
        lines.append(f"  authorship: {authorship}")
    if shield_drop_on_task_start is not None:
        lines += ["shield:", f"  drop_on_task_start: {str(shield_drop_on_task_start).lower()}"]
    return "\n".join(lines) + "\n"


def assert_loaded_project(project_id: str, yaml_text: str):
    """Load a project inside an isolated temp config and return it."""
    with project_env(yaml_text, project_id=project_id):
        project = load_project(project_id)
    return project


class TestProject:
    """Tests for project loading/listing."""

    def test_load_project_gatekeeping_defaults(self) -> None:
        project_id = "proj1"
        with project_env(
            project_yaml(project_id, security_class="gatekeeping"),
            project_id=project_id,
        ):
            project = load_project(project_id)
            assert project.id == project_id
            assert project.security_class == "gatekeeping"
            assert project.tasks_root == (state_root() / "tasks" / project_id).resolve()
            assert project.gate_path == (state_root() / "gate" / f"{project_id}.git").resolve()
            assert project.staging_root == (build_root() / project_id).resolve()
            assert project.git_authorship == "agent-human"

    @pytest.mark.parametrize(
        ("yaml_text", "config_text", "expected"),
        [
            (project_yaml("proj-authorship", authorship="human-agent"), None, "human-agent"),
            (project_yaml("proj-global-authorship"), "git:\n  authorship: human\n", "human"),
        ],
        ids=["project-authorship", "global-authorship"],
    )
    def test_git_authorship_resolution(
        self,
        yaml_text: str,
        config_text: str | None,
        expected: str,
    ) -> None:
        project_id = yaml_text.split("\n", 2)[1].split(": ", 1)[1]
        with project_env(yaml_text, project_id=project_id) as ctx:
            if config_text is None:
                project = load_project(project_id)
            else:
                config_file = ctx.base / "config.yml"
                config_file.write_text(config_text, encoding="utf-8")
                with unittest.mock.patch.dict(os.environ, {"TEROK_CONFIG_FILE": str(config_file)}):
                    project = load_project(project_id)
        assert project.git_authorship == expected

    def test_load_project_invalid_git_authorship_raises(self) -> None:
        with project_env(
            project_yaml("proj-bad-authorship", authorship="mystery-mode"),
            project_id="proj-bad-authorship",
        ):
            with pytest.raises(SystemExit, match="git.authorship"):
                load_project("proj-bad-authorship")

    def test_list_projects_prefers_user(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            system_root = base / "system"
            user_projects = base / "user" / "terok" / "projects"
            system_root.mkdir(parents=True, exist_ok=True)
            user_projects.mkdir(parents=True, exist_ok=True)

            write_project(
                system_root, "proj2", project_yaml("proj2").replace("example.com", "system.example")
            )
            write_project(
                user_projects, "proj2", project_yaml("proj2").replace("example.com", "user.example")
            )

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(system_root),
                    "XDG_CONFIG_HOME": str(base / "user"),
                },
            ):
                projects = list_projects()
        assert len(projects) == 1
        assert projects[0].upstream_url == "https://user.example/repo.git"
        assert projects[0].root == (user_projects / "proj2").resolve()

    @pytest.mark.parametrize(
        ("writer", "callable_under_test", "expected"),
        [
            (
                lambda root: write_project(
                    root,
                    "good",
                    "project:\n  id: good\ngit:\n  upstream_url: https://example.com/good.git\n",
                ),
                list_projects,
                "good",
            ),
        ],
        ids=["list-projects-skips-bad-yaml"],
    )
    def test_list_projects_skips_malformed_yaml(
        self,
        writer,
        callable_under_test,
        expected: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_dir = base / "config"
            writer(config_dir)
            write_project(config_dir, "bad", "project:\n  id: bad\n  foo: [invalid\n")
            with unittest.mock.patch.dict(
                os.environ,
                {"TEROK_CONFIG_DIR": str(config_dir), "XDG_CONFIG_HOME": str(base / "empty")},
            ):
                projects = callable_under_test()
        assert len(projects) == 1
        assert projects[0].id == expected

    def test_load_project_malformed_yaml(self) -> None:
        malformed = "project:\n  id: bad-yaml\n  foo: [invalid yaml\n"
        with project_env(malformed, project_id="bad-yaml"):
            with pytest.raises(SystemExit, match="Failed to parse"):
                load_project("bad-yaml")

    @pytest.mark.parametrize(
        ("project_id", "yaml_text", "expected"),
        [
            ("proj-shield-default", project_yaml("proj-shield-default"), True),
            (
                "proj-shield-drop",
                project_yaml("proj-shield-drop", shield_drop_on_task_start=True),
                True,
            ),
            (
                "proj-shield-no-drop",
                project_yaml("proj-shield-no-drop", shield_drop_on_task_start=False),
                False,
            ),
        ],
        ids=["default", "enabled", "disabled"],
    )
    def test_shield_drop_on_task_start(
        self,
        project_id: str,
        yaml_text: str,
        expected: bool,
    ) -> None:
        with project_env(yaml_text, project_id=project_id):
            assert load_project(project_id).shield_drop_on_task_start is expected

    def test_get_project_state(self) -> None:
        project_id = "proj3"
        with project_env(
            project_yaml(project_id), project_id=project_id, with_config_file=True
        ) as env:
            stage_dir = build_root() / project_id
            stage_dir.mkdir(parents=True, exist_ok=True)
            for name in ("L0.Dockerfile", "L1.cli.Dockerfile", "L1.ui.Dockerfile", "L2.Dockerfile"):
                (stage_dir / name).write_text("", encoding="utf-8")

            ssh_dir = env.envs_dir / f"_ssh-config-{project_id}"
            ssh_dir.mkdir(parents=True, exist_ok=True)
            (ssh_dir / "config").write_text("", encoding="utf-8")

            gate_dir = state_root() / "gate" / f"{project_id}.git"
            gate_dir.mkdir(parents=True, exist_ok=True)

            with (
                unittest.mock.patch(
                    "terok.lib.containers.project_state.subprocess.run"
                ) as run_mock,
                unittest.mock.patch(
                    "terok.lib.core.projects._get_global_git_config", return_value=None
                ),
            ):
                run_mock.return_value.returncode = 0
                state = get_project_state(project_id, gate_commit_provider=lambda _pid: None)

        assert state == {
            "dockerfiles": True,
            "dockerfiles_old": True,
            "images": True,
            "images_old": True,
            "ssh": True,
            "gate": True,
            "gate_last_commit": None,
        }
