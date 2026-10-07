# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The harvest review: what a task reached for, grouped by what the shield did."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock

import pytest
from rich.table import Table

from terok.tui import task_actions as task_actions_mod
from terok.tui.shield_harvest_screen import ShieldHarvestScreen
from terok.tui.task_actions import TaskActionsMixin


@dataclass(frozen=True)
class _Entry:
    """Stand-in for ``terok_shield.HarvestEntry`` — the screen only reads fields."""

    action: str
    target: str
    count: int = 1
    ports: tuple[int, ...] = (443,)
    last_seen: str = "2026-09-30T10:00:00+00:00"


def _render_text(screen: ShieldHarvestScreen) -> str:
    """Render the screen's body to plain text for assertions."""
    from rich.console import Console

    body = screen._body()
    console = Console(width=120, record=True)
    console.print(body)
    return console.export_text()


def test_empty_harvest_says_there_is_nothing_to_promote() -> None:
    """A task that was never refused anything has no candidates, and says so."""
    text = _render_text(ShieldHarvestScreen("proj:1", []))

    assert "Nothing harvested" in text
    assert "nothing to promote" in text


def test_entries_group_by_what_the_shield_did() -> None:
    """Refusals and window accepts are different decisions, so they read apart."""
    screen = ShieldHarvestScreen(
        "proj:1",
        [_Entry("blocked", "refused.test"), _Entry("bypass", "admitted.test")],
    )

    text = _render_text(screen)

    assert "Refused by the policy" in text
    assert "Let through by the timed window" in text
    assert text.index("refused.test") < text.index("admitted.test")


def test_a_verdict_with_no_entries_gets_no_heading() -> None:
    """An empty section would read as a claim that nothing was refused."""
    text = _render_text(ShieldHarvestScreen("proj:1", [_Entry("blocked", "refused.test")]))

    assert "Let through by the timed window" not in text


def test_rows_carry_the_counts_and_ports_a_decision_needs() -> None:
    """Tries and ports are what separate a stray probe from a real dependency."""
    screen = ShieldHarvestScreen("proj:1", [_Entry("blocked", "refused.test", 12, (80, 443))])

    text = _render_text(screen)

    assert "12" in text
    assert "80,443" in text


def test_a_target_without_ports_renders_a_dash() -> None:
    """An empty column would look like a missing value rather than no port."""
    text = _render_text(ShieldHarvestScreen("proj:1", [_Entry("blocked", "refused.test", 1, ())]))

    assert "-" in text


def test_the_body_is_a_table_once_there_are_entries() -> None:
    """The empty state is prose; anything else is tabular."""
    assert isinstance(ShieldHarvestScreen("proj:1", [_Entry("blocked", "x.test")])._body(), Table)


@pytest.mark.asyncio
async def test_action_reads_the_log_through_the_shield_manager() -> None:
    """The review opens on what ``ShieldManager.harvest()`` returned."""
    entries = [_Entry("blocked", "refused.test")]
    stub = SimpleNamespace(
        notify=MagicMock(),
        push_screen=mock.AsyncMock(),
        current_project_name="proj",
        current_task=SimpleNamespace(task_id="1", mode="cli"),
    )

    with (
        mock.patch.object(task_actions_mod, "ShieldManager") as manager,
        mock.patch.object(task_actions_mod, "load_project"),
    ):
        manager.return_value.harvest.return_value = entries
        await TaskActionsMixin._action_shield_harvest(stub)

    screen = stub.push_screen.call_args.args[0]
    assert isinstance(screen, ShieldHarvestScreen)
    assert screen._entries == entries


@pytest.mark.asyncio
async def test_action_reports_an_unreadable_log_instead_of_crashing() -> None:
    """A missing task dir is an answer for the operator, not a traceback."""
    stub = SimpleNamespace(
        notify=MagicMock(),
        push_screen=mock.AsyncMock(),
        current_project_name="proj",
        current_task=SimpleNamespace(task_id="1", mode="cli"),
    )

    with mock.patch.object(task_actions_mod, "load_project", side_effect=OSError("gone")):
        await TaskActionsMixin._action_shield_harvest(stub)

    stub.push_screen.assert_not_called()
    assert "Could not read the audit log" in stub.notify.call_args.args[0]


@pytest.mark.asyncio
async def test_action_needs_a_task() -> None:
    """With nothing selected there is no log to read."""
    stub = SimpleNamespace(
        notify=MagicMock(),
        push_screen=mock.AsyncMock(),
        current_project_name=None,
        current_task=None,
    )

    await TaskActionsMixin._action_shield_harvest(stub)

    stub.push_screen.assert_not_called()
    stub.notify.assert_called_once_with("No task selected.")
