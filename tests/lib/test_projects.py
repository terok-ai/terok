# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from terok.lib.containers.project_state import get_project_state
from terok.lib.containers.tasks import task_new
from terok.lib.core.config import build_root, state_root
from terok.lib.core.projects import list_projects, load_project
from terok.lib.facade import project_delete
from test_utils import project_env, write_project


class ProjectTests(unittest.TestCase):
    def test_load_project_gatekeeping_defaults(self) -> None:
        project_id = "proj1"
        yaml = f"""\
project:
  id: {project_id}
  security_class: gatekeeping
git:
  upstream_url: https://example.com/repo.git
"""
        with project_env(yaml, project_id=project_id):
            proj = load_project(project_id)
            self.assertEqual(proj.id, project_id)
            self.assertEqual(proj.security_class, "gatekeeping")
            self.assertEqual(proj.tasks_root, (state_root() / "tasks" / project_id).resolve())
            self.assertEqual(
                proj.gate_path, (state_root() / "gate" / f"{project_id}.git").resolve()
            )
            self.assertEqual(proj.staging_root, (build_root() / project_id).resolve())

    def test_list_projects_prefers_user(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            system_root = base / "system"
            user_root = base / "user"
            system_root.mkdir(parents=True, exist_ok=True)
            user_projects = user_root / "terok" / "projects"

            project_id = "proj2"
            write_project(
                system_root,
                project_id,
                f"""\nproject:\n  id: {project_id}\ngit:\n  upstream_url: https://system.example/repo.git\n""".lstrip(),
            )
            write_project(
                user_projects,
                project_id,
                f"""\nproject:\n  id: {project_id}\ngit:\n  upstream_url: https://user.example/repo.git\n""".lstrip(),
            )

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(system_root),
                    "XDG_CONFIG_HOME": str(user_root),
                },
            ):
                projects = list_projects()
                self.assertEqual(len(projects), 1)
                self.assertEqual(projects[0].upstream_url, "https://user.example/repo.git")
                self.assertEqual(projects[0].root, (user_projects / project_id).resolve())

    def test_load_project_malformed_yaml(self) -> None:
        """load_project raises SystemExit on malformed YAML (not an unhandled YAMLError)."""
        project_id = "bad-yaml"
        malformed = "project:\n  id: bad-yaml\n  foo: [invalid yaml\n"
        with project_env(malformed, project_id=project_id):
            with self.assertRaises(SystemExit) as ctx:
                load_project(project_id)
            self.assertIn("Failed to parse", str(ctx.exception))

    def test_list_projects_skips_malformed_yaml(self) -> None:
        """list_projects skips projects with malformed YAML instead of crashing."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_dir = base / "config"

            # One valid project
            write_project(
                config_dir,
                "good",
                "project:\n  id: good\ngit:\n  upstream_url: https://example.com/good.git\n",
            )
            # One malformed project
            write_project(config_dir, "bad", "project:\n  id: bad\n  foo: [invalid\n")

            with unittest.mock.patch.dict(
                os.environ,
                {"TEROK_CONFIG_DIR": str(config_dir), "XDG_CONFIG_HOME": str(base / "empty")},
            ):
                projects = list_projects()
                # The malformed project should be skipped, only the good one returned
                self.assertEqual(len(projects), 1)
                self.assertEqual(projects[0].id, "good")

    def test_get_project_state(self) -> None:
        project_id = "proj3"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
"""
        with project_env(yaml, project_id=project_id, with_config_file=True) as env:
            stage_dir = build_root() / project_id
            stage_dir.mkdir(parents=True, exist_ok=True)
            for name in (
                "L0.Dockerfile",
                "L1.cli.Dockerfile",
                "L1.ui.Dockerfile",
                "L2.Dockerfile",
            ):
                (stage_dir / name).write_text("", encoding="utf-8")

            ssh_dir = env.envs_dir / f"_ssh-config-{project_id}"
            ssh_dir.mkdir(parents=True, exist_ok=True)
            (ssh_dir / "config").write_text("", encoding="utf-8")

            gate_dir = state_root() / "gate" / f"{project_id}.git"
            gate_dir.mkdir(parents=True, exist_ok=True)

            with unittest.mock.patch(
                "terok.lib.containers.project_state.subprocess.run"
            ) as run_mock:
                run_mock.return_value.returncode = 0
                state = get_project_state(project_id, gate_commit_provider=lambda pid: None)

            self.assertEqual(
                state,
                {
                    "dockerfiles": True,
                    "dockerfiles_old": True,
                    "images": True,
                    "images_old": True,
                    "ssh": True,
                    "gate": True,
                    "gate_last_commit": None,
                },
            )

    def test_project_delete_removes_config_and_state(self) -> None:
        """project_delete removes project config dir and task state."""
        project_id = "del-proj"
        yaml_text = f"project:\n  id: {project_id}\n"
        with project_env(yaml_text, project_id=project_id) as ctx:
            # Create a task so there's state to clean up
            with unittest.mock.patch("terok.lib.containers.tasks.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                task_id = task_new(project_id)

            meta_path = ctx.state_dir / "projects" / project_id / "tasks" / f"{task_id}.yml"
            self.assertTrue(meta_path.is_file())

            proj = load_project(project_id)
            workspace = proj.tasks_root / str(task_id)
            self.assertTrue(workspace.is_dir())
            config_dir = proj.root
            self.assertTrue(config_dir.is_dir())

            with unittest.mock.patch("terok.lib.containers.tasks.subprocess.run") as run_mock:
                run_mock.return_value.returncode = 0
                project_delete(project_id)

            # Config, workspace, and metadata should all be gone
            self.assertFalse(config_dir.exists())
            self.assertFalse(workspace.exists())
            self.assertFalse(meta_path.exists())

    def test_project_delete_preserves_shared_gate(self) -> None:
        """project_delete keeps the gate if another project shares it."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_dir = base / "config"
            state_dir = base / "state"

            # Two projects sharing the same gate path
            gate_path = state_dir / "gate" / "shared.git"
            gate_path.mkdir(parents=True, exist_ok=True)
            (gate_path / "HEAD").write_text("ref: refs/heads/main\n")

            for pid in ("proj-a", "proj-b"):
                write_project(
                    config_dir,
                    pid,
                    f"project:\n  id: {pid}\ngate:\n  path: {gate_path}\n",
                )

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(config_dir),
                    "TEROK_STATE_DIR": str(state_dir),
                },
            ):
                with unittest.mock.patch("terok.lib.containers.tasks.subprocess.run"):
                    project_delete("proj-a")

                # Gate should still exist because proj-b uses it
                self.assertTrue(gate_path.is_dir())
                # proj-a config should be gone
                self.assertFalse((config_dir / "proj-a").exists())
                # proj-b should still be loadable
                proj_b = load_project("proj-b")
                self.assertEqual(proj_b.id, "proj-b")

    def test_project_delete_removes_unshared_gate(self) -> None:
        """project_delete removes the gate when no other project uses it."""
        project_id = "solo-gate"
        yaml_text = f"project:\n  id: {project_id}\n"
        with project_env(yaml_text, project_id=project_id) as ctx:
            # Create a gate directory
            gate_dir = ctx.state_dir / "gate" / f"{project_id}.git"
            gate_dir.mkdir(parents=True, exist_ok=True)
            (gate_dir / "HEAD").write_text("ref: refs/heads/main\n")

            with unittest.mock.patch("terok.lib.containers.tasks.subprocess.run"):
                project_delete(project_id)

            self.assertFalse(gate_dir.exists())

    def test_project_delete_nonexistent_raises(self) -> None:
        """project_delete raises SystemExit for a nonexistent project."""
        with project_env("project:\n  id: exists\n", project_id="exists"):
            with self.assertRaises(SystemExit):
                project_delete("no-such-project")
