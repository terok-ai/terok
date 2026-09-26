# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Structured downward setup checks retain the CLI's exit 3/4 contract."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from terok_util import SetupCheck, SetupStatus

from terok.cli.commands.task import _setup_verdict_or_exit


@pytest.mark.parametrize("status", [SetupStatus.MISSING, SetupStatus.STALE, SetupStatus.INVALID])
def test_setup_needed_checks_exit_three(
    status: SetupStatus, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each repairable status carries the owner's diagnostic and canonical setup command."""
    checks = [SetupCheck("child", "hooks", status, "hooks need repair")]
    with patch("terok.lib.api.setup.check_setup", return_value=checks):
        with pytest.raises(SystemExit) as exc:
            _setup_verdict_or_exit()
    assert exc.value.code == 3
    err = capsys.readouterr().err
    assert "child/hooks: hooks need repair" in err
    assert "terok setup" in err


def test_downgrade_exits_four_without_override_hint(capsys: pytest.CaptureFixture[str]) -> None:
    """A lower owner's downgrade wins over missing upper setup, without inspecting its file."""
    checks = [
        SetupCheck("terok", "receipt", SetupStatus.MISSING),
        SetupCheck("child", "receipt", SetupStatus.DOWNGRADE, "newer child 1.0; running 0.9"),
    ]
    with patch("terok.lib.api.setup.check_setup", return_value=checks):
        with pytest.raises(SystemExit) as exc:
            _setup_verdict_or_exit()
    assert exc.value.code == 4
    err = capsys.readouterr().err
    assert "newer child 1.0; running 0.9" in err
    assert "Downgrades are not supported" in err
    assert "rm " not in err
    assert "stamp" not in err


def test_ready_checks_return_silently(capsys: pytest.CaptureFixture[str]) -> None:
    """A ready closure permits the task operation without output."""
    checks = [SetupCheck("terok", "receipt", SetupStatus.READY)]
    with patch("terok.lib.api.setup.check_setup", return_value=checks) as check:
        assert _setup_verdict_or_exit() is None
    check.assert_called_once_with(live=True)
    assert capsys.readouterr().err == ""
