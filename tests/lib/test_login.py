# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

import types
import unittest
import unittest.mock

import yaml

from terok.lib.containers.tasks import get_login_command, task_login, task_new
from test_utils import mock_git_config, project_env


class LoginTests(unittest.TestCase):
    """Tests for task_login, get_login_command, and _validate_login."""

    @staticmethod
    def _setup_task_with_mode(
        ctx: types.SimpleNamespace, project_id: str, *, mode: str | None = None
    ) -> None:
        """Create a task inside an active project_env, optionally setting mode in metadata."""
        task_new(project_id)
        if mode:
            meta_dir = ctx.state_dir / "projects" / project_id / "tasks"
            meta_path = meta_dir / "1.yml"
            meta = yaml.safe_load(meta_path.read_text())
            meta["mode"] = mode
            meta_path.write_text(yaml.safe_dump(meta))

    def test_task_login_unknown_task(self) -> None:
        """task_login raises SystemExit for non-existent task."""
        project_id = "proj_login_unknown"
        with project_env(f"project:\n  id: {project_id}\n", project_id=project_id):
            with self.assertRaises(SystemExit) as ctx:
                task_login(project_id, "999")
            self.assertIn("Unknown task", str(ctx.exception))

    def test_task_login_no_mode(self) -> None:
        """task_login raises SystemExit when task has never been run (no mode)."""
        project_id = "proj_login_nomode"
        with project_env(f"project:\n  id: {project_id}\n", project_id=project_id) as ctx:
            self._setup_task_with_mode(ctx, project_id)
            with self.assertRaises(SystemExit) as exc_ctx:
                task_login(project_id, "1")
            self.assertIn("never been run", str(exc_ctx.exception))

    def test_task_login_container_not_found(self) -> None:
        """task_login raises SystemExit when container does not exist."""
        project_id = "proj_login_nf"
        with project_env(f"project:\n  id: {project_id}\n", project_id=project_id) as ctx:
            self._setup_task_with_mode(ctx, project_id, mode="cli")
            with unittest.mock.patch(
                "terok.lib.containers.tasks.get_container_state", return_value=None
            ):
                with self.assertRaises(SystemExit) as exc_ctx:
                    task_login(project_id, "1")
                self.assertIn("does not exist", str(exc_ctx.exception))

    def test_task_login_container_not_running(self) -> None:
        """task_login raises SystemExit when container is not running."""
        project_id = "proj_login_nr"
        with project_env(f"project:\n  id: {project_id}\n", project_id=project_id) as ctx:
            self._setup_task_with_mode(ctx, project_id, mode="cli")
            with unittest.mock.patch(
                "terok.lib.containers.tasks.get_container_state", return_value="exited"
            ):
                with self.assertRaises(SystemExit) as exc_ctx:
                    task_login(project_id, "1")
                self.assertIn("not running", str(exc_ctx.exception))

    def test_task_login_success(self) -> None:
        """task_login calls os.execvp with correct podman+tmux command."""
        project_id = "proj_login_ok"
        with project_env(f"project:\n  id: {project_id}\n", project_id=project_id) as ctx:
            self._setup_task_with_mode(ctx, project_id, mode="cli")
            with (
                unittest.mock.patch(
                    "terok.lib.containers.tasks.get_container_state",
                    return_value="running",
                ),
                unittest.mock.patch("terok.lib.containers.tasks.os.execvp") as mock_exec,
            ):
                task_login(project_id, "1")

                mock_exec.assert_called_once_with(
                    "podman",
                    [
                        "podman",
                        "exec",
                        "-it",
                        f"{project_id}-cli-1",
                        "tmux",
                        "new-session",
                        "-A",
                        "-s",
                        "main",
                    ],
                )

    def test_get_login_command_returns_list(self) -> None:
        """get_login_command returns correct command list for CLI-mode task."""
        project_id = "proj_logincmd"
        with project_env(f"project:\n  id: {project_id}\n", project_id=project_id) as ctx:
            self._setup_task_with_mode(ctx, project_id, mode="cli")
            with unittest.mock.patch(
                "terok.lib.containers.tasks.get_container_state",
                return_value="running",
            ):
                cmd = get_login_command(project_id, "1")
                self.assertEqual(
                    cmd,
                    [
                        "podman",
                        "exec",
                        "-it",
                        f"{project_id}-cli-1",
                        "tmux",
                        "new-session",
                        "-A",
                        "-s",
                        "main",
                    ],
                )

    def test_get_login_command_web_mode(self) -> None:
        """get_login_command uses web mode container name when mode is web."""
        project_id = "proj_loginweb"
        with project_env(f"project:\n  id: {project_id}\n", project_id=project_id) as ctx:
            self._setup_task_with_mode(ctx, project_id, mode="web")
            with unittest.mock.patch(
                "terok.lib.containers.tasks.get_container_state",
                return_value="running",
            ):
                cmd = get_login_command(project_id, "1")
                self.assertEqual(cmd[3], f"{project_id}-web-1")

    def test_login_no_longer_injects_agent_config(self) -> None:
        """get_login_command does NOT inject agent config (handled via mount)."""
        project_id = "proj_login_cfg"
        yaml_text = f"project:\n  id: {project_id}\nagent:\n  model: sonnet\n"
        with project_env(yaml_text, project_id=project_id) as ctx:
            self._setup_task_with_mode(ctx, project_id, mode="cli")
            with (
                unittest.mock.patch(
                    "terok.lib.containers.tasks.get_container_state",
                    return_value="running",
                ),
                mock_git_config(),
                unittest.mock.patch("terok.lib.containers.tasks.subprocess.run") as mock_run,
            ):
                cmd = get_login_command(project_id, "1")

                # Should still return the tmux command
                self.assertEqual(cmd[3], f"{project_id}-cli-1")
                self.assertIn("tmux", cmd)

                # No podman exec/cp calls --- config injection is via mount
                mock_run.assert_not_called()
