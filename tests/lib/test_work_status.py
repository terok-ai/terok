# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for agent work-status reading."""

from pathlib import Path

import pytest
import yaml

from luskctl.lib.containers.work_status import (
    STATUS_FILE_NAME,
    WORK_STATUS_DISPLAY,
    WORK_STATUSES,
    WorkStatus,
    read_work_status,
)


class TestReadWorkStatus:
    """Tests for read_work_status()."""

    def test_valid_yaml_dict(self, tmp_path: Path):
        (tmp_path / STATUS_FILE_NAME).write_text(
            yaml.safe_dump({"status": "coding", "message": "Implementing auth"})
        )
        ws = read_work_status(tmp_path)
        assert ws.status == "coding"
        assert ws.message == "Implementing auth"

    def test_bare_string(self, tmp_path: Path):
        (tmp_path / STATUS_FILE_NAME).write_text("testing\n")
        ws = read_work_status(tmp_path)
        assert ws.status == "testing"
        assert ws.message is None

    def test_empty_file(self, tmp_path: Path):
        (tmp_path / STATUS_FILE_NAME).write_text("")
        ws = read_work_status(tmp_path)
        assert ws.status is None
        assert ws.message is None

    def test_missing_dir(self, tmp_path: Path):
        ws = read_work_status(tmp_path / "nonexistent")
        assert ws.status is None
        assert ws.message is None

    def test_missing_file(self, tmp_path: Path):
        ws = read_work_status(tmp_path)
        assert ws.status is None
        assert ws.message is None

    def test_malformed_yaml(self, tmp_path: Path):
        (tmp_path / STATUS_FILE_NAME).write_text("{{broken yaml")
        ws = read_work_status(tmp_path)
        assert ws.status is None
        assert ws.message is None

    def test_status_only_dict(self, tmp_path: Path):
        (tmp_path / STATUS_FILE_NAME).write_text(yaml.safe_dump({"status": "done"}))
        ws = read_work_status(tmp_path)
        assert ws.status == "done"
        assert ws.message is None

    def test_unknown_status_preserved(self, tmp_path: Path):
        (tmp_path / STATUS_FILE_NAME).write_text(
            yaml.safe_dump({"status": "thinking-hard", "message": "Deep thoughts"})
        )
        ws = read_work_status(tmp_path)
        assert ws.status == "thinking-hard"
        assert ws.message == "Deep thoughts"

    def test_numeric_yaml_returns_empty(self, tmp_path: Path):
        (tmp_path / STATUS_FILE_NAME).write_text("42\n")
        ws = read_work_status(tmp_path)
        assert ws.status is None

    def test_list_yaml_returns_empty(self, tmp_path: Path):
        (tmp_path / STATUS_FILE_NAME).write_text("- item1\n- item2\n")
        ws = read_work_status(tmp_path)
        assert ws.status is None


class TestWorkStatusVocabulary:
    """Tests for WORK_STATUSES and WORK_STATUS_DISPLAY consistency."""

    def test_all_statuses_have_display(self):
        for status in WORK_STATUSES:
            assert status in WORK_STATUS_DISPLAY, f"Missing display for {status}"

    def test_all_display_have_status(self):
        for status in WORK_STATUS_DISPLAY:
            assert status in WORK_STATUSES, f"Display entry without status: {status}"

    def test_vocabulary_completeness(self):
        expected = {
            "planning",
            "coding",
            "testing",
            "debugging",
            "reviewing",
            "documenting",
            "done",
            "blocked",
            "error",
        }
        assert set(WORK_STATUSES.keys()) == expected

    def test_display_has_emoji_and_label(self):
        for status, info in WORK_STATUS_DISPLAY.items():
            assert info.label, f"Empty label for {status}"
            assert info.emoji, f"Empty emoji for {status}"


class TestWorkStatusDataclass:
    """Tests for WorkStatus dataclass."""

    def test_defaults(self):
        ws = WorkStatus()
        assert ws.status is None
        assert ws.message is None

    def test_frozen(self):
        ws = WorkStatus(status="coding")
        with pytest.raises(AttributeError):
            ws.status = "testing"  # type: ignore[misc]
