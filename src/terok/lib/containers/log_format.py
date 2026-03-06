# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Agent log formatters for structured container log output.

Provides pluggable formatters that transform raw container logs (e.g. Claude
stream-json NDJSON) into human-readable, color-coded terminal output.

The ``AgentLogFormatter`` protocol defines the interface: call ``feed_line()``
for each log line, and ``finish()`` at the end for any summary output.
"""

from __future__ import annotations

import json
import sys
from enum import Enum, auto
from typing import Protocol

from ..util.ansi import blue, green, red, supports_color, yellow

# ---------------------------------------------------------------------------
# Formatter protocol
# ---------------------------------------------------------------------------


class AgentLogFormatter(Protocol):
    """Interface for agent log formatters."""

    def feed_line(self, line: str) -> None:
        """Process one line of log output."""
        ...

    def finish(self) -> None:
        """Called after all lines have been fed. Print any summary."""
        ...


# ---------------------------------------------------------------------------
# Plain text (pass-through)
# ---------------------------------------------------------------------------


class PlainTextFormatter:
    """Pass-through formatter that prints lines unchanged."""

    def feed_line(self, line: str) -> None:
        """Print *line* as-is."""
        print(line, flush=True)

    def finish(self) -> None:
        """No-op; plain text has no summary."""
        pass


# ---------------------------------------------------------------------------
# Claude stream-json formatter
# ---------------------------------------------------------------------------


class _StreamState(Enum):
    """State machine states for Claude stream-json processing."""

    IDLE = auto()
    TEXT_BLOCK = auto()
    TOOL_USE_BLOCK = auto()


class ClaudeStreamJsonFormatter:
    """Formats Claude stream-json NDJSON into colored terminal output.

    When *streaming* is True, processes ``content_block_start/delta/stop``
    events for a typewriter effect on assistant text.  When False, only
    processes coalesced messages (``assistant``, ``result``, ``system``).

    Args:
        streaming: Enable partial streaming (typewriter text deltas).
        color: Enable ANSI colors (auto-detected from terminal if None).
    """

    def __init__(self, *, streaming: bool = True, color: bool | None = None) -> None:
        """Initialise formatter with streaming and color preferences."""
        self._streaming = streaming
        self._color = color if color is not None else supports_color()
        self._state = _StreamState.IDLE
        self._tool_input_buf: list[str] = []
        self._current_tool_name: str = ""
        # Accumulated result for finish() summary
        self._result: dict | None = None

    # -- helpers --

    def _blue(self, text: str) -> str:
        """Wrap *text* in blue ANSI if color is enabled."""
        return blue(text, self._color)

    def _yellow(self, text: str) -> str:
        """Wrap *text* in yellow ANSI if color is enabled."""
        return yellow(text, self._color)

    def _green(self, text: str) -> str:
        """Wrap *text* in green ANSI if color is enabled."""
        return green(text, self._color)

    def _red(self, text: str) -> str:
        """Wrap *text* in red ANSI if color is enabled."""
        return red(text, self._color)

    # -- line processing --

    def feed_line(self, line: str) -> None:
        """Parse a single NDJSON log line and print formatted output."""
        if not line.strip():
            return
        stripped = line.strip()
        try:
            data = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            # Not JSON — print as plain text, preserving leading whitespace
            print(line.rstrip("\r\n"), flush=True)
            return

        msg_type = data.get("type", "")

        if msg_type == "system":
            self._handle_system(data)
        elif msg_type == "assistant":
            self._handle_assistant(data)
        elif msg_type == "user":
            self._handle_user(data)
        elif msg_type == "result":
            self._handle_result(data)
        elif self._streaming and msg_type == "content_block_start":
            self._handle_block_start(data)
        elif self._streaming and msg_type == "content_block_delta":
            self._handle_block_delta(data)
        elif self._streaming and msg_type == "content_block_stop":
            self._handle_block_stop(data)
        # Ignore unknown types silently

    def _handle_system(self, data: dict) -> None:
        """Handle system init messages."""
        subtype = data.get("subtype", "")
        if subtype == "init":
            session_id = data.get("session_id", "")
            tools = data.get("tools", [])
            model = data.get("model", "")
            parts = [f"Session: {session_id}"] if session_id else []
            if model:
                parts.append(f"model={model}")
            if tools:
                parts.append(f"{len(tools)} tools available")
            if parts:
                print(self._blue(f"[system] {', '.join(parts)}"), flush=True)

    def _handle_assistant(self, data: dict) -> None:
        """Handle coalesced assistant messages (used in non-streaming mode)."""
        message = data.get("message", {})
        content = message.get("content", [])
        for block in content:
            block_type = block.get("type", "")
            if block_type == "text":
                text = block.get("text", "")
                if text.strip():
                    print(text, flush=True)
            elif block_type == "tool_use":
                name = block.get("name", "unknown")
                tool_input = block.get("input", {})
                print(self._blue(f"[tool] {name}"), flush=True)
                self._print_tool_input(tool_input)

    def _handle_user(self, data: dict) -> None:
        """Handle user messages (typically tool results)."""
        message = data.get("message", {})
        content = message.get("content", [])
        for block in content:
            block_type = block.get("type", "")
            if block_type == "tool_result":
                tool_id = block.get("tool_use_id", "")
                result_content = block.get("content", "")
                is_error = block.get("is_error", False)
                label = "[tool_result]" if not is_error else "[tool_error]"
                color_fn = self._green if not is_error else self._red
                if isinstance(result_content, str):
                    text = result_content
                elif isinstance(result_content, list):
                    text = " ".join(
                        b.get("text", "") for b in result_content if b.get("type") == "text"
                    )
                else:
                    text = str(result_content)
                # Truncate long results for readability
                if len(text) > 500:
                    text = text[:497] + "..."
                if tool_id:
                    print(color_fn(f"{label} ({tool_id[:8]}...)"), flush=True)
                else:
                    print(color_fn(label), flush=True)
                if text.strip():
                    print(f"  {text}", flush=True)

    def _handle_result(self, data: dict) -> None:
        """Handle final result message (cost, summary)."""
        self._result = data

    # -- streaming event handlers --

    def _handle_block_start(self, data: dict) -> None:
        """Begin a new text or tool-use streaming block."""
        content_block = data.get("content_block", {})
        block_type = content_block.get("type", "")
        if block_type == "text":
            self._state = _StreamState.TEXT_BLOCK
        elif block_type == "tool_use":
            self._state = _StreamState.TOOL_USE_BLOCK
            self._current_tool_name = content_block.get("name", "unknown")
            self._tool_input_buf.clear()
            print(self._blue(f"[tool] {self._current_tool_name}"), flush=True)

    def _handle_block_delta(self, data: dict) -> None:
        """Process an incremental text or tool-input delta."""
        delta = data.get("delta", {})
        delta_type = delta.get("type", "")
        if self._state == _StreamState.TEXT_BLOCK and delta_type == "text_delta":
            text = delta.get("text", "")
            if text:
                print(text, end="", flush=True)
        elif self._state == _StreamState.TOOL_USE_BLOCK and delta_type == "input_json_delta":
            partial = delta.get("partial_json", "")
            if partial:
                self._tool_input_buf.append(partial)

    def _handle_block_stop(self, _data: dict) -> None:
        """Finalise the current streaming block and flush output."""
        if self._state == _StreamState.TEXT_BLOCK:
            print(flush=True)  # newline after streamed text
        elif self._state == _StreamState.TOOL_USE_BLOCK:
            accumulated = "".join(self._tool_input_buf)
            if accumulated:
                try:
                    parsed = json.loads(accumulated)
                    self._print_tool_input(parsed)
                except (json.JSONDecodeError, ValueError):
                    print(self._yellow(f"  {accumulated}"), flush=True)
            self._tool_input_buf.clear()
        self._state = _StreamState.IDLE

    # -- tool input formatting --

    def _print_tool_input(self, tool_input: dict | str) -> None:
        """Print tool input key-value pairs, truncating long values."""
        if isinstance(tool_input, dict):
            for k, v in tool_input.items():
                val_str = str(v)
                if len(val_str) > 200:
                    val_str = val_str[:197] + "..."
                print(self._yellow(f"  {k}: {val_str}"), flush=True)
        elif tool_input:
            print(self._yellow(f"  {tool_input}"), flush=True)

    # -- finish --

    def finish(self) -> None:
        """Flush pending output and print the result summary if available."""
        # Flush any in-progress streaming block
        if self._state == _StreamState.TEXT_BLOCK:
            print(flush=True)
        elif self._state == _StreamState.TOOL_USE_BLOCK:
            accumulated = "".join(self._tool_input_buf)
            if accumulated:
                print(self._yellow(f"  {accumulated}"), flush=True)
        self._state = _StreamState.IDLE

        if self._result:
            self._print_result_summary()

    def _print_result_summary(self) -> None:
        """Print cost, duration, and token usage from the result message."""
        data = self._result
        if not data:
            return
        # Extract cost/usage from result
        cost_usd = data.get("cost_usd")
        duration_ms = data.get("duration_ms")
        is_error = data.get("is_error", False)
        num_turns = data.get("num_turns")
        usage = data.get("usage", {})

        parts: list[str] = []
        if is_error:
            parts.append(self._red("FAILED"))
        if num_turns is not None:
            parts.append(f"turns={num_turns}")
        if cost_usd is not None:
            parts.append(f"cost=${cost_usd:.4f}")
        if duration_ms is not None:
            secs = duration_ms / 1000
            parts.append(f"duration={secs:.1f}s")
        if usage:
            inp = usage.get("input_tokens", 0)
            out = usage.get("output_tokens", 0)
            if inp or out:
                parts.append(f"tokens={inp}in/{out}out")

        if parts:
            summary = ", ".join(parts)
            print(file=sys.stderr)
            print(self._yellow(f"[result] {summary}"), file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def auto_detect_formatter(
    mode: str | None,
    *,
    streaming: bool = True,
    color: bool | None = None,
    provider: str | None = None,
) -> AgentLogFormatter:
    """Return the appropriate formatter for a task's mode and provider.

    Args:
        mode: Task mode (``"run"`` for headless/autopilot, ``"cli"``, ``"web"``).
        streaming: Enable partial streaming for supported formatters.
        color: Force color on/off. ``None`` auto-detects from terminal.
        provider: Headless provider name.  When mode is ``"run"`` and
            provider is ``"claude"`` (or ``None``), returns the Claude
            stream-json formatter.  Other providers get plain text.
    """
    if mode == "run":
        effective_provider = provider or "claude"
        if effective_provider == "claude":
            return ClaudeStreamJsonFormatter(streaming=streaming, color=color)
        return PlainTextFormatter()
    return PlainTextFormatter()
