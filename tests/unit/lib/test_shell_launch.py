# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for shell launch helpers used by the TUI."""

from __future__ import annotations

import shlex
import subprocess
import unittest.mock
from collections.abc import Callable

import pytest

from terok.tui.shell_launch import (
    is_inside_gnome_terminal,
    is_inside_konsole,
    is_inside_ptyxis,
    is_inside_tmux,
    launch_login,
    spawn_terminal_with_command,
    tmux_new_window,
)
from terok.tui.tmux_session import TMUX_TIMEOUT_S
from tests.testfs import FAKE_TMUX_SOCKET

SHELL_COMMAND = ["podman", "exec", "-it", "c1", "bash"]


def _shell_payload(command: list[str]) -> str:
    """Return the shell-quoted payload string used by launcher helpers."""
    return " ".join(shlex.quote(part) for part in command)


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
            (is_inside_ptyxis, terminal_env("ptyxis"), False, True),
            (is_inside_ptyxis, terminal_env("gnome-terminal"), False, False),
            (is_inside_ptyxis, {}, False, False),
            (is_inside_ptyxis, {}, True, True),
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
            "ptyxis-by-env",
            "ptyxis-other-terminal",
            "ptyxis-missing-env",
            "ptyxis-parent-fallback",
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
            (subprocess.CompletedProcess(args=[], returncode=0, stdout="@7\n"), True),
            (subprocess.CalledProcessError(1, "tmux"), False),
            (FileNotFoundError("tmux"), False),
            (subprocess.TimeoutExpired("tmux", TMUX_TIMEOUT_S), False),
        ],
        ids=["success", "failure", "tmux-not-found", "hung-server"],
    )
    def test_tmux_new_window(self, side_effect: object, expected: bool) -> None:
        expected_argv = [
            "tmux",
            "new-window",
            "-P",
            "-F",
            "#{window_id}",
            "-n",
            "login:c1",
            _shell_payload(SHELL_COMMAND),
        ]
        with unittest.mock.patch("terok.tui.shell_launch.subprocess.run") as mock_run:
            if isinstance(side_effect, Exception):
                mock_run.side_effect = side_effect
            else:
                mock_run.return_value = side_effect
            result = tmux_new_window(SHELL_COMMAND, title="login:c1")
        assert result is expected
        mock_run.assert_called_once_with(
            expected_argv, check=True, capture_output=True, text=True, timeout=TMUX_TIMEOUT_S
        )

    @pytest.mark.parametrize(
        ("stamp", "stdout", "expected_stamps"),
        [
            ("terok-p1-t1", "@7\n", [("@7", "terok-p1-t1")]),
            ("terok-p1-t1", "", []),
            (None, "@7\n", []),
        ],
        ids=["stamps-new-window", "no-window-id-no-stamp", "no-stamp-requested"],
    )
    def test_tmux_new_window_stamping(
        self, stamp: str | None, stdout: str, expected_stamps: list[tuple[str, str]]
    ) -> None:
        stamped: list[tuple[str, str]] = []
        with (
            unittest.mock.patch(
                "terok.tui.shell_launch.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout),
            ),
            unittest.mock.patch(
                "terok.tui.shell_launch.stamp_login_window",
                side_effect=lambda wid, cname: stamped.append((wid, cname)),
            ),
        ):
            assert tmux_new_window(SHELL_COMMAND, title="login:c1", stamp=stamp)
        assert stamped == expected_stamps


