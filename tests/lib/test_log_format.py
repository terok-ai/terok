# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for log_format module (agent log formatters)."""

import json
import sys
import unittest
from io import StringIO

from terok.lib.containers.log_format import (
    ClaudeStreamJsonFormatter,
    PlainTextFormatter,
    auto_detect_formatter,
)


class PlainTextFormatterTests(unittest.TestCase):
    """Tests for PlainTextFormatter."""

    def test_passthrough(self) -> None:
        fmt = PlainTextFormatter()
        buf = StringIO()
        sys.stdout = buf
        try:
            fmt.feed_line("hello world")
            fmt.feed_line("line two")
            fmt.finish()
        finally:
            sys.stdout = sys.__stdout__
        self.assertEqual(buf.getvalue(), "hello world\nline two\n")

    def test_finish_is_noop(self) -> None:
        fmt = PlainTextFormatter()
        fmt.finish()  # should not raise


class ClaudeStreamJsonFormatterStreamingTests(unittest.TestCase):
    """Tests for ClaudeStreamJsonFormatter with streaming=True."""

    def _make_formatter(self) -> ClaudeStreamJsonFormatter:
        return ClaudeStreamJsonFormatter(streaming=True, color=False)

    def test_system_init(self) -> None:
        fmt = self._make_formatter()
        buf = StringIO()
        sys.stdout = buf
        try:
            line = json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "abc123",
                    "model": "claude-sonnet-4-20250514",
                    "tools": [{"name": "Read"}, {"name": "Write"}],
                }
            )
            fmt.feed_line(line)
        finally:
            sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertIn("[system]", output)
        self.assertIn("abc123", output)
        self.assertIn("2 tools available", output)

    def test_streaming_text_block(self) -> None:
        """Streaming text delta produces typewriter output."""
        fmt = self._make_formatter()
        buf = StringIO()
        sys.stdout = buf
        try:
            # content_block_start for text
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "content_block_start",
                        "content_block": {"type": "text"},
                    }
                )
            )
            # text deltas
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "Hello"},
                    }
                )
            )
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": " world"},
                    }
                )
            )
            # content_block_stop
            fmt.feed_line(json.dumps({"type": "content_block_stop"}))
        finally:
            sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertIn("Hello", output)
        self.assertIn(" world", output)

    def test_streaming_tool_use_block(self) -> None:
        """Streaming tool_use block accumulates input, prints on stop."""
        fmt = self._make_formatter()
        buf = StringIO()
        sys.stdout = buf
        try:
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "content_block_start",
                        "content_block": {"type": "tool_use", "name": "Read"},
                    }
                )
            )
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "input_json_delta", "partial_json": '{"file_pa'},
                    }
                )
            )
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "input_json_delta", "partial_json": 'th": "/foo"}'},
                    }
                )
            )
            fmt.feed_line(json.dumps({"type": "content_block_stop"}))
        finally:
            sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertIn("[tool] Read", output)
        self.assertIn("file_path", output)
        self.assertIn("/foo", output)

    def test_result_captured_for_finish(self) -> None:
        """Result message is captured and printed on finish()."""
        fmt = self._make_formatter()
        buf_out = StringIO()
        buf_err = StringIO()
        sys.stdout = buf_out
        sys.stderr = buf_err
        try:
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "result",
                        "cost_usd": 0.0123,
                        "duration_ms": 5000,
                        "num_turns": 3,
                        "usage": {"input_tokens": 1000, "output_tokens": 500},
                    }
                )
            )
            fmt.finish()
        finally:
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__
        output = buf_err.getvalue()
        self.assertIn("[result]", output)
        self.assertIn("turns=3", output)
        self.assertIn("cost=$0.0123", output)
        self.assertIn("duration=5.0s", output)
        self.assertIn("tokens=1000in/500out", output)

    def test_assistant_coalesced_message(self) -> None:
        """Even in streaming mode, coalesced assistant messages are handled."""
        fmt = self._make_formatter()
        buf = StringIO()
        sys.stdout = buf
        try:
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "text", "text": "I'll fix the bug."},
                                {"type": "tool_use", "name": "Edit", "input": {"file": "a.py"}},
                            ],
                        },
                    }
                )
            )
        finally:
            sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertIn("I'll fix the bug.", output)
        self.assertIn("[tool] Edit", output)
        self.assertIn("file", output)

    def test_user_tool_result(self) -> None:
        """User messages with tool_result are displayed."""
        fmt = self._make_formatter()
        buf = StringIO()
        sys.stdout = buf
        try:
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_01234567",
                                    "content": "File contents here",
                                }
                            ],
                        },
                    }
                )
            )
        finally:
            sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertIn("[tool_result]", output)
        self.assertIn("File contents here", output)

    def test_user_tool_error(self) -> None:
        """User messages with tool_result and is_error are shown as errors."""
        fmt = self._make_formatter()
        buf = StringIO()
        sys.stdout = buf
        try:
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_01234567",
                                    "content": "Permission denied",
                                    "is_error": True,
                                }
                            ],
                        },
                    }
                )
            )
        finally:
            sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertIn("[tool_error]", output)
        self.assertIn("Permission denied", output)


