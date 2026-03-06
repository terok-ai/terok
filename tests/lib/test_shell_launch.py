# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

import subprocess
import unittest
import unittest.mock

from terok.tui.shell_launch import (
    is_inside_gnome_terminal,
    is_inside_konsole,
    is_inside_tmux,
    launch_login,
    spawn_terminal_with_command,
    tmux_new_window,
)


class TmuxDetectionTests(unittest.TestCase):
    """Tests for tmux environment detection."""

    def test_is_inside_tmux_true(self) -> None:
        with unittest.mock.patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,12345,0"}):
            self.assertTrue(is_inside_tmux())

    def test_is_inside_tmux_false(self) -> None:
        with unittest.mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(is_inside_tmux())


class GnomeTerminalDetectionTests(unittest.TestCase):
    """Tests for GNOME Terminal environment detection."""

    def test_is_inside_gnome_terminal_true(self) -> None:
        with unittest.mock.patch.dict("os.environ", {"TERM_PROGRAM": "gnome-terminal"}):
            self.assertTrue(is_inside_gnome_terminal())

    def test_is_inside_gnome_terminal_false_other_terminal(self) -> None:
        with (
            unittest.mock.patch.dict("os.environ", {"TERM_PROGRAM": "iTerm.app"}),
            unittest.mock.patch(
                "terok.tui.shell_launch._parent_process_has_name", return_value=False
            ),
        ):
            self.assertFalse(is_inside_gnome_terminal())

    def test_is_inside_gnome_terminal_false_not_set(self) -> None:
        with (
            unittest.mock.patch.dict("os.environ", {}, clear=True),
            unittest.mock.patch(
                "terok.tui.shell_launch._parent_process_has_name", return_value=False
            ),
        ):
            self.assertFalse(is_inside_gnome_terminal())

    def test_is_inside_gnome_terminal_fallback_via_parent_process(self) -> None:
        with (
            unittest.mock.patch.dict("os.environ", {}, clear=True),
            unittest.mock.patch(
                "terok.tui.shell_launch._parent_process_has_name", return_value=True
            ),
        ):
            self.assertTrue(is_inside_gnome_terminal())

    def test_is_inside_gnome_terminal_via_gnome_terminal_service(self) -> None:
        with (
            unittest.mock.patch.dict("os.environ", {"GNOME_TERMINAL_SERVICE": "1"}),
            unittest.mock.patch(
                "terok.tui.shell_launch._parent_process_has_name", return_value=False
            ),
        ):
            self.assertTrue(is_inside_gnome_terminal())


class KonsoleDetectionTests(unittest.TestCase):
    """Tests for Konsole environment detection."""

    def test_is_inside_konsole_true(self) -> None:
        with unittest.mock.patch.dict("os.environ", {"TERM_PROGRAM": "konsole"}):
            self.assertTrue(is_inside_konsole())

    def test_is_inside_konsole_false_other_terminal(self) -> None:
        with (
            unittest.mock.patch.dict("os.environ", {"TERM_PROGRAM": "gnome-terminal"}),
            unittest.mock.patch(
                "terok.tui.shell_launch._parent_process_has_name", return_value=False
            ),
        ):
            self.assertFalse(is_inside_konsole())

    def test_is_inside_konsole_false_not_set(self) -> None:
        with (
            unittest.mock.patch.dict("os.environ", {}, clear=True),
            unittest.mock.patch(
                "terok.tui.shell_launch._parent_process_has_name", return_value=False
            ),
        ):
            self.assertFalse(is_inside_konsole())

    def test_is_inside_konsole_fallback_via_parent_process(self) -> None:
        with (
            unittest.mock.patch.dict("os.environ", {}, clear=True),
            unittest.mock.patch(
                "terok.tui.shell_launch._parent_process_has_name", return_value=True
            ),
        ):
            self.assertTrue(is_inside_konsole())


