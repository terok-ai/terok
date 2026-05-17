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
from unittest.mock import AsyncMock, MagicMock

import pytest

from terok.lib.integrations.sandbox import SetupVerdict
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
    result = await TerokTUI._run_setup_flow(stub, SetupVerdict.OK)
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
    await TerokTUI._run_setup_flow(stub, SetupVerdict.OK, force=True)
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
    result = await TerokTUI._run_setup_flow(stub, SetupVerdict.OK, force=True)
    assert result is True
    stub._run_setup_subprocess.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_ok_verdict_shows_dialog_without_force() -> None:
    """Non-OK verdicts always show the dialog — force is irrelevant."""
    stub = SimpleNamespace(
        push_screen_wait=AsyncMock(return_value=SetupOutcome.SKIPPED),
        notify=MagicMock(),
    )
    await TerokTUI._run_setup_flow(stub, SetupVerdict.FIRST_RUN)
    stub.push_screen_wait.assert_awaited_once()