class TestSpawnTerminal:
    """Tests for spawn_terminal_with_command."""

    @pytest.mark.parametrize(
        ("env", "parent_match", "title", "expected_argv"),
        [
            (
                terminal_env("gnome-terminal"),
                False,
                None,
                ["gnome-terminal", "--tab", "--", "bash", "-c", _shell_payload(SHELL_COMMAND)],
            ),
            (
                terminal_env("gnome-terminal"),
                False,
                "login:c1",
                [
                    "gnome-terminal",
                    "--tab",
                    "--title",
                    "login:c1",
                    "--",
                    "bash",
                    "-c",
                    _shell_payload(SHELL_COMMAND),
                ],
            ),
            (
                terminal_env("konsole"),
                False,
                None,
                ["konsole", "--new-tab", "-e", "bash", "-c", _shell_payload(SHELL_COMMAND)],
            ),
            (
                terminal_env("konsole"),
                False,
                "login:c1",
                [
                    "konsole",
                    "--new-tab",
                    "--title",
                    "login:c1",
                    "-e",
                    "bash",
                    "-c",
                    _shell_payload(SHELL_COMMAND),
                ],
            ),
            (
                terminal_env("ptyxis"),
                False,
                None,
                ["ptyxis", "--tab", "--", "bash", "-c", _shell_payload(SHELL_COMMAND)],
            ),
            (
                terminal_env("ptyxis"),
                False,
                "login:c1",
                [
                    "ptyxis",
                    "--tab",
                    "--title",
                    "login:c1",
                    "--",
                    "bash",
                    "-c",
                    _shell_payload(SHELL_COMMAND),
                ],
            ),
        ],
        ids=[
            "gnome",
            "gnome-with-title",
            "konsole",
            "konsole-with-title",
            "ptyxis",
            "ptyxis-with-title",
        ],
    )
    def test_spawn_terminal_with_supported_terminal(
        self,
        env: dict[str, str],
        parent_match: bool,
        title: str | None,
        expected_argv: list[str],
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
        mock_popen.assert_called_once_with(expected_argv, start_new_session=True)

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
            unittest.mock.patch("terok.tui.shell_launch.subprocess.Popen") as mock_popen,
        ):
            assert not spawn_terminal_with_command(["echo", "hello"])
        mock_popen.assert_not_called()

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
    """Tests for the launch_login orchestrator.

    ``launch_login`` is only reached from a local-terminal TUI — the
    web-served case is refused upstream (issue #473) — so it picks
    between tmux, a desktop terminal, or "none" (caller suspends).
    """

    @pytest.mark.parametrize(
        ("patches", "expected"),
        [
            (
                {
                    "is_inside_tmux": True,
                    "spawn_terminal_with_command": False,
                    "tmux_new_window": True,
                },
                ("tmux", None),
            ),
            (
                {
                    "is_inside_tmux": False,
                    "spawn_terminal_with_command": True,
                    "tmux_new_window": False,
                },
                ("terminal", None),
            ),
            (
                {
                    "is_inside_tmux": True,
                    "spawn_terminal_with_command": True,
                    "tmux_new_window": False,
                },
                ("terminal", None),
            ),
            (
                {
                    "is_inside_tmux": False,
                    "spawn_terminal_with_command": False,
                    "tmux_new_window": False,
                },
                ("none", None),
            ),
        ],
        ids=[
            "prefers-tmux",
            "falls-back-to-terminal",
            "falls-back-after-tmux-failure",
            "returns-none",
        ],
    )
    def test_launch_login(
        self,
        patches: dict[str, object],
        expected: tuple[str, int | None],
    ) -> None:
        with (
            unittest.mock.patch(
                "terok.tui.shell_launch.is_inside_tmux",
                return_value=patches["is_inside_tmux"],
            ) as mock_inside_tmux,
            unittest.mock.patch(
                "terok.tui.shell_launch.spawn_terminal_with_command",
                return_value=patches["spawn_terminal_with_command"],
            ) as mock_spawn_terminal,
            unittest.mock.patch(
                "terok.tui.shell_launch.tmux_new_window",
                return_value=patches["tmux_new_window"],
            ) as mock_tmux,
        ):
            assert launch_login(SHELL_COMMAND, title="login:c1") == expected

        mock_inside_tmux.assert_called_once_with()

        if expected == ("tmux", None):
            mock_tmux.assert_called_once_with(SHELL_COMMAND, title="login:c1", stamp=None)
            mock_spawn_terminal.assert_not_called()
            return

        if patches["is_inside_tmux"]:
            mock_tmux.assert_called_once_with(SHELL_COMMAND, title="login:c1", stamp=None)
        else:
            mock_tmux.assert_not_called()
        mock_spawn_terminal.assert_called_once_with(SHELL_COMMAND, title="login:c1")

    @pytest.mark.parametrize(
        ("existing_window", "select_ok", "expected"),
        [
            ("@7", True, ("tmux-existing", None)),
            ("@7", False, ("tmux", None)),
            (None, True, ("tmux", None)),
        ],
        ids=["switches-to-existing", "creates-when-select-fails", "creates-when-none-open"],
    )
    def test_launch_login_reuses_container_window(
        self,
        existing_window: str | None,
        select_ok: bool,
        expected: tuple[str, int | None],
    ) -> None:
        """A reuse key switches to the container's open window; otherwise a stamped one opens."""
        with (
            unittest.mock.patch("terok.tui.shell_launch.is_inside_tmux", return_value=True),
            unittest.mock.patch(
                "terok.tui.shell_launch.find_login_window",
                return_value=existing_window,
            ) as mock_find,
            unittest.mock.patch(
                "terok.tui.shell_launch.select_window",
                return_value=select_ok,
            ) as mock_select,
            unittest.mock.patch(
                "terok.tui.shell_launch.tmux_new_window",
                return_value=True,
            ) as mock_new,
        ):
            assert launch_login(SHELL_COMMAND, title="login:c1", reuse_key="c1") == expected

        mock_find.assert_called_once_with("c1")
        if existing_window is None:
            mock_select.assert_not_called()
        if expected[0] == "tmux-existing":
            mock_new.assert_not_called()
        else:
            mock_new.assert_called_once_with(SHELL_COMMAND, title="login:c1", stamp="c1")
