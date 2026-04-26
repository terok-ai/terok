# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``terok selinux`` CLI subcommand group."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from terok.cli.commands import selinux


def _ns(selinux_cmd: str | None = "setup") -> argparse.Namespace:
    """Build a Namespace as the dispatcher would receive."""
    return argparse.Namespace(cmd="selinux", selinux_cmd=selinux_cmd)


def test_dispatch_returns_false_for_other_top_level_cmds() -> None:
    """Unrelated top-level commands fall through to the next dispatcher."""
    assert selinux.dispatch(argparse.Namespace(cmd="not-selinux")) is False


def test_dispatch_returns_false_for_unknown_subcommand() -> None:
    """An unknown subcommand falls through (argparse already rejects most cases)."""
    assert selinux.dispatch(_ns(selinux_cmd="other")) is False


def test_setup_invokes_install_script_via_sudo(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """``terok selinux setup`` execs ``sudo bash <script>`` and prints a confirmation."""
    fake_script = tmp_path / "install.sh"
    fake_script.write_text("#!/bin/bash\nexit 0\n")

    completed = MagicMock(spec=subprocess.CompletedProcess)
    completed.returncode = 0

    with (
        patch("terok_sandbox.selinux_install_script", return_value=fake_script),
        patch("subprocess.run", return_value=completed) as run_mock,
    ):
        assert selinux.dispatch(_ns()) is True

    run_mock.assert_called_once_with(
        ["sudo", "bash", str(fake_script)],
        check=True,
    )
    out = capsys.readouterr().out
    assert "SELinux policy installed" in out
    assert "terok setup" in out


def test_setup_skips_when_install_script_missing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A non-existent script path is a clean no-op with a stderr note."""
    missing = tmp_path / "absent.sh"
    with (
        patch("terok_sandbox.selinux_install_script", return_value=missing),
        patch("subprocess.run") as run_mock,
    ):
        assert selinux.dispatch(_ns()) is True
    run_mock.assert_not_called()
    err = capsys.readouterr().err
    assert "not found" in err
    assert "nothing to do" in err


def test_setup_propagates_install_failure_as_systemexit(tmp_path: Path) -> None:
    """A non-zero exit from the install script surfaces as ``SystemExit`` with the code."""
    fake_script = tmp_path / "install.sh"
    fake_script.write_text("#!/bin/bash\nexit 1\n")

    failed = subprocess.CalledProcessError(returncode=2, cmd=["sudo"])

    with (
        patch("terok_sandbox.selinux_install_script", return_value=fake_script),
        patch("subprocess.run", side_effect=failed),
        pytest.raises(SystemExit) as exc,
    ):
        selinux.dispatch(_ns())

    assert "exit 2" in str(exc.value)


def test_register_adds_subparser_with_setup_subcommand() -> None:
    """``register`` adds the ``selinux setup`` path; bare ``selinux`` requires a subcommand."""
    parser = argparse.ArgumentParser()
    selinux.register(parser.add_subparsers(dest="cmd"))
    parsed = parser.parse_args(["selinux", "setup"])
    assert parsed.cmd == "selinux"
    assert parsed.selinux_cmd == "setup"
