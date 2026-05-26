# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the structurally-mounted gate commands.

The gate now lives inside each container's supervisor — there is no host
daemon to install/start/stop.  The only remaining sandbox-provided verb
is read-only: ``gate path <project>`` prints the ``file://`` URL of the
project's bare mirror.
"""

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

    args = parser.parse_args(["gate", "path", "demo"])
    assert args._cmd.name == "path"


def test_gate_path_dispatches(capsys) -> None:
    """``gate path`` invokes the handler and prints a ``file://`` mirror URL."""
    parser = argparse.ArgumentParser()
    _wire_gate(parser)

    args = parser.parse_args(["gate", "path", "demo"])
    CommandTree.dispatch(args)
    out = capsys.readouterr().out
    assert out.startswith("file://")
    assert out.strip().endswith("demo.git")


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
