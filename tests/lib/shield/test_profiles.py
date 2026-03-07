# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for profile loading and composition."""

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from terok.lib.security.shield.profiles import (
    compose_profiles,
    list_profiles,
    load_profile,
)


class TestLoadProfile(unittest.TestCase):
    """Tests for load_profile."""

    def test_missing_profile_raises(self) -> None:
        with (
            unittest.mock.patch(
                "terok.lib.security.shield.profiles.shield_profiles_dir",
                return_value=Path("/nonexistent"),
            ),
            self.assertRaises(FileNotFoundError),
        ):
            load_profile("nonexistent-profile")

    @unittest.mock.patch("terok.lib.security.shield.profiles.shield_profiles_dir")
    def test_user_profile_takes_precedence(self, mock_dir: unittest.mock.Mock) -> None:
        """User profiles override bundled ones."""
        with tempfile.TemporaryDirectory() as tmp:
            mock_dir.return_value = Path(tmp)
            user_profile = Path(tmp) / "test.nft"
            user_profile.write_text("# user profile\n")
            content = load_profile("test")
            self.assertEqual(content, "# user profile\n")

    def test_bundled_profile_exists(self) -> None:
        """The dev-standard profile is bundled and loadable."""
        content = load_profile("dev-standard")
        self.assertIn("allow_v4", content)


class TestComposeProfiles(unittest.TestCase):
    """Tests for compose_profiles."""

    @unittest.mock.patch("terok.lib.security.shield.profiles.shield_profiles_dir")
    def test_compose_concatenates(self, mock_dir: unittest.mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mock_dir.return_value = Path(tmp)
            (Path(tmp) / "a.nft").write_text("# a\n")
            (Path(tmp) / "b.nft").write_text("# b\n")
            result = compose_profiles(["a", "b"])
            self.assertIn("# a", result)
            self.assertIn("# b", result)


class TestListProfiles(unittest.TestCase):
    """Tests for list_profiles."""

    def test_includes_bundled(self) -> None:
        profiles = list_profiles()
        self.assertIn("dev-standard", profiles)
        self.assertIn("dev-python", profiles)
        self.assertIn("dev-node", profiles)
