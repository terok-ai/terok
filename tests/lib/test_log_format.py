# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for log_format module (agent log formatters)."""

import json
from collections.abc import Iterable
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from typing import Any

import pytest

from terok.lib.containers.log_format import (
    ClaudeStreamJsonFormatter,
    PlainTextFormatter,
    auto_detect_formatter,
)


def make_formatter(*, streaming: bool = True, color: bool = False) -> ClaudeStreamJsonFormatter:
    """Build a Claude stream formatter with sensible defaults for tests."""
    return ClaudeStreamJsonFormatter(streaming=streaming, color=color)


def render_formatter(
    formatter: PlainTextFormatter | ClaudeStreamJsonFormatter,
    *lines: str | dict[str, Any],
    finish: bool = False,
) -> tuple[str, str]:
    """Feed lines into a formatter and capture ``stdout``/``stderr``."""
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        for line in lines:
            formatter.feed_line(json.dumps(line) if isinstance(line, dict) else line)
        if finish:
            formatter.finish()
    return stdout.getvalue(), stderr.getvalue()


def assert_contains_all(text: str, parts: Iterable[str]) -> None:
    """Assert that each expected fragment appears in ``text``."""
    for part in parts:
        assert part in text


SYSTEM_INIT_EVENT = {
    "type": "system",
    "subtype": "init",
    "session_id": "abc123",
    "model": "claude-sonnet-4-20250514",
    "tools": [{"name": "Read"}, {"name": "Write"}],
}

TOOL_RESULT_BASE = {
    "type": "user",
    "message": {
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_01234567",
            }
        ],
    },
}


class TestPlainTextFormatter:
    """Tests for PlainTextFormatter."""

    def test_passthrough(self) -> None:
        output, error = render_formatter(
            PlainTextFormatter(),
            "hello world",
            "line two",
            finish=True,
        )
        assert output == "hello world\nline two\n"
        assert error == ""

    def test_finish_is_noop(self) -> None:
        output, error = render_formatter(PlainTextFormatter(), finish=True)
        assert output == ""
        assert error == ""


class TestClaudeStreamJsonFormatterStreaming:
    """Tests for ClaudeStreamJsonFormatter with streaming=True."""

    def test_system_init(self) -> None:
        output, _error = render_formatter(make_formatter(), SYSTEM_INIT_EVENT)
        assert_contains_all(output, ("[system]", "abc123", "2 tools available"))

    def test_streaming_text_block(self) -> None:
        """Streaming text delta produces typewriter output."""
        output, _error = render_formatter(
            make_formatter(),
            {"type": "content_block_start", "content_block": {"type": "text"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}},
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": " world"},
            },
            {"type": "content_block_stop"},
        )
        assert_contains_all(output, ("Hello", " world"))

    def test_streaming_tool_use_block(self) -> None:
        """Streaming tool_use block accumulates input, prints on stop."""
        output, _error = render_formatter(
            make_formatter(),
            {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Read"},
            },
            {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": '{"file_pa'},
            },
            {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": 'th": "/foo"}'},
            },
            {"type": "content_block_stop"},
        )
        assert_contains_all(output, ("[tool] Read", "file_path", "/foo"))

    def test_result_captured_for_finish(self) -> None:
        """Result message is captured and printed on finish()."""
        _output, error = render_formatter(
            make_formatter(),
            {
                "type": "result",
                "cost_usd": 0.0123,
                "duration_ms": 5000,
                "num_turns": 3,
                "usage": {"input_tokens": 1000, "output_tokens": 500},
            },
            finish=True,
        )
        assert_contains_all(
            error,
            ("[result]", "turns=3", "cost=$0.0123", "duration=5.0s", "tokens=1000in/500out"),
        )

    def test_assistant_coalesced_message(self) -> None:
        """Even in streaming mode, coalesced assistant messages are handled."""
        output, _error = render_formatter(
            make_formatter(),
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "I'll fix the bug."},
                        {"type": "tool_use", "name": "Edit", "input": {"file": "a.py"}},
                    ],
                },
            },
        )
        assert_contains_all(output, ("I'll fix the bug.", "[tool] Edit", "file"))

    @pytest.mark.parametrize(
        ("tool_result", "expected"),
        [
            (
                {"content": "File contents here"},
                ("[tool_result]", "File contents here"),
            ),
            (
                {"content": "Permission denied", "is_error": True},
                ("[tool_error]", "Permission denied"),
            ),
        ],
        ids=["success", "error"],
    )
    def test_user_tool_result_variants(
        self, tool_result: dict[str, Any], expected: tuple[str, str]
    ) -> None:
        """User tool_result messages are rendered with the expected label."""
        payload = json.loads(json.dumps(TOOL_RESULT_BASE))
        payload["message"]["content"][0].update(tool_result)
        output, _error = render_formatter(make_formatter(), payload)
        assert_contains_all(output, expected)


