# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the [`ShieldSetsScreen`][terok.tui.shield_sets_screen.ShieldSetsScreen] egress-set picker.

Pins the dismissal contract the Project Details caller relies on:
``("recommended",)`` for the master "Recommended" state, an explicit tuple
otherwise (empty = no curated sets), ``None`` on cancel — plus the
master/item cascade.
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Checkbox

from terok.lib.api import EGRESS_SETS, RECOMMENDED_SET
from terok.tui.shield_sets_screen import ShieldSetsScreen

_SENTINEL_PENDING = object()
_SLUGS = tuple(EGRESS_SETS)
_HOLDS_RECOMMENDED = [
    pytest.param((RECOMMENDED_SET,), id="alone"),
    pytest.param((RECOMMENDED_SET, _SLUGS[0]), id="before-a-set"),
    pytest.param((_SLUGS[0], RECOMMENDED_SET), id="after-a-set"),
]


class _Host(App):
    """Minimal test host that pushes a screen and captures its dismissal value."""

    def __init__(self, screen: ShieldSetsScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result: object = _SENTINEL_PENDING

    def on_mount(self) -> None:
        self.push_screen(self._screen, self._capture)

    def _capture(self, result: object) -> None:
        self.result = result


def _master(screen: ShieldSetsScreen) -> Checkbox:
    return screen.query_one("#shield-sets-recommended", Checkbox)


def _item(screen: ShieldSetsScreen, slug: str) -> Checkbox:
    return screen.query_one(f"#shield-sets-item-{slug}", Checkbox)


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [None, ()], ids=["unset", "empty"])
async def test_no_selection_initial_checks_nothing(initial: tuple[str, ...] | None) -> None:
    """Unset or empty ``shield.sets`` → master off, every item off."""
    app = _Host(ShieldSetsScreen(initial=initial))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ShieldSetsScreen)
        assert _master(screen).value is False
        for slug in _SLUGS:
            assert _item(screen, slug).value is False


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", _HOLDS_RECOMMENDED)
async def test_recommended_initial_arms_master_and_every_item(initial: tuple[str, ...]) -> None:
    """A selection holding ``recommended`` → master checked, every item checked."""
    app = _Host(ShieldSetsScreen(initial=initial))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ShieldSetsScreen)
        assert _master(screen).value is True
        for slug in _SLUGS:
            assert _item(screen, slug).value is True


@pytest.mark.asyncio
async def test_explicit_initial_seeds_named_items_only() -> None:
    """An explicit selection seeds exactly its items, master off."""
    pick = _SLUGS[0]
    app = _Host(ShieldSetsScreen(initial=(pick,)))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert _master(screen).value is False
        assert _item(screen, pick).value is True
        for other in _SLUGS[1:]:
            assert _item(screen, other).value is False


@pytest.mark.asyncio
async def test_toggling_master_sets_every_item() -> None:
    """Arming master checks every set; disarming it clears them all."""
    app = _Host(ShieldSetsScreen(initial=None))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        _master(screen).value = True
        await pilot.pause()
        assert all(_item(screen, slug).value for slug in _SLUGS)
        _master(screen).value = False
        await pilot.pause()
        assert not any(_item(screen, slug).value for slug in _SLUGS)


@pytest.mark.asyncio
async def test_unchecking_item_flips_master_off() -> None:
    """Removing one set with master on turns the selection into an enumeration."""
    app = _Host(ShieldSetsScreen(initial=(RECOMMENDED_SET,)))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        _item(screen, _SLUGS[0]).value = False
        await pilot.pause()
        assert _master(screen).value is False


@pytest.mark.asyncio
async def test_checking_every_item_by_hand_leaves_master_off() -> None:
    """Enumerating today's sets freezes that list — it is not ``recommended``."""
    app = _Host(ShieldSetsScreen(initial=None))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        for slug in _SLUGS:
            _item(screen, slug).value = True
        await pilot.pause()
        assert _master(screen).value is False
        await pilot.click("#shield-sets-save")
        await pilot.pause()
    assert app.result == _SLUGS


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", _HOLDS_RECOMMENDED)
async def test_save_with_master_emits_recommended(initial: tuple[str, ...]) -> None:
    """Save with master on returns the meta-set alone, by name."""
    app = _Host(ShieldSetsScreen(initial=initial))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#shield-sets-save")
        await pilot.pause()
    assert app.result == (RECOMMENDED_SET,)


@pytest.mark.asyncio
async def test_save_with_subset_emits_tuple() -> None:
    """Master off + named items → an explicit frozen tuple."""
    app = _Host(ShieldSetsScreen(initial=(_SLUGS[0], _SLUGS[1])))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#shield-sets-save")
        await pilot.pause()
    assert app.result == (_SLUGS[0], _SLUGS[1])


@pytest.mark.asyncio
async def test_save_with_nothing_selected_emits_empty_tuple() -> None:
    """Unlike agents, an empty selection is valid: explicitly no curated sets."""
    app = _Host(ShieldSetsScreen(initial=None))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#shield-sets-save")
        await pilot.pause()
    assert app.result == ()


@pytest.mark.asyncio
async def test_cancel_dismisses_with_none() -> None:
    """Cancel returns ``None`` — caller treats as no change."""
    app = _Host(ShieldSetsScreen(initial=None))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#shield-sets-cancel")
        await pilot.pause()
    assert app.result is None
