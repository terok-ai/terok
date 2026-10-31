# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The instructions screen shows how instructions combine, and switches the global setting."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Checkbox, Label

from terok.lib.core.instructions import inherits_instructions
from terok.tui.instructions_screen import EDIT_GLOBAL, SHOW_DEFAULT, InstructionsScreen

_PENDING = object()


class _Host(App):
    """Pushes the screen and records what it dismisses with."""

    def __init__(self) -> None:
        super().__init__()
        self.result: object = _PENDING

    def on_mount(self) -> None:
        self.push_screen(InstructionsScreen(), self._capture)

    def _capture(self, result: object) -> None:
        self.result = result


@pytest.fixture
def config_yml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point terok at a private user config file, and return its path."""
    path = tmp_path / "config.yml"
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(path))
    return path


def _labels(app: App) -> str:
    """Return the text of every label on the current screen."""
    return "\n".join(str(label.render()) for label in app.screen.query(Label))


async def test_shows_the_setting_on_by_default(config_yml: Path) -> None:
    async with _Host().run_test(size=(100, 30)) as pilot:
        box = pilot.app.screen.query_one("#instructions-inherit", Checkbox)
        assert box.value is True
        assert box.disabled is False
        assert f"{config_yml.parent / 'instructions.md'} (empty)" in _labels(pilot.app)


async def test_shows_a_file_with_text_without_the_empty_note(config_yml: Path) -> None:
    (config_yml.parent / "instructions.md").write_text("mine", encoding="utf-8")
    async with _Host().run_test(size=(100, 30)) as pilot:
        assert "(empty)" not in _labels(pilot.app)


async def test_shows_an_override_as_off(config_yml: Path) -> None:
    config_yml.write_text("agent:\n  instructions: []\n", encoding="utf-8")
    async with _Host().run_test(size=(100, 30)) as pilot:
        assert pilot.app.screen.query_one("#instructions-inherit", Checkbox).value is False


async def test_switching_writes_config_at_once(config_yml: Path) -> None:
    async with _Host().run_test(size=(100, 30)) as pilot:
        pilot.app.screen.query_one("#instructions-inherit", Checkbox).value = False
        await pilot.pause()
        assert inherits_instructions(config_yml) is False
        pilot.app.screen.query_one("#instructions-inherit", Checkbox).value = True
        await pilot.pause()
        assert inherits_instructions(config_yml) is True


async def test_form_that_cannot_be_switched_disables_the_checkbox(config_yml: Path) -> None:
    config_yml.write_text("agent:\n  instructions: flat text\n", encoding="utf-8")
    async with _Host().run_test(size=(100, 30)) as pilot:
        assert pilot.app.screen.query_one("#instructions-inherit", Checkbox).disabled is True
        assert "edit config.yml" in _labels(pilot.app)


async def test_refused_write_puts_the_checkbox_back(
    config_yml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr("terok.lib.api.agents.set_instructions_inherit", refuse)
    async with _Host().run_test(size=(100, 30)) as pilot:
        box = pilot.app.screen.query_one("#instructions-inherit", Checkbox)
        box.value = False
        await pilot.pause()
        assert box.value is True


@pytest.mark.parametrize(
    ("button", "expected"),
    [
        ("#instructions-edit-global", EDIT_GLOBAL),
        ("#instructions-show-default", SHOW_DEFAULT),
        ("#instructions-close", None),
    ],
    ids=["edit", "view", "close"],
)
async def test_buttons_dismiss_with_their_action(
    config_yml: Path, button: str, expected: str | None
) -> None:
    app = _Host()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.click(button)
        await pilot.pause()
    assert app.result == expected


async def test_escape_closes_without_an_action(config_yml: Path) -> None:
    app = _Host()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("escape")
        await pilot.pause()
    assert app.result is None
