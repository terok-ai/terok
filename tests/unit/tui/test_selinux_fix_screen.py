# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the SELinux-policy-missing remediation modal."""

from __future__ import annotations

import importlib
import sys
from unittest import mock

import pytest


def _import_screen() -> tuple[type, type]:
    """Import the screen under the same textual-stubbed env as the other TUI tests.

    Mirrors the import_screens helper from test_detail_screens — Textual
    is stubbed so we don't need the runtime app harness to assert on
    the screen's pure state transitions.
    """
    if "textual" not in sys.modules:
        from . import test_detail_screens as _ds  # noqa: F401 — loads the textual stubs

    module = importlib.reload(importlib.import_module("terok.tui.selinux_fix_screen"))
    return module.SelinuxFixScreen, module.SelinuxFixOutcome


class TestSelinuxFixScreenActions:
    """Each binding + button dismisses with the expected outcome."""

    @pytest.mark.parametrize(
        ("method", "expected_enum_name"),
        [
            ("action_install_policy", "INSTALL_POLICY"),
            ("action_switch_to_tcp", "SWITCH_TO_TCP"),
            ("action_skip", "SKIPPED"),
            ("action_close", "SKIPPED"),
        ],
    )
    def test_actions_dismiss_with_outcome(self, method: str, expected_enum_name: str) -> None:
        screen_cls, outcome_cls = _import_screen()
        screen = screen_cls()
        screen.dismiss = mock.Mock()
        getattr(screen, method)()
        screen.dismiss.assert_called_once_with(getattr(outcome_cls, expected_enum_name))

    @pytest.mark.parametrize(
        ("button_id", "expected_enum_name"),
        [
            ("selinux-fix-install", "INSTALL_POLICY"),
            ("selinux-fix-tcp", "SWITCH_TO_TCP"),
            ("selinux-fix-skip", "SKIPPED"),
        ],
    )
    def test_button_pressed_routes_to_outcome(
        self, button_id: str, expected_enum_name: str
    ) -> None:
        screen_cls, outcome_cls = _import_screen()
        screen = screen_cls()
        screen.dismiss = mock.Mock()

        event = mock.Mock()
        event.button.id = button_id
        screen.on_button_pressed(event)
        screen.dismiss.assert_called_once_with(getattr(outcome_cls, expected_enum_name))
