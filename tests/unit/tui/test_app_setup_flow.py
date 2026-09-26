# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for [`TerokTUI._run_setup_flow`][terok.tui.app] OK-verdict gating.

Pins the difference between the two callers:

- The auto-first-run flow uses ``force=False`` (default) so an OK verdict
  short-circuits — a healthy install isn't nagged with a useless dialog.
- The command-palette action ``Run terok setup`` uses ``force=True`` so
  the dialog is shown even when the verdict is OK — the user explicitly
  asked for the (idempotent) re-run.

The bug this fixes: without ``force``, palette clicks on a healthy install
silently no-op'd and no log entry was created — looked like the action
hadn't fired at all.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from terok_util import SetupStatus

from terok.tui.app import TerokTUI
from terok.tui.setup_screen import SetupOutcome, SetupScreen


@pytest.mark.asyncio
async def test_ok_verdict_skips_dialog_without_force() -> None:
    """Auto-first-run: OK verdict ⇒ no dialog, no subprocess, returns True."""
    stub = SimpleNamespace(
        push_screen_wait=AsyncMock(),
        _run_setup_subprocess=AsyncMock(),
        notify=MagicMock(),
    )
    result = await TerokTUI._run_setup_flow(stub, SetupStatus.READY)
    assert result is True
    stub.push_screen_wait.assert_not_called()
    stub._run_setup_subprocess.assert_not_called()


@pytest.mark.asyncio
async def test_ok_verdict_shows_dialog_when_forced() -> None:
    """Palette path: OK + force=True ⇒ dialog is shown.

    The user explicitly invoked ``Run terok setup`` from the palette;
    short-circuiting on OK would silently swallow their request (the
    original bug).  Outcome=SKIPPED here means the user then bailed
    — but the *dialog appeared*, which is the contract this test pins.
    """
    stub = SimpleNamespace(
        push_screen_wait=AsyncMock(return_value=SetupOutcome.SKIPPED),
        notify=MagicMock(),
    )
    await TerokTUI._run_setup_flow(stub, SetupStatus.READY, force=True)
    stub.push_screen_wait.assert_awaited_once()
    pushed = stub.push_screen_wait.await_args.args[0]
    assert isinstance(pushed, SetupScreen)


@pytest.mark.asyncio
async def test_ok_verdict_forced_dispatch_runs_subprocess() -> None:
    """Palette + Run-clicked: dialog returns SHOULD_RUN ⇒ subprocess dispatched."""
    stub = SimpleNamespace(
        push_screen_wait=AsyncMock(return_value=SetupOutcome.SHOULD_RUN),
        _run_setup_subprocess=AsyncMock(return_value=True),
        notify=MagicMock(),
    )
    result = await TerokTUI._run_setup_flow(stub, SetupStatus.READY, force=True)
    assert result is True
    stub._run_setup_subprocess.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_ok_verdict_shows_dialog_without_force() -> None:
    """Non-OK verdicts always show the dialog — force is irrelevant."""
    stub = SimpleNamespace(
        push_screen_wait=AsyncMock(return_value=SetupOutcome.SKIPPED),
        notify=MagicMock(),
    )
    await TerokTUI._run_setup_flow(stub, SetupStatus.MISSING)
    stub.push_screen_wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_unexpected_probe_error_cannot_hide_setup_warning() -> None:
    """Broken readiness probes are invalid, never silently interpreted as ready."""
    stub = SimpleNamespace(
        _projects_by_id={"existing": object()},
        _broken_by_id={},
        _first_run_dismissed=True,
        _save_selection_state=MagicMock(),
        _run_first_run_flow=MagicMock(),
    )
    with patch("terok.tui.app.check_setup", side_effect=OSError("unreadable")):
        await TerokTUI._maybe_show_first_run_flow(stub)
    stub._run_first_run_flow.assert_called_once_with(
        verdict=SetupStatus.INVALID, empty_install=False
    )


def test_setup_screen_probe_error_is_invalid() -> None:
    """A directly opened setup screen also refuses to treat probe errors as success."""
    with patch("terok.tui.setup_screen.check_setup", side_effect=OSError("unreadable")):
        assert SetupScreen()._verdict is SetupStatus.INVALID


def test_downgrade_screen_has_no_override_action() -> None:
    """The UI offers no receipt deletion or forced setup escape hatch."""
    buttons = list(SetupScreen._buttons_for(SetupStatus.DOWNGRADE))
    assert [button.id for button in buttons] == ["setup-refused", "setup-close"]
    assert buttons[0].disabled
    assert "Downgrades are not supported" in SetupScreen._blurb_for(SetupStatus.DOWNGRADE)
