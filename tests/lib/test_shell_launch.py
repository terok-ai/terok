# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for shell launch helpers used by the TUI."""

from __future__ import annotations

import subprocess
import unittest.mock
from collections.abc import Callable

import pytest

from terok.tui.shell_launch import (
    is_inside_gnome_terminal,
    is_inside_konsole,
    is_inside_tmux,
    launch_login,
    spawn_terminal_with_command,
    tmux_new_window,
)
from testfs import FAKE_TMUX_SOCKET

SHELL_COMMAND = ["podman", "exec", "-it", "c1", "bash"]


def terminal_env(
    term_program: str | None = None, *, tmux: bool = False, gnome_service: bool = False
) -> dict[str, str]:
    """Build a minimal environment dict for shell-launch tests."""
    env: dict[str, str] = {}
    if term_program is not None:
        env["TERM_PROGRAM"] = term_program
    if tmux:
        env["TMUX"] = str(FAKE_TMUX_SOCKET)
    if gnome_service:
        env["GNOME_TERMINAL_SERVICE"] = "1"
    return env


class TestTerminalDetection:
    """Tests for terminal environment detection."""

    @pytest.mark.parametrize(
        ("env", "expected"),
        [(terminal_env(tmux=True), True), ({}, False)],
        ids=["inside-tmux", "not-in-tmux"],
    )
    def test_is_inside_tmux(self, env: dict[str, str], expected: bool) -> None:
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            assert is_inside_tmux() is expected

    @pytest.mark.parametrize(
        ("detector", "env", "parent_match", "expected"),
        [
            (is_inside_gnome_terminal, terminal_env("gnome-terminal"), False, True),
            (is_inside_gnome_terminal, terminal_env("iTerm.app"), False, False),
            (is_inside_gnome_terminal, {}, False, False),
            (is_inside_gnome_terminal, {}, True, True),
            (is_inside_gnome_terminal, terminal_env(gnome_service=True), False, True),
            (is_inside_konsole, terminal_env("konsole"), False, True),
            (is_inside_konsole, terminal_env("gnome-terminal"), False, False),
            (is_inside_konsole, {}, False, False),
            (is_inside_konsole, {}, True, True),
        ],
        ids=[
            "gnome-by-env",
            "gnome-other-terminal",
            "gnome-missing-env",
            "gnome-parent-fallback",
            "gnome-service",
            "konsole-by-env",
            "konsole-other-terminal",
            "konsole-missing-env",
            "konsole-parent-fallback",
        ],
    )
    def test_terminal_detection(
        self,
        detector: Callable[[], bool],
        env: dict[str, str],
        parent_match: bool,
        expected: bool,
    ) -> None:
        with (
            unittest.mock.patch.dict("os.environ", env, clear=True),
            unittest.mock.patch(
                "terok.tui.shell_launch._parent_process_has_name",
                return_value=parent_match,
            ),
        ):
            assert detector() is expected


class TestTmuxNewWindow:
    """Tests for tmux_new_window."""

    @pytest.mark.parametrize(
        ("side_effect", "expected"),
        [
            (subprocess.CompletedProcess(args=[], returncode=0), True),
            (subprocess.CalledProcessError(1, "tmux"), False),
            (FileNotFoundError("tmux"), False),
        ],
        ids=["success", "failure", "tmux-not-found"],
    )
    def test_tmux_new_window(self, side_effect: object, expected: bool) -> None:
        with unittest.mock.patch("terok.tui.shell_launch.subprocess.run") as mock_run:
            if isinstance(side_effect, Exception):
                mock_run.side_effect = side_effect
            else:
                mock_run.return_value = side_effect
            result = tmux_new_window(SHELL_COMMAND, title="login:c1")
        assert result is expected
        assert mock_run.call_args.kwargs.get("check") is True
        if expected:
            call_args = mock_run.call_args[0][0]
            assert call_args[:2] == ["tmux", "new-window"]
            assert "-n" in call_args
            assert "login:c1" in call_args


