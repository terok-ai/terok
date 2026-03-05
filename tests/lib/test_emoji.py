# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the emoji display-width utility."""

import unittest

from terok.lib.util.emoji import draw_emoji, is_emoji_enabled, set_emoji_enabled


class TestDrawEmoji(unittest.TestCase):
    """Verify draw_emoji pads emojis to a consistent cell width."""

    def setUp(self):
        """Ensure emoji mode is enabled for each test."""
        set_emoji_enabled(True)

    def tearDown(self):
        """Reset emoji mode after each test."""
        set_emoji_enabled(True)

    def test_empty_string_returns_empty(self):
        """Empty input produces empty output."""
        self.assertEqual(draw_emoji(""), "")

    def test_none_returns_empty(self):
        """None input produces empty output."""
        self.assertEqual(draw_emoji(None), "")  # type: ignore[arg-type]

    def test_wide_emoji_no_padding(self):
        """A natively 2-cell-wide emoji (eaw=W) needs no padding."""
        self.assertEqual(draw_emoji("\U0001f680"), "\U0001f680")

    def test_narrow_char_gets_padded(self):
        """A 1-cell character gets padded to width 2."""
        self.assertEqual(draw_emoji("X"), "X ")

    def test_custom_width(self):
        """Custom width parameter is respected."""
        self.assertEqual(draw_emoji("X", width=4), "X   ")

    def test_emoji_wider_than_target(self):
        """Emoji wider than target width is returned unchanged."""
        self.assertEqual(draw_emoji("\U0001f680", width=1), "\U0001f680")

    def test_all_status_emojis_are_exactly_width_2(self):
        """All status emojis used by the project are exactly 2 cells wide."""
        from terok.lib.containers.task_display import STATUS_DISPLAY

        for status, info in STATUS_DISPLAY.items():
            self.assertEqual(
                draw_emoji(info.emoji),
                info.emoji,
                f"Status emoji for {status!r} should not need padding",
            )
            self.assertEqual(
                draw_emoji(info.emoji, width=3),
                f"{info.emoji} ",
                f"Status emoji for {status!r} should be exactly 2 cells wide",
            )

    def test_all_mode_emojis_are_exactly_width_2(self):
        """All mode emojis used by the project are exactly 2 cells wide."""
        from terok.lib.containers.task_display import MODE_DISPLAY

        for mode, info in MODE_DISPLAY.items():
            self.assertEqual(
                draw_emoji(info.emoji),
                info.emoji,
                f"Mode emoji for {mode!r} should not need padding",
            )
            self.assertEqual(
                draw_emoji(info.emoji, width=3),
                f"{info.emoji} ",
                f"Mode emoji for {mode!r} should be exactly 2 cells wide",
            )

    def test_all_backend_emojis_are_exactly_width_2(self):
        """All web backend emojis are exactly 2 cells wide."""
        from terok.lib.containers.task_display import (
            WEB_BACKEND_DEFAULT_EMOJI,
            WEB_BACKEND_EMOJI,
        )

        for backend, emoji in WEB_BACKEND_EMOJI.items():
            self.assertEqual(
                draw_emoji(emoji),
                emoji,
                f"Backend emoji for {backend!r} should not need padding",
            )
            self.assertEqual(
                draw_emoji(emoji, width=3),
                f"{emoji} ",
                f"Backend emoji for {backend!r} should be exactly 2 cells wide",
            )
        self.assertEqual(draw_emoji(WEB_BACKEND_DEFAULT_EMOJI), WEB_BACKEND_DEFAULT_EMOJI)
        self.assertEqual(
            draw_emoji(WEB_BACKEND_DEFAULT_EMOJI, width=3),
            f"{WEB_BACKEND_DEFAULT_EMOJI} ",
        )

    def test_all_security_class_emojis_are_exactly_width_2(self):
        """All security class emojis are exactly 2 cells wide."""
        from terok.lib.containers.task_display import SECURITY_CLASS_DISPLAY

        for key, badge in SECURITY_CLASS_DISPLAY.items():
            self.assertEqual(
                draw_emoji(badge.emoji),
                badge.emoji,
                f"Security class emoji for {key!r} should not need padding",
            )
            self.assertEqual(
                draw_emoji(badge.emoji, width=3),
                f"{badge.emoji} ",
                f"Security class emoji for {key!r} should be exactly 2 cells wide",
            )

    def test_all_gpu_emojis_are_exactly_width_2(self):
        """All GPU display emojis are exactly 2 cells wide."""
        from terok.lib.containers.task_display import GPU_DISPLAY

        for key, badge in GPU_DISPLAY.items():
            self.assertEqual(
                draw_emoji(badge.emoji),
                badge.emoji,
                f"GPU emoji for {key!r} should not need padding",
            )
            self.assertEqual(
                draw_emoji(badge.emoji, width=3),
                f"{badge.emoji} ",
                f"GPU emoji for {key!r} should be exactly 2 cells wide",
            )

    def test_all_work_status_emojis_are_exactly_width_2(self):
        """All work status emojis are exactly 2 cells wide."""
        from terok.lib.containers.work_status import WORK_STATUS_DISPLAY

        for key, info in WORK_STATUS_DISPLAY.items():
            self.assertEqual(
                draw_emoji(info.emoji),
                info.emoji,
                f"Work status emoji for {key!r} should not need padding",
            )
            self.assertEqual(
                draw_emoji(info.emoji, width=3),
                f"{info.emoji} ",
                f"Work status emoji for {key!r} should be exactly 2 cells wide",
            )


