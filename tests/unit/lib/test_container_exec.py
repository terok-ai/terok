# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for container-based command execution."""

import subprocess

from terok_sandbox import ExecResult

from terok.lib.orchestration.container_exec import container_git_diff


class TestContainerGitDiff:
    """Tests for container_git_diff."""

    def test_running_container_returns_diff(self, mock_runtime) -> None:
        """Diff output returned when the container is already running."""
        expected = "diff --git a/f.txt b/f.txt\n+hello\n"
        mock_runtime.container.return_value.state = "running"
        mock_runtime.exec.return_value = ExecResult(exit_code=0, stdout=expected, stderr="")
        result = container_git_diff("proj", "1", "cli", "HEAD")
        assert result == expected
        mock_runtime.exec.assert_called_once()
        mock_runtime.container.return_value.start.assert_not_called()

    def test_stopped_container_restarts_and_stops(self, mock_runtime) -> None:
        """Stopped CLI container is started, exec'd, and stopped again."""
        expected = " 1 file changed\n"
        mock_runtime.container.return_value.state = "exited"
        mock_runtime.exec.return_value = ExecResult(exit_code=0, stdout=expected, stderr="")
        result = container_git_diff("proj", "2", "cli", "--stat", "HEAD@{1}..HEAD")
        assert result == expected

        mock_runtime.container.assert_any_call("proj-cli-2")
        container = mock_runtime.container.return_value
        container.start.assert_called_once()
        container.stop.assert_called_once_with(timeout=10)
        mock_runtime.exec.assert_called_once()
        args, kwargs = mock_runtime.exec.call_args
        assert args[1] == ["git", "-C", "/workspace", "diff", "--stat", "HEAD@{1}..HEAD"]
        assert kwargs == {"timeout": 30}

    def test_exited_headless_container_not_restarted(self, mock_runtime) -> None:
        """Exited headless (run mode) containers must not be restarted."""
        mock_runtime.container.return_value.state = "exited"
        result = container_git_diff("proj", "1", "run", "--stat", "HEAD@{1}..HEAD")
        assert result is None
        mock_runtime.container.return_value.start.assert_not_called()

    def test_no_container_returns_none(self, mock_runtime) -> None:
        """Return None when the container does not exist."""
        mock_runtime.container.return_value.state = None
        result = container_git_diff("proj", "99", "cli")
        assert result is None

    def test_podman_exec_failure_returns_none(self, mock_runtime) -> None:
        """Return None when git diff fails inside the container."""
        mock_runtime.container.return_value.state = "running"
        mock_runtime.exec.return_value = ExecResult(exit_code=128, stdout="", stderr="")
        result = container_git_diff("proj", "1", "cli", "HEAD")
        assert result is None

    def test_start_failure_returns_none(self, mock_runtime) -> None:
        """Return None when container start fails on a stopped container."""
        mock_runtime.container.return_value.state = "exited"
        mock_runtime.container.return_value.start.side_effect = RuntimeError("start failed")
        # "run" mode refuses to restart, so use "cli" to hit the start path
        result = container_git_diff("proj", "1", "cli")
        assert result is None

    def test_timeout_returns_none(self, mock_runtime) -> None:
        """Return None on podman exec timeout."""
        mock_runtime.container.return_value.state = "running"
        mock_runtime.exec.side_effect = subprocess.TimeoutExpired("podman", 30)
        result = container_git_diff("proj", "1", "cli", "HEAD")
        assert result is None
