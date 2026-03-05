# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

import unittest
import unittest.mock
from io import StringIO

from terok.cli.commands.image import _cmd_cleanup, _cmd_list
from terok.lib.containers.image_cleanup import CleanupResult, ImageInfo


class TestCmdList(unittest.TestCase):
    """Tests for the ``image list`` CLI command."""

    @unittest.mock.patch("terok.cli.commands.image.list_images")
    def test_list_no_images(self, mock_list: unittest.mock.Mock) -> None:
        mock_list.return_value = []
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_list(None)
        self.assertIn("No terok images found", out.getvalue())

    @unittest.mock.patch("terok.cli.commands.image.list_images")
    def test_list_with_images(self, mock_list: unittest.mock.Mock) -> None:
        mock_list.return_value = [
            ImageInfo("terok-l0", "ubuntu-24.04", "sha256:aaa", "500MB", "2 days ago"),
            ImageInfo("myproj", "l2-cli", "sha256:bbb", "1.5GB", "1 day ago"),
        ]
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_list(None)
        output = out.getvalue()
        self.assertIn("terok-l0:ubuntu-24.04", output)
        self.assertIn("myproj:l2-cli", output)
        self.assertIn("2 image(s)", output)


class TestCmdCleanup(unittest.TestCase):
    """Tests for the ``image cleanup`` CLI command."""

    @unittest.mock.patch("terok.cli.commands.image.cleanup_images")
    def test_cleanup_nothing(self, mock_cleanup: unittest.mock.Mock) -> None:
        mock_cleanup.return_value = CleanupResult(removed=[], failed=[], dry_run=False)
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_cleanup(dry_run=False)
        self.assertIn("No orphaned terok images found", out.getvalue())

    @unittest.mock.patch("terok.cli.commands.image.cleanup_images")
    def test_cleanup_dry_run(self, mock_cleanup: unittest.mock.Mock) -> None:
        mock_cleanup.return_value = CleanupResult(
            removed=["old-proj:l2-cli"], failed=[], dry_run=True
        )
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_cleanup(dry_run=True)
        output = out.getvalue()
        self.assertIn("Would remove", output)
        self.assertIn("1 image(s) would be removed", output)

    @unittest.mock.patch("terok.cli.commands.image.cleanup_images")
    def test_cleanup_with_failures(self, mock_cleanup: unittest.mock.Mock) -> None:
        mock_cleanup.return_value = CleanupResult(
            removed=["old-proj:l2-cli"],
            failed=["in-use-proj:l2-cli"],
            dry_run=False,
        )
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_cleanup(dry_run=False)
        output = out.getvalue()
        self.assertIn("Removed", output)
        self.assertIn("Failed", output)
        self.assertIn("1 failed", output)