class TestNoEmojiMode(unittest.TestCase):
    """Verify draw_emoji returns text labels when emoji mode is disabled."""

    def setUp(self):
        """Disable emoji mode for these tests."""
        set_emoji_enabled(False)

    def tearDown(self):
        """Re-enable emoji mode after tests."""
        set_emoji_enabled(True)

    def test_is_emoji_enabled_false(self):
        """is_emoji_enabled reflects the current state."""
        self.assertFalse(is_emoji_enabled())

    def test_no_emoji_with_label(self):
        """With emoji disabled and a label, returns [label]."""
        result = draw_emoji("\U0001f680", label="rocket")
        self.assertEqual(result, "[rocket]")

    def test_no_emoji_with_label_padded(self):
        """With emoji disabled, short labels are padded to width."""
        result = draw_emoji("\U0001f680", width=6, label="ok")
        self.assertEqual(result, "[ok]  ")

    def test_no_emoji_without_label(self):
        """With emoji disabled and no label, returns empty string."""
        result = draw_emoji("\U0001f680")
        self.assertEqual(result, "")

    def test_no_emoji_empty_string(self):
        """With emoji disabled and empty input, returns empty string."""
        result = draw_emoji("", label="test")
        self.assertEqual(result, "[test]")

    def test_no_emoji_label_wider_than_width(self):
        """Labels wider than target width are returned unchanged."""
        result = draw_emoji("\U0001f680", width=2, label="running")
        self.assertEqual(result, "[running]")

    def test_set_emoji_enabled_toggle(self):
        """Toggling emoji mode changes draw_emoji behavior."""
        set_emoji_enabled(True)
        self.assertTrue(is_emoji_enabled())
        self.assertEqual(draw_emoji("\U0001f680"), "\U0001f680")

        set_emoji_enabled(False)
        self.assertFalse(is_emoji_enabled())
        self.assertEqual(draw_emoji("\U0001f680", label="rocket"), "[rocket]")

    def test_all_status_display_has_labels(self):
        """All STATUS_DISPLAY entries have non-empty labels for no-emoji mode."""
        from terok.lib.containers.task_display import STATUS_DISPLAY

        for status, info in STATUS_DISPLAY.items():
            self.assertTrue(
                info.label,
                f"STATUS_DISPLAY[{status!r}] must have a non-empty label for --no-emoji mode",
            )

    def test_all_mode_display_has_labels(self):
        """All MODE_DISPLAY entries have labels (empty is OK for None mode)."""
        from terok.lib.containers.task_display import MODE_DISPLAY

        for mode, info in MODE_DISPLAY.items():
            if mode is not None:
                self.assertTrue(
                    info.label,
                    f"MODE_DISPLAY[{mode!r}] must have a non-empty label for --no-emoji mode",
                )

    def test_all_security_class_display_has_labels(self):
        """All SECURITY_CLASS_DISPLAY entries have non-empty labels."""
        from terok.lib.containers.task_display import SECURITY_CLASS_DISPLAY

        for key, badge in SECURITY_CLASS_DISPLAY.items():
            self.assertTrue(
                badge.label,
                f"SECURITY_CLASS_DISPLAY[{key!r}] must have a non-empty label",
            )

    def test_all_gpu_display_has_labels(self):
        """All GPU_DISPLAY entries have non-empty labels."""
        from terok.lib.containers.task_display import GPU_DISPLAY

        for key, badge in GPU_DISPLAY.items():
            self.assertTrue(
                badge.label,
                f"GPU_DISPLAY[{key!r}] must have a non-empty label",
            )

    def test_all_work_status_display_has_labels(self):
        """All WORK_STATUS_DISPLAY entries have non-empty labels."""
        from terok.lib.containers.work_status import WORK_STATUS_DISPLAY

        for key, info in WORK_STATUS_DISPLAY.items():
            self.assertTrue(
                info.label,
                f"WORK_STATUS_DISPLAY[{key!r}] must have a non-empty label",
            )


if __name__ == "__main__":
    unittest.main()
