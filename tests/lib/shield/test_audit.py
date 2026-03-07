# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for audit logging."""

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from terok.lib.security.shield.audit import list_log_files, log_event, tail_log


class TestLogEvent(unittest.TestCase):
    """Tests for log_event."""

    @unittest.mock.patch("terok.lib.security.shield.audit.shield_logs_dir")
    def test_writes_jsonl(self, mock_dir: unittest.mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mock_dir.return_value = Path(tmp)
            log_event("test-ctr", "setup", detail="test")
            log_file = Path(tmp) / "test-ctr.jsonl"
            self.assertTrue(log_file.exists())
            lines = log_file.read_text().strip().split("\n")
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])
            self.assertEqual(entry["container"], "test-ctr")
            self.assertEqual(entry["action"], "setup")
            self.assertEqual(entry["detail"], "test")
            self.assertIn("ts", entry)

    @unittest.mock.patch("terok.lib.security.shield.audit.shield_logs_dir")
    def test_optional_fields(self, mock_dir: unittest.mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mock_dir.return_value = Path(tmp)
            log_event("test-ctr", "denied", dest="1.2.3.4")
            entry = json.loads((Path(tmp) / "test-ctr.jsonl").read_text().strip())
            self.assertEqual(entry["dest"], "1.2.3.4")
            self.assertNotIn("detail", entry)


class TestTailLog(unittest.TestCase):
    """Tests for tail_log."""

    @unittest.mock.patch("terok.lib.security.shield.audit.shield_logs_dir")
    def test_tail_returns_entries(self, mock_dir: unittest.mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mock_dir.return_value = Path(tmp)
            log_file = Path(tmp) / "test-ctr.jsonl"
            entries = [
                json.dumps(
                    {
                        "ts": "2026-01-01T00:00:00+00:00",
                        "container": "test-ctr",
                        "action": f"event-{i}",
                    }
                )
                for i in range(5)
            ]
            log_file.write_text("\n".join(entries) + "\n")
            result = list(tail_log("test-ctr", n=3))
            self.assertEqual(len(result), 3)
            self.assertEqual(result[0]["action"], "event-2")

    @unittest.mock.patch("terok.lib.security.shield.audit.shield_logs_dir")
    def test_tail_missing_file(self, mock_dir: unittest.mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mock_dir.return_value = Path(tmp)
            result = list(tail_log("nonexistent"))
            self.assertEqual(result, [])


class TestListLogFiles(unittest.TestCase):
    """Tests for list_log_files."""

    @unittest.mock.patch("terok.lib.security.shield.audit.shield_logs_dir")
    def test_lists_containers(self, mock_dir: unittest.mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mock_dir.return_value = Path(tmp)
            (Path(tmp) / "ctr-a.jsonl").write_text("")
            (Path(tmp) / "ctr-b.jsonl").write_text("")
            result = list_log_files()
            self.assertEqual(result, ["ctr-a", "ctr-b"])