class TestClaudeStreamJsonFormatterNoStream:
    """Tests for ClaudeStreamJsonFormatter with streaming=False."""

    def test_stream_events_skipped(self) -> None:
        """With streaming=False, content_block events are ignored."""
        output, _error = render_formatter(
            make_formatter(streaming=False),
            {"type": "content_block_start", "content_block": {"type": "text"}},
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "should not appear"},
            },
            {"type": "content_block_stop"},
        )
        assert output == ""

    def test_coalesced_messages_still_work(self) -> None:
        """With streaming=False, coalesced assistant messages still appear."""
        output, _error = render_formatter(
            make_formatter(streaming=False),
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "coalesced output"}],
                },
            },
        )
        assert "coalesced output" in output


class TestClaudeStreamJsonFormatterEdgeCase:
    """Edge case tests for ClaudeStreamJsonFormatter."""

    def test_malformed_json_not_crash(self) -> None:
        """Malformed JSON lines are printed as plain text."""
        output, _error = render_formatter(
            make_formatter(),
            "{not valid json",
            "",
            "plain text line",
        )
        assert_contains_all(output, ("{not valid json", "plain text line"))

    @pytest.mark.parametrize(
        "line",
        ["", "   ", "\t\n", json.dumps({"type": "unknown_future_type", "data": "foo"})],
        ids=["empty", "whitespace", "tab-newline", "unknown-type"],
    )
    def test_non_rendering_lines_are_ignored(self, line: str) -> None:
        """Empty/whitespace and unknown event types produce no output."""
        output, error = render_formatter(make_formatter(), line)
        assert output == ""
        assert error == ""

    def test_finish_flushes_in_progress_text_block(self) -> None:
        """finish() outputs newline if text block was in progress."""
        output, _error = render_formatter(
            make_formatter(),
            {"type": "content_block_start", "content_block": {"type": "text"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "partial"}},
            finish=True,
        )
        assert "partial" in output

    def test_long_tool_result_truncated(self) -> None:
        """Long tool results are truncated to 500 chars."""
        payload = json.loads(json.dumps(TOOL_RESULT_BASE))
        payload["message"]["content"][0]["content"] = "x" * 1000
        output, _error = render_formatter(make_formatter(), payload)
        assert "..." in output
        assert len(output) < 600

    def test_color_enabled(self) -> None:
        """When color=True, output contains ANSI escape codes."""
        output, _error = render_formatter(
            make_formatter(color=True),
            {
                "type": "system",
                "subtype": "init",
                "session_id": "test",
                "tools": [],
                "model": "test",
            },
        )
        assert "\x1b[" in output

    def test_result_error_flag(self) -> None:
        """Result with is_error shows FAILED."""
        _output, error = render_formatter(
            make_formatter(),
            {"type": "result", "is_error": True, "num_turns": 1},
            finish=True,
        )
        assert "FAILED" in error


class TestAutoDetectFormatter:
    """Tests for auto_detect_formatter factory."""

    def test_run_mode_returns_claude_formatter(self) -> None:
        assert isinstance(auto_detect_formatter("run"), ClaudeStreamJsonFormatter)

    @pytest.mark.parametrize("mode", ["cli", "web", None], ids=["cli", "web", "none"])
    def test_plain_text_modes(self, mode: str | None) -> None:
        """Non-run modes return the plain-text formatter."""
        assert isinstance(auto_detect_formatter(mode), PlainTextFormatter)

    def test_streaming_parameter_passed_through(self) -> None:
        fmt = auto_detect_formatter("run", streaming=False)
        assert isinstance(fmt, ClaudeStreamJsonFormatter)
        assert not fmt._streaming

    def test_color_parameter_passed_through(self) -> None:
        fmt = auto_detect_formatter("run", color=True)
        assert isinstance(fmt, ClaudeStreamJsonFormatter)
        assert fmt._color
