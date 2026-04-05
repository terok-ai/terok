# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for D-Bus CLI commands (registry-driven dispatch)."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from terok.cli.commands.dbus import dispatch, register


@pytest.fixture()
def dbus_parser() -> argparse.ArgumentParser:
    """Return an argument parser with the dbus subcommands registered."""
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="cmd"))
    return parser


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        pytest.param(
            ["dbus", "notify", "Hello"],
            {"dbus_cmd": "notify", "summary": "Hello", "body": "", "timeout": -1},
            id="notify-summary-only",
        ),
        pytest.param(
            ["dbus", "notify", "Hello", "World"],
            {"dbus_cmd": "notify", "summary": "Hello", "body": "World", "timeout": -1},
            id="notify-with-body",
        ),
        pytest.param(
            ["dbus", "notify", "Hello", "-t", "5000"],
            {"dbus_cmd": "notify", "summary": "Hello", "body": "", "timeout": 5000},
            id="notify-short-timeout",
        ),
        pytest.param(
            ["dbus", "notify", "Hello", "--timeout", "3000"],
            {"dbus_cmd": "notify", "summary": "Hello", "body": "", "timeout": 3000},
            id="notify-long-timeout",
        ),
        pytest.param(
            ["dbus", "subscribe"],
            {"dbus_cmd": "subscribe"},
            id="subscribe",
        ),
    ],
)
def test_register_parses_dbus_subcommands(
    dbus_parser: argparse.ArgumentParser,
    argv: list[str],
    expected: dict[str, object],
) -> None:
    """Registered dbus subcommands parse the expected argument shapes."""
    args = dbus_parser.parse_args(argv)
    for key, value in expected.items():
        assert getattr(args, key) == value


def test_dispatch_returns_false_for_non_dbus_commands() -> None:
    """Dispatch ignores non-dbus CLI namespaces."""
    assert not dispatch(argparse.Namespace(cmd="project"))


@patch("terok.cli.commands.dbus.asyncio.run")
def test_dispatch_notify(mock_run: MagicMock) -> None:
    """``dbus notify`` dispatches to the notify handler with correct kwargs."""
    args = argparse.Namespace(
        cmd="dbus", dbus_cmd="notify", summary="Test", body="Body", timeout=5000
    )
    assert dispatch(args)
    mock_run.assert_called_once()
    coro = mock_run.call_args[0][0]
    # Coroutine was created from the notify handler
    assert coro.cr_code.co_qualname == "_handle_notify"
    coro.close()


@patch("terok.cli.commands.dbus.asyncio.run")
def test_dispatch_subscribe(mock_run: MagicMock) -> None:
    """``dbus subscribe`` dispatches to the subscribe handler."""
    args = argparse.Namespace(cmd="dbus", dbus_cmd="subscribe")
    assert dispatch(args)
    mock_run.assert_called_once()
    coro = mock_run.call_args[0][0]
    assert coro.cr_code.co_qualname == "_handle_subscribe"
    coro.close()


def test_dispatch_unknown_subcommand_returns_false() -> None:
    """Unknown dbus subcommand returns False (not handled)."""
    assert not dispatch(argparse.Namespace(cmd="dbus", dbus_cmd="nonexistent"))
