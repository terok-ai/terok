# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for task lifecycle hooks."""

from __future__ import annotations

import subprocess
import unittest.mock
from pathlib import Path

import pytest

from terok.lib.containers.hooks import _build_hook_env, run_hook


class TestBuildHookEnv:
    """Tests for _build_hook_env helper."""

    def test_basic_env(self) -> None:
        """Verify core environment variables are set."""
        env = _build_hook_env("proj", "1", "toad", "proj-toad-1", "post_ready")
        assert env["TEROK_HOOK"] == "post_ready"
        assert env["TEROK_PROJECT_ID"] == "proj"
        assert env["TEROK_TASK_ID"] == "1"
        assert env["TEROK_TASK_MODE"] == "toad"
        assert env["TEROK_CONTAINER_NAME"] == "proj-toad-1"
        assert "TEROK_WEB_PORT" not in env

    def test_with_web_port(self) -> None:
        """Verify TEROK_WEB_PORT is set when web_port is given."""
        env = _build_hook_env("p", "2", "toad", "c", "post_ready", web_port=7861)
        assert env["TEROK_WEB_PORT"] == "7861"

    def test_with_task_dir(self, tmp_path: Path) -> None:
        """Verify TEROK_TASK_DIR is set when task_dir is given."""
        env = _build_hook_env("p", "3", "cli", "c", "post_start", task_dir=tmp_path)
        assert env["TEROK_TASK_DIR"] == str(tmp_path)

    def test_inherits_os_environ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify the hook env inherits the host process environment."""
        monkeypatch.setenv("MY_CUSTOM_VAR", "hello")
        env = _build_hook_env("p", "1", "cli", "c", "post_start")
        assert env["MY_CUSTOM_VAR"] == "hello"


class TestRunHook:
    """Tests for run_hook execution."""

    def test_none_command_is_noop(self) -> None:
        """A None command should be a silent no-op."""
        run_hook(
            "post_start", None,
            project_id="p", task_id="1", mode="cli", cname="c",
        )

    def test_empty_string_is_noop(self) -> None:
        """An empty string command should be a silent no-op."""
        run_hook(
            "post_start", "",
            project_id="p", task_id="1", mode="cli", cname="c",
        )

    def test_command_is_executed(self) -> None:
        """Verify a hook command is executed via sh -c with correct env."""
        with unittest.mock.patch("terok.lib.containers.hooks.subprocess.run") as mock_run:
            run_hook(
                "post_start", "echo hello",
                project_id="proj", task_id="1", mode="cli", cname="proj-cli-1",
            )

            mock_run.assert_called_once()
            args = mock_run.call_args
            assert args[0][0] == ["sh", "-c", "echo hello"]
            env = args[1]["env"]
            assert env["TEROK_HOOK"] == "post_start"
            assert env["TEROK_PROJECT_ID"] == "proj"

    def test_pre_stop_has_timeout(self) -> None:
        """Verify pre_stop hooks have a 30s timeout."""
        with unittest.mock.patch("terok.lib.containers.hooks.subprocess.run") as mock_run:
            run_hook(
                "pre_stop", "cleanup.sh",
                project_id="p", task_id="1", mode="cli", cname="c",
            )
            assert mock_run.call_args[1]["timeout"] == 30

    def test_post_start_no_timeout(self) -> None:
        """Verify post_start hooks have no timeout."""
        with unittest.mock.patch("terok.lib.containers.hooks.subprocess.run") as mock_run:
            run_hook(
                "post_start", "setup.sh",
                project_id="p", task_id="1", mode="cli", cname="c",
            )
            assert mock_run.call_args[1]["timeout"] is None

    def test_web_port_passed_to_env(self) -> None:
        """Verify web_port is forwarded to the hook environment."""
        with unittest.mock.patch("terok.lib.containers.hooks.subprocess.run") as mock_run:
            run_hook(
                "post_ready", "fwd.sh",
                project_id="p", task_id="1", mode="toad", cname="c",
                web_port=7861,
            )
            env = mock_run.call_args[1]["env"]
            assert env["TEROK_WEB_PORT"] == "7861"

    def test_failure_does_not_raise(self) -> None:
        """Verify hook failures are swallowed (logged, not raised)."""
        with unittest.mock.patch(
            "terok.lib.containers.hooks.subprocess.run",
            side_effect=OSError("boom"),
        ):
            run_hook(
                "post_start", "fail.sh",
                project_id="p", task_id="1", mode="cli", cname="c",
            )

    def test_timeout_does_not_raise(self) -> None:
        """Verify hook timeouts are swallowed (logged, not raised)."""
        with unittest.mock.patch(
            "terok.lib.containers.hooks.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30),
        ):
            run_hook(
                "pre_stop", "slow.sh",
                project_id="p", task_id="1", mode="cli", cname="c",
            )
