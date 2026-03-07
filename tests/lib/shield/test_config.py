# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for shield config and path resolution."""

import unittest
import unittest.mock

from terok.lib.security.shield.config import (
    ShieldConfig,
    ShieldMode,
    load_shield_config,
    shield_hooks_dir,
    shield_logs_dir,
    shield_state_dir,
)


class TestShieldMode(unittest.TestCase):
    """Tests for ShieldMode enum."""

    def test_disabled(self) -> None:
        self.assertEqual(ShieldMode.DISABLED.value, "disabled")

    def test_standard(self) -> None:
        self.assertEqual(ShieldMode.STANDARD.value, "standard")

    def test_hardened(self) -> None:
        self.assertEqual(ShieldMode.HARDENED.value, "hardened")


class TestShieldConfig(unittest.TestCase):
    """Tests for ShieldConfig dataclass."""

    def test_defaults(self) -> None:
        cfg = ShieldConfig()
        self.assertEqual(cfg.mode, ShieldMode.DISABLED)
        self.assertEqual(cfg.default_profiles, ["dev-standard"])
        self.assertTrue(cfg.audit_enabled)
        self.assertTrue(cfg.audit_log_allowed)


class TestLoadShieldConfig(unittest.TestCase):
    """Tests for load_shield_config."""

    @unittest.mock.patch("terok.lib.security.shield.config.get_global_section", return_value={})
    def test_empty_config(self, _mock: unittest.mock.Mock) -> None:
        cfg = load_shield_config()
        self.assertEqual(cfg.mode, ShieldMode.DISABLED)

    @unittest.mock.patch(
        "terok.lib.security.shield.config.get_global_section",
        return_value={"mode": "standard", "default_profiles": ["dev-python"]},
    )
    def test_standard_mode(self, _mock: unittest.mock.Mock) -> None:
        cfg = load_shield_config()
        self.assertEqual(cfg.mode, ShieldMode.STANDARD)
        self.assertEqual(cfg.default_profiles, ["dev-python"])

    @unittest.mock.patch(
        "terok.lib.security.shield.config.get_global_section",
        return_value={"mode": "hardened"},
    )
    def test_hardened_mode(self, _mock: unittest.mock.Mock) -> None:
        cfg = load_shield_config()
        self.assertEqual(cfg.mode, ShieldMode.HARDENED)

    @unittest.mock.patch(
        "terok.lib.security.shield.config.get_global_section",
        return_value={"mode": "invalid_value"},
    )
    def test_invalid_mode_defaults_disabled(self, _mock: unittest.mock.Mock) -> None:
        cfg = load_shield_config()
        self.assertEqual(cfg.mode, ShieldMode.DISABLED)


class TestPathHelpers(unittest.TestCase):
    """Tests for shield path helpers."""

    @unittest.mock.patch("terok.lib.security.shield.config.state_root")
    def test_shield_state_dir(self, mock_root: unittest.mock.Mock) -> None:
        from pathlib import Path

        mock_root.return_value = Path("/tmp/terok-state")
        result = shield_state_dir()
        self.assertEqual(result, Path("/tmp/terok-state/shield"))

    @unittest.mock.patch("terok.lib.security.shield.config.state_root")
    def test_shield_hooks_dir(self, mock_root: unittest.mock.Mock) -> None:
        from pathlib import Path

        mock_root.return_value = Path("/tmp/terok-state")
        result = shield_hooks_dir()
        self.assertEqual(result, Path("/tmp/terok-state/shield/hooks"))

    @unittest.mock.patch("terok.lib.security.shield.config.state_root")
    def test_shield_logs_dir(self, mock_root: unittest.mock.Mock) -> None:
        from pathlib import Path

        mock_root.return_value = Path("/tmp/terok-state")
        result = shield_logs_dir()
        self.assertEqual(result, Path("/tmp/terok-state/shield/logs"))
