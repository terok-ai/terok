# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for container-based command execution."""

import subprocess
import unittest.mock

from terok_sandbox import ExecResult

from terok.lib.orchestration.container_exec import container_git_diff

_MOD = "terok.lib.orchestration.container_exec"


class TestContainerGitDiff:
    """Tests for container_git_diff."""

    def test_running_container_returns_diff(self) -> None:
        """Diff output returned when the container is already running."""
        expected = "diff --git a/f.txt b/f.txt\n+hello\n"
        exec_result = ExecResult(exit_code=0, stdout=expected, stderr="")
        with (
            unittest.mock.patch(f"{_MOD}.get_container_state", return_value="running"),
            unittest.mock.patch(f"{_MOD}.sandbox_exec", return_value=exec_result) as mock_exec,
            unittest.mock.patch(f"{_MOD}.container_start") as mock_start,
        ):
            result = container_git_diff("proj", "1", "cli", "HEAD")
            assert result == expected
            mock_exec.assert_called_once()
            mock_start.assert_not_called()

    def test_stopped_container_restarts_and_stops(self) -> None:
        """Stopped CLI container is started, exec'd, and stopped again."""
        expected = " 1 file changed\n"
        start_result = subprocess.CompletedProcess(args=[], returncode=0, stderr="")
        exec_result = ExecResult(exit_code=0, stdout=expected, stderr="")
        stop_result = subprocess.CompletedProcess(args=[], returncode=0, stderr="")
        with (
            unittest.mock.patch(f"{_MOD}.get_container_state", return_value="exited"),
            unittest.mock.patch(f"{_MOD}.container_start", return_value=start_result) as mock_start,
            unittest.mock.patch(f"{_MOD}.sandbox_exec", return_value=exec_result) as mock_exec,
            unittest.mock.patch(f"{_MOD}.container_stop", return_value=stop_result) as mock_stop,
        ):
            result = container_git_diff("proj", "2", "cli", "--stat", "HEAD@{1}..HEAD")
            assert result == expected

            mock_start.assert_called_once_with("proj-cli-2")
            mock_exec.assert_called_once_with(
                "proj-cli-2",
                ["git", "-C", "/workspace", "diff", "--stat", "HEAD@{1}..HEAD"],
                timeout=30,
            )
            mock_stop.assert_called_once_with("proj-cli-2", timeout=10)

    def test_exited_headless_container_not_restarted(self) -> None:
        """Exited headless (run mode) containers must not be restarted."""
        with unittest.mock.patch(f"{_MOD}.get_container_state", return_value="exited"):
            result = container_git_diff("proj", "1", "run", "--stat", "HEAD@{1}..HEAD")
            assert result is None

    def test_no_container_returns_none(self) -> None:
        """Return None when the container does not exist."""
        with unittest.mock.patch(f"{_MOD}.get_container_state", return_value=None):
            result = container_git_diff("proj", "99", "cli")
            assert result is None

    def test_podman_exec_failure_returns_none(self) -> None:
        """Return None when git diff fails inside the container."""
        exec_result = ExecResult(exit_code=128, stdout="", stderr="")
        with (
            unittest.mock.patch(f"{_MOD}.get_container_state", return_value="running"),
            unittest.mock.patch(f"{_MOD}.sandbox_exec", return_value=exec_result),
        ):
            result = container_git_diff("proj", "1", "cli", "HEAD")
            assert result is None

    def test_start_failure_returns_none(self) -> None:
        """Return None when container start fails on a stopped container."""
        start_result = subprocess.CompletedProcess(args=[], returncode=125, stderr="Error")
        with (
            unittest.mock.patch(f"{_MOD}.get_container_state", return_value="exited"),
            unittest.mock.patch(f"{_MOD}.container_start", return_value=start_result),
        ):
            # "run" mode refuses to restart, so use "cli" to hit the start path
            result = container_git_diff("proj", "1", "cli")
            assert result is None

    def test_timeout_returns_none(self) -> None:
        """Return None on sandbox_exec timeout."""
        with (
            unittest.mock.patch(f"{_MOD}.get_container_state", return_value="running"),
            unittest.mock.patch(
                f"{_MOD}.sandbox_exec",
                side_effect=subprocess.TimeoutExpired("podman", 30),
            ),
        ):
            result = container_git_diff("proj", "1", "cli", "HEAD")
            assert result is None
