# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task-controlled display data never acquires the terminal's control channel."""

import argparse
import json
from collections.abc import Sequence
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from terok.cli.commands import task as task_cli
from terok.lib.core.projects import load_project
from terok.lib.core.work_status import read_work_status, write_work_status
from terok.lib.domain.log_format import ClaudeStreamJsonFormatter, PlainTextFormatter
from terok.lib.domain.task_logs import LogViewOptions, _show_persisted_logs, task_logs
from terok.lib.orchestration.tasks import (
    load_task_meta,
    task_list,
    task_new,
    task_status,
    write_task_meta,
)
from terok.lib.util.check_reporter import CheckReporter
from tests.test_utils import mock_git_config, project_env

_HOSTILE = "before\x1b]52;c;cG9pc29u\x07\r\x9b2Jafter"
_HOSTILE_SAFE = "before\\x1b]52;c;cG9pc29u\\x07 \\x9b2Jafter"
_COLOURED = "\x1b[32mok\x1b[0m done"
_PROJECT = "terminal-output"


@pytest.mark.parametrize("listing", [False, True])
def test_status_rendering_does_not_modify_raw_telemetry(
    listing: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    """Status/list escape after decoding YAML, without changing stored telemetry."""
    with project_env(f"project:\n  id: {_PROJECT}\n", project_name=_PROJECT), mock_git_config():
        task_id = task_new(_PROJECT)
        config_dir = load_project(_PROJECT).tasks_root / task_id / "agent-config"
        write_work_status(config_dir, _HOSTILE, _HOSTILE)
        capsys.readouterr()
        with patch("terok.lib.orchestration.tasks.query.get_all_task_states", return_value={}):
            if listing:
                task_list(_PROJECT)
            else:
                task_status(_PROJECT, task_id)
        output = capsys.readouterr().out
        assert _HOSTILE_SAFE in output
        assert "\x1b" not in output
        assert read_work_status(config_dir).status == _HOSTILE


@pytest.mark.parametrize("grouped", [False, True])
def test_diagnostic_details_cannot_forge_report_lines(grouped: bool) -> None:
    """Both reporter paths neutralize task stderr, parser errors and embedded newlines."""
    stream = StringIO()
    reporter = CheckReporter(stream=stream, color=False)
    detail = f"{_HOSTILE}\nforged check: ok"
    if grouped:
        with reporter.group("Credential files") as group:
            group.add("warn", detail)
    else:
        reporter.emit("warn", "Credential file", detail)
    output = stream.getvalue()
    assert f"{_HOSTILE_SAFE} forged check: ok" in output
    assert "\x1b" not in output


@pytest.mark.parametrize(
    "events",
    [
        [_HOSTILE],
        [{"type": "system", "subtype": "init", "session_id": _HOSTILE, "model": _HOSTILE}],
        [{"type": "assistant", "message": {"content": [{"type": "text", "text": _HOSTILE}]}}],
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": _HOSTILE, "input": {_HOSTILE: _HOSTILE}}
                    ]
                },
            }
        ],
        [{"type": "assistant", "message": {"content": [{"type": "tool_use", "input": _HOSTILE}]}}],
        [
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": _HOSTILE, "content": _HOSTILE}
                    ]
                },
            }
        ],
        [
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "content": [{"type": "text", "text": _HOSTILE}]}
                    ]
                },
            }
        ],
        [{"type": "content_block_start", "content_block": {"type": "tool_use", "name": _HOSTILE}}],
        [
            {"type": "content_block_start", "content_block": {"type": "text"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "before\x1b"}},
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "]52;c;cG9pc29u\x07\r\x9b2Jafter"},
            },
        ],
        [
            {"type": "content_block_start", "content_block": {"type": "tool_use"}},
            {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": _HOSTILE},
            },
            {"type": "content_block_stop"},
        ],
        [
            {"type": "content_block_start", "content_block": {"type": "tool_use"}},
            {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": _HOSTILE},
            },
        ],
        [
            {
                "type": "result",
                "num_turns": _HOSTILE,
                "usage": {"input_tokens": _HOSTILE, "output_tokens": _HOSTILE},
            }
        ],
    ],
)
def test_decoded_log_fields_and_fragments_are_terminal_safe(
    events: Sequence[str | dict], capsys: pytest.CaptureFixture[str]
) -> None:
    """Every formatter field family escapes after JSON decoding, including split commands."""
    formatter = ClaudeStreamJsonFormatter(color=False)
    for event in events:
        formatter.feed_line(event if isinstance(event, str) else json.dumps(event))
    formatter.finish()
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "\\x1b" in output
    for control in ("\x1b", "\x07", "\r", "\x9b"):
        assert control not in output