class TestSpawnTerminal:
    """Tests for spawn_terminal_with_command."""

    @pytest.mark.parametrize(
        ("env", "parent_match", "title", "expected_binary", "expected_args", "unexpected_args"),
        [
            (
                terminal_env("gnome-terminal"),
                False,
                None,
                "gnome-terminal",
                ["--tab", "--"],
                ["--window", "--title"],
            ),
            (
                terminal_env("gnome-terminal"),
                False,
                "login:c1",
                "gnome-terminal",
                ["--tab", "--", "--title", "login:c1"],
                ["--window"],
            ),
            (
                terminal_env("konsole"),
                False,
                None,
                "konsole",
                ["--new-tab"],
                ["--title"],
            ),
            (
                terminal_env("konsole"),
                False,
                "login:c1",
                "konsole",
                ["--new-tab", "--title", "login:c1"],
                [],
            ),
        ],
        ids=["gnome", "gnome-with-title", "konsole", "konsole-with-title"],
    )
    def test_spawn_terminal_with_supported_terminal(
        self,
        env: dict[str, str],
        parent_match: bool,
        title: str | None,
        expected_binary: str,
        expected_args: list[str],
        unexpected_args: list[str],
    ) -> None:
        with (
            unittest.mock.patch.dict("os.environ", env, clear=True),
            unittest.mock.patch(
                "terok.tui.shell_launch._parent_process_has_name",
                return_value=parent_match,
            ),
            unittest.mock.patch("terok.tui.shell_launch.subprocess.Popen") as mock_popen,
        ):
            result = spawn_terminal_with_command(SHELL_COMMAND, title=title)
        assert result
        call_args = mock_popen.call_args[0][0]
        assert call_args[0] == expected_binary
        for arg in expected_args:
            assert arg in call_args
        for arg in unexpected_args:
            assert arg not in call_args

    @pytest.mark.parametrize(
        ("env", "parent_match"),
        [(terminal_env("iTerm.app"), False), ({}, False)],
        ids=["other-terminal", "no-terminal"],
    )
    def test_spawn_terminal_with_unsupported_terminal(
        self,
        env: dict[str, str],
        parent_match: bool,
    ) -> None:
        with (
            unittest.mock.patch.dict("os.environ", env, clear=True),
            unittest.mock.patch(
                "terok.tui.shell_launch._parent_process_has_name",
                return_value=parent_match,
            ),
        ):
            assert not spawn_terminal_with_command(["echo", "hello"])

    @pytest.mark.parametrize(
        "side_effect",
        [OSError("boom"), FileNotFoundError("gnome-terminal")],
        ids=["os-error", "not-found"],
    )
    def test_spawn_terminal_returns_false_on_popen_error(self, side_effect: Exception) -> None:
        with (
            unittest.mock.patch.dict("os.environ", terminal_env("gnome-terminal"), clear=True),
            unittest.mock.patch(
                "terok.tui.shell_launch._parent_process_has_name",
                return_value=False,
            ),
            unittest.mock.patch("terok.tui.shell_launch.subprocess.Popen") as mock_popen,
        ):
            mock_popen.side_effect = side_effect
            assert not spawn_terminal_with_command(SHELL_COMMAND, title="login:c1")


