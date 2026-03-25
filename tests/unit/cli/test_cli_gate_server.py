# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the wire_group-mounted gate commands."""

from __future__ import annotations

from unittest.mock import patch

from terok.cli.wiring import wire_dispatch, wire_group


def test_gate_group_registered() -> None:
    """GATE_COMMANDS are mountable under the 'gate' prefix."""
    import argparse

    from terok_sandbox import GATE_COMMANDS

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    wire_group(sub, "gate", GATE_COMMANDS, help="Gate server commands")

    args = parser.parse_args(["gate", "status"])
    assert args.cmd == "gate"
    assert hasattr(args, "_wired_cmd")
    assert args._wired_cmd.name == "status"


def test_gate_status_dispatches(capsys) -> None:
    """'gate status' invokes the handler via wire_dispatch and produces output."""
    import argparse

    from terok_sandbox import GATE_COMMANDS

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    wire_group(sub, "gate", GATE_COMMANDS)

    args = parser.parse_args(["gate", "status"])
    handled = wire_dispatch(args)

    assert handled is True
    out = capsys.readouterr().out
    assert "Mode:" in out
    assert "Running:" in out


def test_gate_group_help_shown_without_subcommand() -> None:
    """Invoking 'gate' without a subcommand shows group help."""
    import argparse

    from terok_sandbox import GATE_COMMANDS

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    wire_group(sub, "gate", GATE_COMMANDS, help="Gate server commands")

    args = parser.parse_args(["gate"])
    with patch.object(args._group_help, "print_help") as mock_help:
        handled = wire_dispatch(args)

    assert handled is True
    mock_help.assert_called_once()