class TmuxNewWindowTests(unittest.TestCase):
    """Tests for tmux_new_window."""

    def test_success(self) -> None:
        with unittest.mock.patch("terok.tui.shell_launch.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            result = tmux_new_window(["podman", "exec", "-it", "c1", "bash"], title="login:c1")
            self.assertTrue(result)
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            self.assertEqual(call_args[:2], ["tmux", "new-window"])
            self.assertIn("-n", call_args)
            self.assertIn("login:c1", call_args)

    def test_failure(self) -> None:
        with unittest.mock.patch("terok.tui.shell_launch.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "tmux")
            result = tmux_new_window(["podman", "exec", "-it", "c1", "bash"])
            self.assertFalse(result)

    def test_tmux_not_found(self) -> None:
        with unittest.mock.patch("terok.tui.shell_launch.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("tmux")
            result = tmux_new_window(["echo", "hello"])
            self.assertFalse(result)


class SpawnTerminalTests(unittest.TestCase):
    """Tests for spawn_terminal_with_command."""

    def test_gnome_terminal_inside_gnome_terminal(self) -> None:
        with (
            unittest.mock.patch.dict("os.environ", {"TERM_PROGRAM": "gnome-terminal"}),
            unittest.mock.patch("terok.tui.shell_launch.subprocess.Popen") as mock_popen,
        ):
            result = spawn_terminal_with_command(["podman", "exec", "-it", "c1", "bash"])
            self.assertTrue(result)
            mock_popen.assert_called_once()
            call_args = mock_popen.call_args[0][0]
            self.assertEqual(call_args[0], "gnome-terminal")
            self.assertIn("--tab", call_args)
            self.assertNotIn("--window", call_args)
            self.assertIn("--", call_args)

    def test_gnome_terminal_with_title(self) -> None:
        with (
            unittest.mock.patch.dict("os.environ", {"TERM_PROGRAM": "gnome-terminal"}),
            unittest.mock.patch("terok.tui.shell_launch.subprocess.Popen") as mock_popen,
        ):
            result = spawn_terminal_with_command(
                ["podman", "exec", "-it", "c1", "bash"], title="login:c1"
            )
            self.assertTrue(result)
            mock_popen.assert_called_once()
            call_args = mock_popen.call_args[0][0]
            self.assertIn("--title", call_args)
            self.assertIn("login:c1", call_args)

    def test_konsole_inside_konsole(self) -> None:
        with (
            unittest.mock.patch.dict("os.environ", {"TERM_PROGRAM": "konsole"}),
            unittest.mock.patch(
                "terok.tui.shell_launch._parent_process_has_name", return_value=False
            ),
            unittest.mock.patch("terok.tui.shell_launch.subprocess.Popen") as mock_popen,
        ):
            result = spawn_terminal_with_command(["podman", "exec", "-it", "c1", "bash"])
            self.assertTrue(result)
            mock_popen.assert_called_once()
            call_args = mock_popen.call_args[0][0]
            self.assertEqual(call_args[0], "konsole")
            self.assertIn("--new-tab", call_args)

    def test_konsole_title_propagation(self) -> None:
        with (
            unittest.mock.patch.dict("os.environ", {"TERM_PROGRAM": "konsole"}),
            unittest.mock.patch(
                "terok.tui.shell_launch._parent_process_has_name", return_value=False
            ),
            unittest.mock.patch("terok.tui.shell_launch.subprocess.Popen") as mock_popen,
        ):
            result = spawn_terminal_with_command(
                ["podman", "exec", "-it", "c1", "bash"], title="login:c1"
            )
            self.assertTrue(result)
            mock_popen.assert_called_once()
            call_args = mock_popen.call_args[0][0]
            self.assertIn("--new-tab", call_args)
            self.assertIn("--title", call_args)
            self.assertIn("login:c1", call_args)

    def test_not_inside_terminal_returns_false(self) -> None:
        with (
            unittest.mock.patch.dict("os.environ", {}, clear=True),
            unittest.mock.patch(
                "terok.tui.shell_launch._parent_process_has_name", return_value=False
            ),
        ):
            result = spawn_terminal_with_command(["echo", "hello"])
            self.assertFalse(result)

    def test_inside_other_terminal_returns_false(self) -> None:
        with (
            unittest.mock.patch.dict("os.environ", {"TERM_PROGRAM": "iTerm.app"}),
            unittest.mock.patch(
                "terok.tui.shell_launch._parent_process_has_name", return_value=False
            ),
        ):
            result = spawn_terminal_with_command(["echo", "hello"])
            self.assertFalse(result)


class LaunchLoginTests(unittest.TestCase):
    """Tests for the launch_login orchestrator."""

    def test_prefers_tmux(self) -> None:
        """When inside tmux, tmux is preferred."""
        with (
            unittest.mock.patch("terok.tui.shell_launch.is_inside_tmux", return_value=True),
            unittest.mock.patch("terok.tui.shell_launch.tmux_new_window", return_value=True),
        ):
            method, port = launch_login(["podman", "exec", "-it", "c1", "bash"])
            self.assertEqual(method, "tmux")
            self.assertIsNone(port)

    def test_falls_back_to_terminal_when_inside_gnome_terminal(self) -> None:
        """When inside gnome-terminal, spawn a new tab."""
        with (
            unittest.mock.patch("terok.tui.shell_launch.is_inside_tmux", return_value=False),
            unittest.mock.patch("terok.tui.shell_launch.is_web_mode", return_value=False),
            unittest.mock.patch(
                "terok.tui.shell_launch.is_inside_gnome_terminal", return_value=True
            ),
            unittest.mock.patch(
                "terok.tui.shell_launch.spawn_terminal_with_command", return_value=True
            ),
        ):
            method, port = launch_login(["podman", "exec", "-it", "c1", "bash"])
            self.assertEqual(method, "terminal")
            self.assertIsNone(port)

    def test_returns_none_when_not_inside_terminal(self) -> None:
        """When not inside a terminal, fall back to other methods."""
        with (
            unittest.mock.patch("terok.tui.shell_launch.is_inside_tmux", return_value=False),
            unittest.mock.patch("terok.tui.shell_launch.is_web_mode", return_value=False),
            unittest.mock.patch(
                "terok.tui.shell_launch.spawn_terminal_with_command", return_value=False
            ),
        ):
            method, port = launch_login(["podman", "exec", "-it", "c1", "bash"])
            self.assertEqual(method, "none")
            self.assertIsNone(port)

    def test_web_mode_with_ttyd(self) -> None:
        """In web mode with ttyd available, launch_login returns ('web', port)."""
        with (
            unittest.mock.patch("terok.tui.shell_launch.is_inside_tmux", return_value=False),
            unittest.mock.patch("terok.tui.shell_launch.is_web_mode", return_value=True),
            unittest.mock.patch(
                "terok.tui.shell_launch.spawn_terminal_with_command", return_value=False
            ),
            unittest.mock.patch("terok.tui.shell_launch.spawn_ttyd", return_value=12345),
        ):
            method, port = launch_login(["podman", "exec", "-it", "c1", "bash"])
            self.assertEqual(method, "web")
            self.assertEqual(port, 12345)

    def test_web_mode_ttyd_unavailable_falls_back(self) -> None:
        """In web mode without ttyd, launch_login falls back to ('none', None)."""
        with (
            unittest.mock.patch("terok.tui.shell_launch.is_inside_tmux", return_value=False),
            unittest.mock.patch("terok.tui.shell_launch.is_web_mode", return_value=True),
            unittest.mock.patch(
                "terok.tui.shell_launch.spawn_terminal_with_command", return_value=False
            ),
            unittest.mock.patch("terok.tui.shell_launch.spawn_ttyd", return_value=None),
        ):
            method, port = launch_login(["podman", "exec", "-it", "c1", "bash"])
            self.assertEqual(method, "none")
            self.assertIsNone(port)
