# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for terok's CLI [`CommandTree`][terok_sandbox.commands.CommandTree] composition helpers."""

from __future__ import annotations

import argparse

from terok_sandbox.commands import ArgDef, CommandDef, CommandTree

from terok.cli.main import _commandtree_dispatch
from terok.cli.tree import inject_cfg_factory


class TestInjectCfgFactory:
    """Cfg-wrap overlay supplies a lazy [`cfg`][terok.cli.tree] at dispatch time."""

    def test_wraps_only_handlers_under_named_subtree(self) -> None:
        """Only handlers under the target prefix that take ``cfg`` get wrapped."""
        seen: dict[str, object] = {}

        def in_scope(*, cfg=None) -> None:
            seen["in_scope"] = cfg

        def out_of_scope(*, cfg=None) -> None:
            seen["out_of_scope"] = cfg

        def no_cfg() -> None:
            seen["no_cfg"] = "called"

        tree = CommandTree(
            (
                CommandDef(
                    name="group",
                    help="g",
                    children=(
                        CommandDef(name="needs", help="", handler=in_scope),
                        CommandDef(name="bare", help="", handler=no_cfg),
                    ),
                ),
                CommandDef(name="outside", help="", handler=out_of_scope),
            )
        )

        sentinel = object()

        wrapped = inject_cfg_factory(tree, subtree_paths=(("group",),), factory=lambda: sentinel)

        # Wired through argparse so dispatch + arg-extraction is exercised end-to-end.
        parser = argparse.ArgumentParser()
        wrapped.wire(parser)
        for argv, key in (
            (["group", "needs"], "in_scope"),
            (["group", "bare"], "no_cfg"),
            (["outside"], "out_of_scope"),
        ):
            CommandTree.dispatch(parser.parse_args(argv))
            assert key in seen

        # In-scope handler saw the factory's return value.
        assert seen["in_scope"] is sentinel
        # Out-of-scope handler was not wrapped — cfg is None.
        assert seen["out_of_scope"] is None
        # Bare handler (no ``cfg`` param) was untouched and ran.
        assert seen["no_cfg"] == "called"

    def test_explicit_cfg_kwarg_wins_over_factory(self) -> None:
        """``setdefault`` semantics: a caller-supplied ``cfg`` isn't overwritten."""
        seen: dict[str, object] = {}
        explicit = object()

        def handler(*, cfg=None) -> None:
            seen["cfg"] = cfg

        cmd = CommandDef(
            name="v",
            help="",
            handler=handler,
            args=(ArgDef(name="--cfg", default=None),),
        )
        tree = CommandTree((cmd,))
        wrapped = inject_cfg_factory(tree, subtree_paths=((),), factory=lambda: object())

        # Direct invocation simulates a programmatic caller passing cfg
        # explicitly; the wrapper uses ``setdefault`` so the caller's
        # value wins over the factory.
        wrapped.find_at(("v",)).handler(cfg=explicit)
        assert seen["cfg"] is explicit


class TestCommandtreeDispatch:
    """The terok-side dispatcher that integrates CommandTree into terok's chain."""

    def test_returns_false_when_no_cmd_attribute(self) -> None:
        """An argparse Namespace from a non-CommandTree branch is declined."""
        args = argparse.Namespace(cmd="some-legacy-verb")
        assert _commandtree_dispatch(args) is False

    def test_invokes_handler_and_returns_true(self) -> None:
        """A Namespace with ``_cmd`` set dispatches and returns True."""
        called: list[int] = []

        def handler() -> None:
            called.append(1)

        cmd = CommandDef(name="v", help="", handler=handler)
        ns = argparse.Namespace(_cmd=cmd)
        assert _commandtree_dispatch(ns) is True
        assert called == [1]
