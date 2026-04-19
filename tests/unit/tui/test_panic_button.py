# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the PanicButton widget and panic flow integration."""

from unittest import mock

import pytest

from tests.unit.tui.tui_test_helpers import build_textual_stubs, import_fresh


@pytest.fixture()
def modules():
    """Import TUI modules with Textual stubs."""
    stubs = build_textual_stubs()
    # The stub Message needs post_message/set_timer/add_class/remove_class
    # to support PanicButton's runtime calls.  These are provided by Textual's
    # Widget at runtime; we patch them onto instances in each test instead of
    # modifying the shared stubs.
    return import_fresh(stubs)


@pytest.fixture()
def button(modules):
    """Create a PanicButton instance with mocked Textual methods."""
    _, widgets, _ = modules
    btn = widgets.PanicButton(id="panic-button")
    btn.add_class = mock.Mock()
    btn.remove_class = mock.Mock()
    btn.update = mock.Mock()
    btn.post_message = mock.Mock()

    timer = mock.Mock()
    timer.stop = mock.Mock()
    btn.set_timer = mock.Mock(return_value=timer)

    return btn


class TestPanicButtonInitialState:
    """Verify the button starts in a safe idle state."""

    def test_not_armed(self, button):
        """Button is not armed after construction."""
        assert button._armed is False

    def test_can_focus_disabled(self, modules):
        """Button is excluded from the Tab focus chain."""
        _, widgets, _ = modules
        assert widgets.PanicButton.can_focus is False

    def test_no_timer(self, button):
        """No disarm timer is set initially."""
        assert button._disarm_timer is None


class TestArm:
    """Verify arming transitions and side effects."""

    def test_sets_armed_flag(self, button):
        """arm() transitions to armed state."""
        button.arm()
        assert button._armed is True

    def test_adds_css_class(self, button):
        """arm() adds the 'armed' CSS class for visual feedback."""
        button.arm()
        button.add_class.assert_called_once_with("armed")

    def test_updates_label(self, button):
        """arm() changes the label to the armed prompt."""
        button.arm()
        button.update.assert_called_with("PRESS AGAIN TO PANIC")

    def test_starts_disarm_timer(self, button):
        """arm() starts a 5-second auto-disarm timer."""
        button.arm()
        button.set_timer.assert_called_once()
        delay, callback = button.set_timer.call_args[0]
        assert delay == 5.0
        assert callback == button.disarm

    def test_idempotent(self, button):
        """Calling arm() twice does not reset the timer."""
        button.arm()
        button.arm()
        button.set_timer.assert_called_once()


class TestDisarm:
    """Verify disarming transitions and cleanup."""

    def test_clears_armed_flag(self, button):
        """disarm() returns to idle state."""
        button.arm()
        button.disarm()
        assert button._armed is False

    def test_removes_css_class(self, button):
        """disarm() removes the 'armed' CSS class."""
        button.arm()
        button.disarm()
        button.remove_class.assert_called_once_with("armed")

    def test_restores_label(self, button):
        """disarm() restores the idle label."""
        button.arm()
        button.disarm()
        assert button.update.call_args[0][0] == "PANIC"

    def test_stops_timer(self, button):
        """disarm() stops the pending auto-disarm timer."""
        button.arm()
        timer = button._disarm_timer
        button.disarm()
        timer.stop.assert_called_once()
        assert button._disarm_timer is None

    def test_noop_when_idle(self, button):
        """disarm() is safe to call when already idle."""
        button.disarm()
        button.remove_class.assert_not_called()


class TestFire:
    """Verify firing posts the message and disarms."""

    def test_posts_fired_message(self, button, modules):
        """fire() posts a PanicButton.Fired message."""
        _, widgets, _ = modules
        button.arm()
        button.fire()
        button.post_message.assert_called_once()
        msg = button.post_message.call_args[0][0]
        assert isinstance(msg, widgets.PanicButton.Fired)

    def test_disarms_after_firing(self, button):
        """fire() returns the button to idle state."""
        button.arm()
        button.fire()
        assert button._armed is False


class TestOnClick:
    """Verify mouse click dispatches to arm or fire."""

    def test_arms_when_idle(self, button):
        """Click on idle button arms it."""
        button.on_click()
        assert button._armed is True

    def test_fires_when_armed(self, button, modules):
        """Click on armed button fires."""
        _, widgets, _ = modules
        button.arm()
        button.on_click()
        button.post_message.assert_called_once()
        msg = button.post_message.call_args[0][0]
        assert isinstance(msg, widgets.PanicButton.Fired)
        assert button._armed is False


class TestConfirmDestructiveScreenRename:
    """Regression guard: the old name is gone, the new name exists."""

    def test_new_name_exists(self, modules):
        """ConfirmDestructiveScreen is importable from screens."""
        screens, _, _ = modules
        assert hasattr(screens, "ConfirmDestructiveScreen")

    def test_old_name_gone(self, modules):
        """ConfirmDeleteScreen no longer exists."""
        screens, _, _ = modules
        assert not hasattr(screens, "ConfirmDeleteScreen")
