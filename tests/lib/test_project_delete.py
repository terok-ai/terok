# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from terok.lib.core.config import build_root, state_root
from terok.lib.core.projects import load_project
from terok.lib.facade import delete_project
from test_utils import project_env, write_project


class DeleteProjectTests(unittest.TestCase):
    """Tests for the delete_project facade function."""

    def test_delete_project_removes_config_dir(self) -> None:
        """delete_project removes the project configuration directory."""
        project_id = "del-proj"
        yaml = f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n"
        with project_env(yaml, project_id=project_id, with_config_file=True):
            project = load_project(project_id)
            self.assertTrue(project.root.is_dir())
            delete_project(project_id)
            self.assertFalse(project.root.is_dir())

    def test_delete_project_removes_build_artifacts(self) -> None:
        """delete_project removes the build directory."""
        project_id = "del-build"
        yaml = f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n"
        with project_env(yaml, project_id=project_id, with_config_file=True):
            bd = build_root() / project_id
            bd.mkdir(parents=True, exist_ok=True)
            (bd / "L2.Dockerfile").write_text("FROM scratch", encoding="utf-8")
            self.assertTrue(bd.is_dir())
            delete_project(project_id)
            self.assertFalse(bd.is_dir())

    def test_delete_project_removes_ssh_dir(self) -> None:
        """delete_project removes SSH credentials directory."""
        project_id = "del-ssh"
        yaml = f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n"
        with project_env(yaml, project_id=project_id, with_config_file=True) as env:
            ssh_dir = env.envs_dir / f"_ssh-config-{project_id}"
            ssh_dir.mkdir(parents=True, exist_ok=True)
            (ssh_dir / "config").write_text("# ssh config", encoding="utf-8")
            self.assertTrue(ssh_dir.is_dir())
            delete_project(project_id)
            self.assertFalse(ssh_dir.is_dir())

    def test_delete_project_removes_task_metadata(self) -> None:
        """delete_project removes task metadata directory."""
        project_id = "del-meta"
        yaml = f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n"
        with project_env(yaml, project_id=project_id):
            meta_dir = state_root() / "projects" / project_id / "tasks"
            meta_dir.mkdir(parents=True, exist_ok=True)
            (meta_dir / "1.yml").write_text("task_id: '1'\n", encoding="utf-8")
            self.assertTrue(meta_dir.is_dir())
            delete_project(project_id)
            self.assertFalse((state_root() / "projects" / project_id).is_dir())

    def test_delete_project_removes_gate(self) -> None:
        """delete_project removes the gate when not shared."""
        project_id = "del-gate"
        yaml = f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n"
        with project_env(yaml, project_id=project_id, with_gate=True) as env:
            gate_path = env.gate_dir
            self.assertTrue(gate_path.is_dir())
            delete_project(project_id)
            self.assertFalse(gate_path.is_dir())

    def test_delete_project_skips_shared_gate(self) -> None:
        """delete_project skips the gate when shared with another project."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_root = base / "config"
            state_dir = base / "state"

            # Two projects sharing the same gate path
            gate_path = state_dir / "gate" / "shared.git"
            gate_path.mkdir(parents=True, exist_ok=True)

            proj1_yaml = (
                "project:\n  id: proj-a\ngit:\n  upstream_url: https://example.com/a.git\n"
                f"gate:\n  path: {gate_path}\n"
            )
            proj2_yaml = (
                "project:\n  id: proj-b\ngit:\n  upstream_url: https://example.com/b.git\n"
                f"gate:\n  path: {gate_path}\n"
            )
            write_project(config_root, "proj-a", proj1_yaml)
            write_project(config_root, "proj-b", proj2_yaml)

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(config_root),
                    "TEROK_STATE_DIR": str(state_dir),
                    "XDG_CONFIG_HOME": str(base / "empty"),
                },
            ):
                result = delete_project("proj-a")
                # Gate should still exist because proj-b shares it
                self.assertTrue(gate_path.is_dir())
                self.assertTrue(len(result["skipped"]) > 0)
                self.assertIn("proj-b", result["skipped"][0])

    def test_delete_project_returns_deleted_paths(self) -> None:
        """delete_project returns a dict with deleted and skipped lists."""
        project_id = "del-ret"
        yaml = f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n"
        with project_env(yaml, project_id=project_id):
            result = delete_project(project_id)
            self.assertIsInstance(result["deleted"], list)
            self.assertIsInstance(result["skipped"], list)
            # At least the config dir should be in deleted
            self.assertTrue(any(project_id in p for p in result["deleted"]))
