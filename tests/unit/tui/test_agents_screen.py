# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the reusable [`AgentsSelectScreen`][terok.tui.agents_screen.AgentsSelectScreen].

The screen is the single source of agent-multi-select UX — used by the
new-project wizard's "Override agents…" button, the per-project setter
on Project Details, and the global-default entry on the command
palette.  These tests pin the dismissal contract and the master /
item cascade so all three callers can rely on the same semantics.
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Checkbox, Label

from terok.tui.agents_screen import AgentsSelectScreen

_SENTINEL_PENDING = object()


class _Host(App):
    """Minimal test host that pushes a screen and captures its dismissal value."""

    def __init__(self, screen: AgentsSelectScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result: object = _SENTINEL_PENDING

    def on_mount(self) -> None:
        self.push_screen(self._screen, self._capture)

    def _capture(self, result: object) -> None:
        self.result = result


def _agent_slugs() -> list[str]:
    """Roster slugs in render order — drives data-driven assertions.

    Skips the caller when the roster is empty (sandboxed CI without
    bundled agents), so every ``slugs[0]`` indexing below stays safe.
    """
    from terok.lib.integrations.executor import AgentRoster

    slugs = list(AgentRoster.shared().agent_names)
    if not slugs:
        pytest.skip("Empty agent roster — needs at least one installed agent")
    return slugs


def _master(screen: AgentsSelectScreen) -> Checkbox:
    return screen.query_one("#agents-select-all", Checkbox)


def _item(screen: AgentsSelectScreen, slug: str) -> Checkbox:
    return screen.query_one(f"#agents-select-item-{slug}", Checkbox)


@pytest.mark.asyncio
async def test_default_initial_is_master_on() -> None:
    """No initial → master checked, every item cascade-checked."""
    app = _Host(AgentsSelectScreen())
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, AgentsSelectScreen)
        assert _master(screen).value is True
        for slug in _agent_slugs():
            assert _item(screen, slug).value is True


@pytest.mark.asyncio
async def test_initial_all_keyword_seeds_master_on() -> None:
    """``initial='all'`` is treated identically to empty input."""
    app = _Host(AgentsSelectScreen(initial="all"))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert _master(app.screen).value is True


@pytest.mark.asyncio
async def test_initial_comma_list_seeds_named_items_only() -> None:
    """``initial='claude'`` → master off, only ``claude`` checked."""
    slugs = _agent_slugs()
    pick = slugs[0]
    app = _Host(AgentsSelectScreen(initial=pick))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert _master(screen).value is False
        assert _item(screen, pick).value is True
        for other in slugs[1:]:
            assert _item(screen, other).value is False


@pytest.mark.asyncio
async def test_initial_all_with_exclude_seeds_everything_except_excluded() -> None:
    """``initial='all,-<slug>'`` → master off, every item except the excluded checked."""
    slugs = _agent_slugs()
    omit = slugs[0]
    app = _Host(AgentsSelectScreen(initial=f"all,-{omit}"))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert _master(screen).value is False
        assert _item(screen, omit).value is False
        for kept in slugs[1:]:
            assert _item(screen, kept).value is True


@pytest.mark.asyncio
async def test_master_cascade_checks_all_items() -> None:
    """Toggling master on cascades to every item; toggling off cascades back."""
    app = _Host(AgentsSelectScreen(initial=_agent_slugs()[0]))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        _master(screen).value = True
        await pilot.pause()
        for slug in _agent_slugs():
            assert _item(screen, slug).value is True


@pytest.mark.asyncio
async def test_unchecking_item_flips_master_off() -> None:
    """Removing one agent with master on means the snapshot diverges from 'all'."""
    app = _Host(AgentsSelectScreen())
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        target = _agent_slugs()[0]
        _item(screen, target).value = False
        await pilot.pause()
        assert _master(screen).value is False


@pytest.mark.asyncio
async def test_cancel_dismisses_with_none() -> None:
    """Cancel button returns ``None`` — caller treats as no change."""
    app = _Host(AgentsSelectScreen())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#agents-select-cancel")
        await pilot.pause()
    assert app.result is None


@pytest.mark.asyncio
async def test_save_with_master_emits_all() -> None:
    """Save with master on returns the literal ``'all'`` token."""
    app = _Host(AgentsSelectScreen())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#agents-select-save")
        await pilot.pause()
    assert app.result == "all"


@pytest.mark.asyncio
async def test_save_with_explicit_subset_emits_comma_list() -> None:
    """Master off + two items on → submission is a comma list, not 'all'."""
    slugs = _agent_slugs()
    if len(slugs) < 2:
        pytest.skip("Need at least 2 agents in roster")
    app = _Host(AgentsSelectScreen(initial=f"{slugs[0]},{slugs[1]}"))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#agents-select-save")
        await pilot.pause()
    assert app.result == f"{slugs[0]},{slugs[1]}"


@pytest.mark.asyncio
async def test_save_with_nothing_selected_shows_error_and_stays_open() -> None:
    """Empty selection refuses to dismiss — caller can't write ambiguous '' value."""
    app = _Host(AgentsSelectScreen())
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, AgentsSelectScreen)
        # Turn off master → cascades all items off.
        _master(screen).value = False
        await pilot.pause()
        await pilot.click("#agents-select-save")
        await pilot.pause()
        error = screen.query_one("#agents-select-error", Label)
        assert "at least one" in str(error.render()).lower()
    # Still showing the modal — no dismissal happened.
    assert app.result is _SENTINEL_PENDING


@pytest.mark.asyncio
async def test_title_is_rendered_on_border() -> None:
    """Custom title shows on the dialog border for caller-distinguishable framing."""
    app = _Host(AgentsSelectScreen(title="Agents for my-proj"))
    async with app.run_test() as pilot:
        await pilot.pause()
        dialog = app.screen.query_one("#agents-select-dialog")
        assert dialog.border_title == "Agents for my-proj"
