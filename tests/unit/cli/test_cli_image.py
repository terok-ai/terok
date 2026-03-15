# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for image CLI commands."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from terok.cli.commands.image import _cmd_cleanup, _cmd_list
from terok.lib.containers.image_cleanup import CleanupResult, ImageInfo

IMAGES = [
    ImageInfo("terok-l0", "ubuntu-24.04", "sha256:aaa", "500MB", "2 days ago"),
    ImageInfo("myproj", "l2-cli", "sha256:bbb", "1.5GB", "1 day ago"),
]


def assert_output_contains(output: str, expected_lines: list[str]) -> None:
    """Assert that all expected lines appear in the command output."""
    for expected in expected_lines:
        assert expected in output


@pytest.mark.parametrize(
    ("images", "expected_lines"),
    [
        ([], ["No terok images found"]),
        (IMAGES, ["terok-l0:ubuntu-24.04", "myproj:l2-cli", "2 image(s)"]),
    ],
    ids=["empty", "with-images"],
)
def test_cmd_list_outputs_expected(
    images: list[ImageInfo],
    expected_lines: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("terok.cli.commands.image.list_images", return_value=images):
        _cmd_list(None)
    assert_output_contains(capsys.readouterr().out, expected_lines)


@pytest.mark.parametrize(
    ("result", "expected_lines"),
    [
        (CleanupResult(removed=[], failed=[], dry_run=False), ["No orphaned terok images found"]),
        (
            CleanupResult(removed=["old-proj:l2-cli"], failed=[], dry_run=True),
            ["Would remove", "1 image(s) would be removed"],
        ),
        (
            CleanupResult(
                removed=["old-proj:l2-cli"],
                failed=["in-use-proj:l2-cli"],
                dry_run=False,
            ),
            ["Removed", "Failed", "1 failed"],
        ),
    ],
    ids=["nothing-to-clean", "dry-run", "with-failures"],
)
def test_cmd_cleanup_outputs_expected(
    result: CleanupResult,
    expected_lines: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("terok.cli.commands.image.cleanup_images", return_value=result):
        _cmd_cleanup(dry_run=result.dry_run)
    assert_output_contains(capsys.readouterr().out, expected_lines)
