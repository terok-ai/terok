# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``terok-xdg-terminal-exec`` — the Ptyxis-aware launcher shim.

The shim is invoked by the Terok ``.desktop`` launcher when the
install-time gate fires (Ptyxis + ``xdg-terminal-exec`` both on PATH).
It probes ``xdg-terminal-exec --print-id`` and either re-execs into
``ptyxis --new-window`` (Ptyxis is the resolved default) or hands off
transparently to ``xdg-terminal-exec`` (anything else).
"""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from terok.cli import xdg_terminal_exec as shim

from .conftest import which_factory as _which_factory


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    """Build a ``subprocess.run`` return value with the given stdout/exit."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class TestMissingXdgTerminalExec:
    """Without ``xdg-terminal-exec`` the shim has nothing to relay to — exit 127."""

    def test_exits_127_with_actionable_error(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with (
            mock.patch(
                "terok.cli.xdg_terminal_exec.shutil.which",
                side_effect=_which_factory(set()),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            shim.main()
        assert exc_info.value.code == 127
        err = capsys.readouterr().err
        assert "xdg-terminal-exec" in err
        assert "dnf install" in err or "apt install" in err
        assert "tui.desktop_entry: skip" in err


class TestPtyxisDispatch:
    """When ``--print-id`` resolves to Ptyxis, exec ptyxis --new-window."""

    def test_execs_ptyxis_with_new_window(self) -> None:
        argv = ["terok-tui"]
        with (
            mock.patch.object(shim.sys, "argv", ["terok-xdg-terminal-exec", *argv]),
            mock.patch(
                "terok.cli.xdg_terminal_exec.shutil.which",
                side_effect=_which_factory({"xdg-terminal-exec", "ptyxis"}),
            ),
            mock.patch(
                "terok.cli.xdg_terminal_exec.subprocess.run",
                return_value=_completed(stdout="org.gnome.Ptyxis.desktop\n"),
            ),
            mock.patch("terok.cli.xdg_terminal_exec.os.execvp") as execvp,
        ):
            shim.main()
        execvp.assert_called_once_with(
            "/usr/bin/ptyxis",
            ["/usr/bin/ptyxis", "--new-window", "--title", "Terok", "--", "terok-tui"],
        )

    def test_matches_ptyxis_devel_id_prefix(self) -> None:
        """Reverse-DNS variants like ``org.gnome.Ptyxis.Devel`` also count as Ptyxis."""
        with (
            mock.patch.object(shim.sys, "argv", ["terok-xdg-terminal-exec", "terok-tui"]),
            mock.patch(
                "terok.cli.xdg_terminal_exec.shutil.which",
                side_effect=_which_factory({"xdg-terminal-exec", "ptyxis"}),
            ),
            mock.patch(
                "terok.cli.xdg_terminal_exec.subprocess.run",
                return_value=_completed(stdout="org.gnome.Ptyxis.Devel.desktop"),
            ),
            mock.patch("terok.cli.xdg_terminal_exec.os.execvp") as execvp,
        ):
            shim.main()
        called_argv = execvp.call_args[0][1]
        assert "--new-window" in called_argv

    def test_falls_through_when_ptyxis_disappeared_after_print_id(self) -> None:
        """``--print-id`` says Ptyxis but ptyxis itself is missing → relay to xdg-terminal-exec.

        Pathological case (race or broken setup), but the shim should
        never crash the launch with FileNotFoundError.
        """
        with (
            mock.patch.object(shim.sys, "argv", ["terok-xdg-terminal-exec", "terok-tui"]),
            mock.patch(
                "terok.cli.xdg_terminal_exec.shutil.which",
                side_effect=_which_factory({"xdg-terminal-exec"}),
            ),
            mock.patch(
                "terok.cli.xdg_terminal_exec.subprocess.run",
                return_value=_completed(stdout="org.gnome.Ptyxis.desktop"),
            ),
            mock.patch("terok.cli.xdg_terminal_exec.os.execvp") as execvp,
        ):
            shim.main()
        execvp.assert_called_once_with(
            "/usr/bin/xdg-terminal-exec",
            ["/usr/bin/xdg-terminal-exec", "terok-tui"],
        )


class TestPassthrough:
    """Anything that isn't Ptyxis is a transparent ``xdg-terminal-exec`` relay."""

    @pytest.mark.parametrize(
        "stdout",
        ["org.gnome.Terminal.desktop\n", "org.kde.konsole.desktop", "kitty.desktop"],
        ids=["gnome-terminal", "konsole", "kitty"],
    )
    def test_non_ptyxis_id_passes_through(self, stdout: str) -> None:
        with (
            mock.patch.object(shim.sys, "argv", ["terok-xdg-terminal-exec", "terok-tui"]),
            mock.patch(
                "terok.cli.xdg_terminal_exec.shutil.which",
                side_effect=_which_factory({"xdg-terminal-exec", "ptyxis"}),
            ),
            mock.patch(
                "terok.cli.xdg_terminal_exec.subprocess.run",
                return_value=_completed(stdout=stdout),
            ),
            mock.patch("terok.cli.xdg_terminal_exec.os.execvp") as execvp,
        ):
            shim.main()
        execvp.assert_called_once_with(
            "/usr/bin/xdg-terminal-exec",
            ["/usr/bin/xdg-terminal-exec", "terok-tui"],
        )

    @pytest.mark.parametrize(
        ("side_effect", "id_str"),
        [
            (None, ""),  # empty stdout — no terminal resolved
            (None, "org.gnome.Terminal.desktop"),  # fine id, but exit 1
            (subprocess.TimeoutExpired(cmd="x", timeout=5), ""),
            (OSError("exec format error"), ""),
        ],
        ids=["empty-stdout", "non-zero-exit", "timeout", "os-error"],
    )
    def test_print_id_failure_falls_through(
        self,
        side_effect: BaseException | None,
        id_str: str,
    ) -> None:
        """Older xdg-terminal-exec without ``--print-id`` (or any probe failure) → passthrough."""
        run_kwargs: dict[str, object] = {}
        if side_effect is not None:
            run_kwargs["side_effect"] = side_effect
        else:
            # exit 1 to match "non-zero-exit" id; empty-stdout uses exit 0
            rc = 0 if id_str == "" else 1
            run_kwargs["return_value"] = _completed(stdout=id_str, returncode=rc)

        with (
            mock.patch.object(shim.sys, "argv", ["terok-xdg-terminal-exec", "terok-tui", "--foo"]),
            mock.patch(
                "terok.cli.xdg_terminal_exec.shutil.which",
                side_effect=_which_factory({"xdg-terminal-exec", "ptyxis"}),
            ),
            mock.patch("terok.cli.xdg_terminal_exec.subprocess.run", **run_kwargs),
            mock.patch("terok.cli.xdg_terminal_exec.os.execvp") as execvp,
        ):
            shim.main()
        execvp.assert_called_once_with(
            "/usr/bin/xdg-terminal-exec",
            ["/usr/bin/xdg-terminal-exec", "terok-tui", "--foo"],
        )

    def test_passes_all_arguments_through(self) -> None:
        """The shim is argv-transparent — every positional makes it across."""
        argv = ["terok-tui", "--debug", "--", "extra arg with spaces"]
        with (
            mock.patch.object(shim.sys, "argv", ["terok-xdg-terminal-exec", *argv]),
            mock.patch(
                "terok.cli.xdg_terminal_exec.shutil.which",
                side_effect=_which_factory({"xdg-terminal-exec"}),
            ),
            mock.patch(
                "terok.cli.xdg_terminal_exec.subprocess.run",
                return_value=_completed(stdout="org.gnome.Terminal.desktop"),
            ),
            mock.patch("terok.cli.xdg_terminal_exec.os.execvp") as execvp,
        ):
            shim.main()
        called_argv = execvp.call_args[0][1]
        assert called_argv[1:] == argv
