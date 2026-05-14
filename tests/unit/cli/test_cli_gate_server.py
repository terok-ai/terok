# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the structurally-mounted gate commands."""

from __future__ import annotations

import argparse

from terok_sandbox.commands import COMMANDS as SANDBOX_COMMANDS, CommandTree


def _wire_gate(parser: argparse.ArgumentParser) -> None:
    """Mount just sandbox's gate subtree into a fresh parser for assertion."""
    gate_node = SANDBOX_COMMANDS.find_at(("gate",))
    CommandTree((gate_node,)).wire(parser)


def test_gate_group_registered() -> None:
    """The gate group is mountable structurally; ``_cmd`` carries the leaf CommandDef."""
    parser = argparse.ArgumentParser()
    _wire_gate(parser)

    args = parser.parse_args(["gate", "status"])
    assert args._cmd.name == "status"


def test_gate_status_dispatches(capsys) -> None:
    """``gate status`` invokes the handler via [`CommandTree.dispatch`][terok_sandbox.commands.CommandTree.dispatch]."""
    parser = argparse.ArgumentParser()
    _wire_gate(parser)

    args = parser.parse_args(["gate", "status"])
    CommandTree.dispatch(args)
    out = capsys.readouterr().out
    assert "Mode:" in out
    assert "Running:" in out


def test_gate_group_help_shown_without_subcommand() -> None:
    """Invoking ``gate`` without a subcommand sets ``_group_help`` for main() to print."""
    parser = argparse.ArgumentParser()
    _wire_gate(parser)

    args = parser.parse_args(["gate"])
    # ``CommandTree.wire`` sets ``_group_help`` on group parsers so the
    # main() dispatcher can detect "group without a subcommand" and
    # print help — same convention as the legacy wire_group path.
    assert hasattr(args, "_group_help")
    assert not hasattr(args, "_cmd")