class TestLaunchLogin:
    """Tests for the launch_login orchestrator."""

    @pytest.mark.parametrize(
        ("patches", "expected", "expected_launcher"),
        [
            (
                {
                    "is_inside_tmux": True,
                    "is_web_mode": False,
                    "spawn_terminal_with_command": False,
                    "tmux_new_window": True,
                    "spawn_ttyd": None,
                },
                ("tmux", None),
                "tmux_new_window",
            ),
            (
                {
                    "is_inside_tmux": False,
                    "is_web_mode": False,
                    "spawn_terminal_with_command": True,
                    "tmux_new_window": False,
                    "spawn_ttyd": None,
                },
                ("terminal", None),
                "spawn_terminal_with_command",
            ),
            (
                {
                    "is_inside_tmux": True,
                    "is_web_mode": False,
                    "spawn_terminal_with_command": True,
                    "tmux_new_window": False,
                    "spawn_ttyd": None,
                },
                ("terminal", None),
                "spawn_terminal_with_command",
            ),
            (
                {
                    "is_inside_tmux": False,
                    "is_web_mode": False,
                    "spawn_terminal_with_command": False,
                    "tmux_new_window": False,
                    "spawn_ttyd": None,
                },
                ("none", None),
                None,
            ),
            (
                {
                    "is_inside_tmux": False,
                    "is_web_mode": True,
                    "spawn_terminal_with_command": False,
                    "tmux_new_window": False,
                    "spawn_ttyd": 12345,
                },
                ("web", 12345),
                "spawn_ttyd",
            ),
            (
                {
                    "is_inside_tmux": False,
                    "is_web_mode": True,
                    "spawn_terminal_with_command": False,
                    "tmux_new_window": False,
                    "spawn_ttyd": None,
                },
                ("none", None),
                None,
            ),
        ],
        ids=[
            "prefers-tmux",
            "falls-back-to-terminal",
            "falls-back-after-tmux-failure",
            "returns-none",
            "web-mode-with-ttyd",
            "web-mode-without-ttyd",
        ],
    )
    def test_launch_login(
        self,
        patches: dict[str, object],
        expected: tuple[str, int | None],
        expected_launcher: str | None,
    ) -> None:
        with (
            unittest.mock.patch(
                "terok.tui.shell_launch.is_inside_tmux",
                return_value=patches["is_inside_tmux"],
            ) as mock_inside_tmux,
            unittest.mock.patch(
                "terok.tui.shell_launch.is_web_mode",
                return_value=patches["is_web_mode"],
            ) as mock_is_web_mode,
            unittest.mock.patch(
                "terok.tui.shell_launch.spawn_terminal_with_command",
                return_value=patches["spawn_terminal_with_command"],
            ) as mock_spawn_terminal,
            unittest.mock.patch(
                "terok.tui.shell_launch.tmux_new_window",
                return_value=patches["tmux_new_window"],
            ) as mock_tmux,
            unittest.mock.patch(
                "terok.tui.shell_launch.spawn_ttyd",
                return_value=patches["spawn_ttyd"],
            ) as mock_spawn_ttyd,
        ):
            assert launch_login(SHELL_COMMAND, title="login:c1") == expected

        mock_inside_tmux.assert_called_once_with()

        if expected_launcher == "tmux_new_window":
            mock_tmux.assert_called_once_with(SHELL_COMMAND, title="login:c1")
            mock_spawn_terminal.assert_not_called()
            mock_spawn_ttyd.assert_not_called()
            return

        if patches["is_inside_tmux"]:
            mock_tmux.assert_called_once_with(SHELL_COMMAND, title="login:c1")
        else:
            mock_tmux.assert_not_called()

        if expected_launcher == "spawn_terminal_with_command":
            mock_spawn_terminal.assert_called_once_with(SHELL_COMMAND, title="login:c1")
            mock_spawn_ttyd.assert_not_called()
        elif expected_launcher == "spawn_ttyd":
            mock_spawn_terminal.assert_not_called()
            mock_spawn_ttyd.assert_called_once_with(SHELL_COMMAND)
        else:
            if patches["is_web_mode"]:
                mock_spawn_terminal.assert_not_called()
                mock_spawn_ttyd.assert_called_once_with(SHELL_COMMAND)
            else:
                mock_spawn_terminal.assert_called_once_with(SHELL_COMMAND, title="login:c1")
                mock_spawn_ttyd.assert_not_called()

        if patches["is_web_mode"]:
            assert mock_is_web_mode.call_count >= 1
