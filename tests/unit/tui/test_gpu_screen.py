# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the [`GpuSelectScreen`][terok.tui.gpu_screen.GpuSelectScreen] modal.

Pins the dismissal contract (``""`` = off, ``"all"``, token lists,
``None`` = cancel), the master/device cascade, the custom-field
precedence, and the degrade-to-all behaviour when detection finds
nothing — the wizard's GPU button relies on all four.
"""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from unittest.mock import patch

import pytest
from textual.app import App
from textual.widgets import Checkbox, Input, Label

from terok.lib.api import GpuDeviceChoice
from terok.tui.gpu_screen import GpuSelectScreen

_SENTINEL_PENDING = object()

_TRI_HOST = (
    GpuDeviceChoice("nvidia", "nvidia  (0000:01:00.0)"),
    GpuDeviceChoice("amd:0", "amd:0  (0000:03:00.0)"),
    GpuDeviceChoice("amd:1", "amd:1  (0000:0b:00.0)"),
    GpuDeviceChoice("intel", "intel  (0000:00:02.0)"),
)


class _Host(App):
    """Minimal test host that pushes the modal and captures its dismissal."""

    def __init__(self, screen: GpuSelectScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result: object = _SENTINEL_PENDING

    def on_mount(self) -> None:
        self.push_screen(self._screen, self._capture)

    def _capture(self, result: object) -> None:
        self.result = result


def _detected(choices: tuple[GpuDeviceChoice, ...]) -> AbstractContextManager[object]:
    return patch("terok.tui.gpu_screen.detect_gpu_choices", return_value=choices)


@pytest.mark.asyncio
async def test_master_default_applies_all() -> None:
    """Fresh modal: master preselected; Apply returns the ``all`` selector."""
    with _detected(_TRI_HOST):
        app = _Host(GpuSelectScreen())
        async with app.run_test(size=(100, 44)) as pilot:
            await pilot.pause()
            assert app.screen.query_one("#gpu-select-all", Checkbox).value is True
            await pilot.click("#gpu-select-apply")
            await pilot.pause()
    assert app.result == "all"


@pytest.mark.asyncio
async def test_off_button_returns_empty() -> None:
    """Off dismisses with ``""`` — GPU passthrough disabled."""
    with _detected(()):
        app = _Host(GpuSelectScreen(initial="all"))
        async with app.run_test(size=(100, 44)) as pilot:
            await pilot.pause()
            await pilot.click("#gpu-select-off")
            await pilot.pause()
    assert app.result == ""


@pytest.mark.asyncio
async def test_device_selection_builds_token_list() -> None:
    """Unchecking devices un-arms master; Apply joins the checked tokens."""
    with _detected(_TRI_HOST):
        app = _Host(GpuSelectScreen())
        async with app.run_test(size=(100, 44)) as pilot:
            await pilot.pause()
            # Detection worker mounted one checkbox per device, all
            # pre-checked because master was on.
            for item_id in ("#gpu-select-item-0", "#gpu-select-item-3"):
                app.screen.query_one(item_id, Checkbox).value = False
            await pilot.pause()
            assert app.screen.query_one("#gpu-select-all", Checkbox).value is False
            await pilot.click("#gpu-select-apply")
            await pilot.pause()
    assert app.result == "amd:0,amd:1"


@pytest.mark.asyncio
async def test_initial_tokens_preselect_devices() -> None:
    """A prior selector round-trips onto the matching device checkboxes."""
    with _detected(_TRI_HOST):
        app = _Host(GpuSelectScreen(initial="amd:1,intel"))
        async with app.run_test(size=(100, 44)) as pilot:
            await pilot.pause()
            assert app.screen.query_one("#gpu-select-all", Checkbox).value is False
            assert app.screen.query_one("#gpu-select-item-2", Checkbox).value is True
            assert app.screen.query_one("#gpu-select-item-3", Checkbox).value is True
            assert app.screen.query_one("#gpu-select-item-0", Checkbox).value is False
            await pilot.click("#gpu-select-apply")
            await pilot.pause()
    assert app.result == "amd:1,intel"


@pytest.mark.asyncio
async def test_custom_field_wins_and_is_validated() -> None:
    """A custom selector overrides checkboxes; bad grammar shows the error."""
    with _detected(()):
        app = _Host(GpuSelectScreen())
        async with app.run_test(size=(100, 44)) as pilot:
            await pilot.pause()
            custom = app.screen.query_one("#gpu-select-custom", Input)
            custom.value = "matrox"
            await pilot.click("#gpu-select-apply")
            await pilot.pause()
            error = str(app.screen.query_one("#gpu-select-error", Label).render())
            assert "matrox" in error
            custom.value = "nvidia:0,amd"
            # A Button swallows presses during its active-effect window;
            # wait it out before the second click.
            await pilot.pause(0.4)
            await pilot.click("#gpu-select-apply")
            await pilot.pause()
    assert app.result == "nvidia:0,amd"


@pytest.mark.asyncio
async def test_empty_detection_degrades_to_all_only() -> None:
    """No devices detected: the note updates, master still applies ``all``."""
    with _detected(()):
        app = _Host(GpuSelectScreen())
        async with app.run_test(size=(100, 44)) as pilot:
            await pilot.pause()
            note = str(app.screen.query_one("#gpu-select-probe-note", Label).render())
            assert "No devices detected" in note
            await pilot.click("#gpu-select-apply")
            await pilot.pause()
    assert app.result == "all"


@pytest.mark.asyncio
async def test_apply_before_detection_keeps_initial_tokens() -> None:
    """Applying while the probe is still running must not drop the selector."""
    gate = threading.Event()

    def _blocked_probe() -> tuple[GpuDeviceChoice, ...]:
        gate.wait(5)
        return ()

    try:
        with patch("terok.tui.gpu_screen.detect_gpu_choices", side_effect=_blocked_probe):
            app = _Host(GpuSelectScreen(initial="amd:1"))
            async with app.run_test(size=(100, 44)) as pilot:
                await pilot.pause()
                await pilot.click("#gpu-select-apply")
                await pilot.pause()
    finally:
        gate.set()
    assert app.result == "amd:1"


@pytest.mark.asyncio
async def test_fits_80x24_terminal() -> None:
    """The whole modal — device list through Apply — fits an 80x24 screen.

    Guards the compact-checkbox layout: the default three-row toggles
    pushed the buttons off-screen (pilot clicks then raise OutOfBounds).
    """
    with _detected(_TRI_HOST):
        app = _Host(GpuSelectScreen())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.screen.query_one("#gpu-select-item-3", Checkbox).region.height == 1
            await pilot.click("#gpu-select-apply")
            await pilot.pause()
    assert app.result == "all"


@pytest.mark.asyncio
async def test_dialog_hugs_content_on_large_terminals() -> None:
    """No blank tail: the dialog auto-sizes to its content, not the screen."""
    with _detected(_TRI_HOST):
        app = _Host(GpuSelectScreen())
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            dialog = app.screen.query_one("#gpu-select-dialog")
            assert dialog.region.height < 30


@pytest.mark.asyncio
async def test_cancel_returns_none() -> None:
    """Cancel dismisses with ``None`` — caller keeps the previous value."""
    with _detected(()):
        app = _Host(GpuSelectScreen(initial="amd"))
        async with app.run_test(size=(100, 44)) as pilot:
            await pilot.pause()
            await pilot.click("#gpu-select-cancel")
            await pilot.pause()
    assert app.result is None