@pytest.mark.parametrize(
    "event",
    [
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": [1, 2], "content": _HOSTILE}]
            },
        },
        {
            "type": "result",
            "num_turns": _HOSTILE,
            "cost_usd": _HOSTILE,
            "duration_ms": "abc",
            "usage": "nope",
        },
        {"type": "assistant", "message": {"content": "not a list"}},
        {"type": "assistant", "message": {"content": [5]}},
        {"type": "system", "subtype": "init", "tools": 7, "session_id": _HOSTILE},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": [_HOSTILE]}},
        [_HOSTILE],
    ],
    ids=[
        "list-id",
        "result-strings",
        "content-str",
        "content-int",
        "tools-int",
        "delta-list",
        "json-list",
    ],
)
def test_wrong_typed_event_fields_cannot_crash_the_viewer(
    event: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """A task may write any JSON; the viewer shows it escaped instead of dying."""
    formatter = ClaudeStreamJsonFormatter(color=False)
    formatter.feed_line(
        json.dumps({"type": "content_block_start", "content_block": {"type": "text"}})
    )
    formatter.feed_line(json.dumps(event))
    formatter.finish()
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "after" in output or json.dumps(event) in output
    for control in ("\x1b", "\x07", "\r", "\x9b"):
        assert control not in output


def test_plain_log_and_own_formatter_colors_remain_distinct(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Application colors remain active, but task-supplied OSC does not."""
    PlainTextFormatter(color=False).feed_line(_HOSTILE)
    assert capsys.readouterr().out == _HOSTILE_SAFE + "\n"
    formatter = ClaudeStreamJsonFormatter(color=True)
    formatter.feed_line(
        json.dumps(
            {"type": "content_block_start", "content_block": {"type": "tool_use", "name": _HOSTILE}}
        )
    )
    output = capsys.readouterr().out
    assert "\x1b[34m" in output
    assert _HOSTILE_SAFE in output
    assert "\x1b]52" not in output


@pytest.mark.parametrize("color", [False, True])
def test_agent_colour_survives_only_on_a_colour_terminal(
    color: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    """A legitimate agent's colours reach a terminal and never a redirected log."""
    PlainTextFormatter(color=color).feed_line(_COLOURED)
    assert capsys.readouterr().out == (f"{_COLOURED}\x1b[0m\n" if color else "ok done\n")


@pytest.mark.parametrize("raw", [False, True])
def test_archive_view_is_safe_unless_raw_is_explicit(
    tmp_path: Path, raw: bool, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Archive bytes remain unchanged; only explicit raw viewing replays terminal controls."""
    monkeypatch.setenv("NO_COLOR", "1")
    log = tmp_path / "archive.log"
    contents = f"first\n{_HOSTILE}\nlast\n"
    log.write_bytes(contents.encode())
    parser = argparse.ArgumentParser()
    task_cli.register(parser.add_subparsers(dest="cmd"))
    argv = ["task", "archive", "logs", _PROJECT, "archive"]
    args = parser.parse_args([*argv, "--raw"] if raw else argv)
    with patch.object(task_cli, "task_archive_logs", return_value=log):
        task_cli._dispatch_archive_sub(args)
    output = capsys.readouterr().out
    assert output == (contents if raw else f"first\n{_HOSTILE_SAFE}\nlast\n")
    assert log.read_bytes() == contents.encode()


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("tail", [None, 1, 0])
def test_persisted_logs_preserve_explicit_raw_replay(
    tmp_path: Path,
    raw: bool,
    tail: int | None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removed containers retain the same raw opt-in and tail behavior as live logs."""
    monkeypatch.setenv("NO_COLOR", "1")
    log = tmp_path / "container.log"
    contents = f"first\n{_HOSTILE}\nlast\n"
    log.write_bytes(contents.encode())

    _show_persisted_logs(log, raw=raw, tail=tail)

    expected = "" if tail == 0 else "last\n" if tail == 1 else contents
    if not raw:
        expected = expected.replace(_HOSTILE, _HOSTILE_SAFE)
    assert capsys.readouterr().out == expected


def test_task_logs_passes_raw_to_persisted_fallback(
    mock_runtime: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """Explicit raw replay survives container removal, without adding a final newline."""
    mock_runtime.container.return_value.state = None
    with project_env(f"project:\n  id: {_PROJECT}\n", project_name=_PROJECT), mock_git_config():
        task_id = task_new(_PROJECT)
        project = load_project(_PROJECT)
        meta, meta_file = load_task_meta(_PROJECT, task_id)
        write_task_meta(meta_file, {**meta, "mode": "run"})
        log = project.tasks_root / task_id / "logs" / "container.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_bytes(_HOSTILE.encode())
        capsys.readouterr()

        task_logs(_PROJECT, task_id, LogViewOptions(raw=True))

        assert capsys.readouterr().out == _HOSTILE


def test_failed_log_stream_escapes_captured_stderr(
    mock_runtime: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed log stream cannot replay terminal commands through its warning."""
    mock_runtime.container.return_value.state = "exited"
    with project_env(f"project:\n  id: {_PROJECT}\n", project_name=_PROJECT), mock_git_config():
        task_id = task_new(_PROJECT)
        meta, meta_file = load_task_meta(_PROJECT, task_id)
        write_task_meta(meta_file, {**meta, "mode": "run"})
        proc = MagicMock(stdout=BytesIO(), stderr=BytesIO(_HOSTILE.encode()), returncode=1)
        proc.poll.return_value = 1
        capsys.readouterr()

        with patch("terok.lib.domain.task_logs.AgentRunner") as runner:
            runner.return_value.stream_logs_process.return_value = proc
            task_logs(_PROJECT, task_id)

        output = capsys.readouterr().out
        assert _HOSTILE_SAFE in output
        assert "\x1b" not in output