class ClaudeStreamJsonFormatterNoStreamTests(unittest.TestCase):
    """Tests for ClaudeStreamJsonFormatter with streaming=False."""

    def _make_formatter(self) -> ClaudeStreamJsonFormatter:
        return ClaudeStreamJsonFormatter(streaming=False, color=False)

    def test_stream_events_skipped(self) -> None:
        """With streaming=False, content_block events are ignored."""
        fmt = self._make_formatter()
        buf = StringIO()
        sys.stdout = buf
        try:
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "content_block_start",
                        "content_block": {"type": "text"},
                    }
                )
            )
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "should not appear"},
                    }
                )
            )
            fmt.feed_line(json.dumps({"type": "content_block_stop"}))
        finally:
            sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertEqual(output, "")

    def test_coalesced_messages_still_work(self) -> None:
        """With streaming=False, coalesced assistant messages still appear."""
        fmt = self._make_formatter()
        buf = StringIO()
        sys.stdout = buf
        try:
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [{"type": "text", "text": "coalesced output"}],
                        },
                    }
                )
            )
        finally:
            sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertIn("coalesced output", output)


class ClaudeStreamJsonFormatterEdgeCaseTests(unittest.TestCase):
    """Edge case tests for ClaudeStreamJsonFormatter."""

    def test_malformed_json_not_crash(self) -> None:
        """Malformed JSON lines are printed as plain text."""
        fmt = ClaudeStreamJsonFormatter(streaming=True, color=False)
        buf = StringIO()
        sys.stdout = buf
        try:
            fmt.feed_line("{not valid json")
            fmt.feed_line("")
            fmt.feed_line("plain text line")
        finally:
            sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertIn("{not valid json", output)
        self.assertIn("plain text line", output)

    def test_empty_lines_ignored(self) -> None:
        """Empty and whitespace-only lines are skipped."""
        fmt = ClaudeStreamJsonFormatter(streaming=True, color=False)
        buf = StringIO()
        sys.stdout = buf
        try:
            fmt.feed_line("")
            fmt.feed_line("   ")
            fmt.feed_line("\t\n")
        finally:
            sys.stdout = sys.__stdout__
        self.assertEqual(buf.getvalue(), "")

    def test_unknown_type_ignored(self) -> None:
        """Unknown message types are silently ignored."""
        fmt = ClaudeStreamJsonFormatter(streaming=True, color=False)
        buf = StringIO()
        sys.stdout = buf
        try:
            fmt.feed_line(json.dumps({"type": "unknown_future_type", "data": "foo"}))
        finally:
            sys.stdout = sys.__stdout__
        self.assertEqual(buf.getvalue(), "")

    def test_finish_flushes_in_progress_text_block(self) -> None:
        """finish() outputs newline if text block was in progress."""
        fmt = ClaudeStreamJsonFormatter(streaming=True, color=False)
        buf = StringIO()
        sys.stdout = buf
        try:
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "content_block_start",
                        "content_block": {"type": "text"},
                    }
                )
            )
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "partial"},
                    }
                )
            )
            # No content_block_stop — finish should still flush
            fmt.finish()
        finally:
            sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertIn("partial", output)

    def test_long_tool_result_truncated(self) -> None:
        """Long tool results are truncated to 500 chars."""
        fmt = ClaudeStreamJsonFormatter(streaming=True, color=False)
        buf = StringIO()
        sys.stdout = buf
        try:
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_abc",
                                    "content": "x" * 1000,
                                }
                            ],
                        },
                    }
                )
            )
        finally:
            sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertIn("...", output)
        # Should not contain the full 1000 chars
        self.assertLess(len(output), 600)

    def test_color_enabled(self) -> None:
        """When color=True, output contains ANSI escape codes."""
        fmt = ClaudeStreamJsonFormatter(streaming=True, color=True)
        buf = StringIO()
        sys.stdout = buf
        try:
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "system",
                        "subtype": "init",
                        "session_id": "test",
                        "tools": [],
                        "model": "test",
                    }
                )
            )
        finally:
            sys.stdout = sys.__stdout__
        output = buf.getvalue()
        self.assertIn("\x1b[", output)

    def test_result_error_flag(self) -> None:
        """Result with is_error shows FAILED."""
        fmt = ClaudeStreamJsonFormatter(streaming=True, color=False)
        buf_err = StringIO()
        sys.stderr = buf_err
        try:
            fmt.feed_line(
                json.dumps(
                    {
                        "type": "result",
                        "is_error": True,
                        "num_turns": 1,
                    }
                )
            )
            fmt.finish()
        finally:
            sys.stderr = sys.__stderr__
        output = buf_err.getvalue()
        self.assertIn("FAILED", output)


class AutoDetectFormatterTests(unittest.TestCase):
    """Tests for auto_detect_formatter factory."""

    def test_run_mode_returns_claude_formatter(self) -> None:
        fmt = auto_detect_formatter("run")
        self.assertIsInstance(fmt, ClaudeStreamJsonFormatter)

    def test_cli_mode_returns_plain_text(self) -> None:
        fmt = auto_detect_formatter("cli")
        self.assertIsInstance(fmt, PlainTextFormatter)

    def test_web_mode_returns_plain_text(self) -> None:
        fmt = auto_detect_formatter("web")
        self.assertIsInstance(fmt, PlainTextFormatter)

    def test_none_mode_returns_plain_text(self) -> None:
        fmt = auto_detect_formatter(None)
        self.assertIsInstance(fmt, PlainTextFormatter)

    def test_streaming_parameter_passed_through(self) -> None:
        fmt = auto_detect_formatter("run", streaming=False)
        self.assertIsInstance(fmt, ClaudeStreamJsonFormatter)
        self.assertFalse(fmt._streaming)

    def test_color_parameter_passed_through(self) -> None:
        fmt = auto_detect_formatter("run", color=True)
        self.assertIsInstance(fmt, ClaudeStreamJsonFormatter)
        self.assertTrue(fmt._color)
