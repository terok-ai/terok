#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""CLI entry point and argument parser for terok.

Subcommand registration and dispatch are delegated to focused modules
under ``commands/``.  This file owns only the root parser, version flag,
argcomplete integration, and top-level dispatch loop.
"""

import argparse
import sys

from terok.lib.integrations.sandbox import NoPassphraseError as _NoPassphraseError

from ..lib.core.config import set_experimental
from ..lib.core.version import format_version_string, get_version_info
from .commands import (
    acp,
    agents,
    auth,
    clearance,
    completions,
    dbus,
    image,
    info,
    panic,
    project,
    setup,
    shield,
    sickbay,
    task,
    uninstall,
)
from .tree import CommandDef, CommandTree, inject_cfg_factory

# Optional: bash completion via argcomplete
try:
    import argcomplete
except ImportError:  # pragma: no cover - optional dep
    argcomplete = None  # type: ignore[assignment]


def _commandtree_dispatch(args: argparse.Namespace) -> bool:
    """Dispatch verbs wired by [`CommandTree.wire`][terok_sandbox.commands.CommandTree.wire].

    Each leaf parser sets ``_cmd`` to its [`CommandDef`][terok_sandbox.commands.CommandDef];
    we hand off to [`CommandTree.dispatch`][terok_sandbox.commands.CommandTree.dispatch]
    which extracts kwargs from *args* and calls the (possibly cfg-wrapped)
    handler.  Returns ``True`` if a wired command was matched.
    """
    if not hasattr(args, "_cmd"):
        return False
    CommandTree.dispatch(args)
    return True


# Dispatch chain — tried in order; first True wins.
_DISPATCHERS = [
    panic.dispatch,
    setup.dispatch,
    uninstall.dispatch,
    auth.dispatch,
    project.dispatch,
    task.dispatch,
    image.dispatch,
    _commandtree_dispatch,
    shield.dispatch,
    dbus.dispatch,
    clearance.dispatch,
    sickbay.dispatch,
    info.dispatch,
    acp.dispatch,
    agents.dispatch,
    completions.dispatch,
]


def main(prog: str = "terok") -> None:
    """Parse CLI arguments and dispatch to the appropriate command handler.

    ``prog`` selects which surface this invocation presents:

    * ``"terok"`` — human-friendly entry point.  Bare ``terok`` in an
      interactive terminal execs ``terok-tui`` instead of printing the
      usage error.
    * ``"terokctl"`` — scriptable surface.  Always parses arguments, so
      no-args produces the argparse ``required subcommand`` error (stable,
      predictable exit code).  The command tree is identical today; the
      split exists so ``terok`` can evolve richer UX while ``terokctl``
      preserves backwards compatibility.
    """
    # Fast-path: bare ``terok`` in a terminal launches the TUI.  Scripts
    # piping ``terok`` get the argparse usage error instead — the TTY
    # check keeps the convenience shortcut from surprising automation.
    # If ``terok-tui`` isn't on PATH (partial install, exotic layout), fall
    # through to argparse so the user sees a usage error rather than a
    # traceback from ``execlp``.
    if prog == "terok" and len(sys.argv) == 1 and sys.stdin.isatty() and sys.stdout.isatty():
        import os

        try:
            os.execlp("terok-tui", "terok-tui")
            return  # type: ignore[unreachable]  # in tests os.execlp is mocked
        except FileNotFoundError:
            pass

    # Get version info for --version flag
    version, branch = get_version_info()
    version_string = format_version_string(version, branch)

    parser = argparse.ArgumentParser(
        prog=prog,
        description="terok – generate/build images and run per-project task containers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Quick start:\n"
            f"  1. Bootstrap:  {prog} setup                       (install host services)\n"
            f"  2. Auth:       {prog} auth claude                 (host-wide auth — no project needed)\n"
            f"  3. Project:    {prog} project wizard              (create a project)\n"
            f"  4. Work:       {prog} task run <project_id>       (attach into a new CLI task)\n"
            "\n"
            f"Bare {prog} auth opens an interactive menu for multiple providers.\n"
            "\n"
            "Standalone agent (no project):\n"
            f"  {prog} executor run claude .          (headless against cwd)\n"
            f"  {prog} executor run claude . -p 'fix' (with prompt)\n"
            "\n"
            f"Tip: enable tab completion with: {prog} completions install\n"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{prog} {version_string}\nLicense: Apache-2.0\nCopyright: 2025 Jiri Vyskocil",
    )
    parser.add_argument(
        "--experimental",
        action="store_true",
        default=False,
        help="Enable experimental features (e.g. web tasks)",
    )
    parser.add_argument(
        "--no-emoji",
        action="store_true",
        default=False,
        help="Replace emojis with text labels (e.g. [gate] instead of \U0001f6aa)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # Register subcommands.  Order matters — it's the order they appear in
    # ``--help``.  Emergency and bootstrap first, then the daily-workflow
    # verbs (auth → project → task → login), then operator tools, then
    # sibling-wired groups, then dev/shell niceties.
    panic.register(sub)
    setup.register(sub)
    uninstall.register(sub)
    auth.register(sub)
    project.register(sub)
    task.register(
        sub, prog=prog
    )  # task group + flat ``login`` shortcut; ``prog`` gates terokctl-only verbs
    image.register(sub)
    clearance.register(sub)
    sickbay.register(sub)
    shield.register(sub)
    agents.register(sub)
    info.register(sub)
    acp.register(sub)

    # Build the terok-side CommandTree from upstream packages, apply
    # terok's cfg-injection overlay (concept translation: terok's
    # SandboxConfig wins over sandbox's default), and surface every
    # sibling subtree both deeply (terok executor sandbox vault X) and
    # as top-level shortcuts (terok vault X) that share CommandDef
    # identity with the deep path.
    _build_wired_tree().wire(sub)

    # Dev / shell niceties at the bottom of the help listing.
    dbus.register(sub)
    completions.register(sub)

    # TUI launcher — only on the human-facing ``terok`` binary.  ``terokctl``
    # is the scripting surface; launching an interactive TUI from there is
    # never useful and just clutters the help listing.
    if prog == "terok":
        sub.add_parser("tui", help="Launch the Textual TUI")

    # Enable bash completion if argcomplete is present and activated
    if argcomplete is not None:  # pragma: no cover - shell integration
        try:
            argcomplete.autocomplete(parser)
        except (TypeError, AttributeError):
            pass

    # Fast-path: ``terok tui [args...]`` bypasses argparse and execs terok-tui.
    # This avoids argparse rejecting TUI-specific flags like --tmux.
    # Gated on prog="terok" so ``terokctl tui`` falls through to argparse
    # (which will error — tui is not registered on the scripting surface).
    if prog == "terok" and len(sys.argv) >= 2 and sys.argv[1] == "tui":
        import os

        os.execlp("terok-tui", "terok-tui", *sys.argv[2:])
        return  # type: ignore[unreachable]  # in tests os.execlp is mocked

    args = parser.parse_args()
    set_experimental(args.experimental)

    if args.no_emoji:
        from ..lib.util.emoji import set_emoji_enabled

        set_emoji_enabled(False)

    # Post-parse tui handler: covers ``terok --no-emoji tui ...`` where the
    # fast-path (argv[1] == "tui") doesn't fire because root flags come first.
    if getattr(args, "cmd", None) == "tui":
        import os

        os.execlp("terok-tui", "terok-tui", *sys.argv[sys.argv.index("tui") + 1 :])
        return  # type: ignore[unreachable]  # in tests os.execlp is mocked

    try:
        for dispatch in _DISPATCHERS:
            if dispatch(args):
                return
    except _NoPassphraseError as exc:
        # sandbox#278 stripped CLI-hint text from the library raise sites so
        # they stay diagnostic-only.  We're the operator-facing surface, so
        # the actionable hint lives here.
        print(
            f"error: {exc}\n"
            "hint:  run `terok vault unlock` to provision the vault passphrase for this session.",
            file=sys.stderr,
        )
        sys.exit(2)

    parser.error("Unknown command")


def _build_wired_tree() -> CommandTree:
    """Compose terok's sibling-wired command tree.

    Pulls executor's full [`CommandTree`][terok_sandbox.commands.CommandTree]
    (already containing executor's overlays + sandbox spliced under
    ``sandbox``), applies terok's
    [`SandboxConfig`][terok_sandbox.SandboxConfig] injection at every
    handler under the ``sandbox`` namespace that takes ``cfg``, and
    surfaces three views over the same modified subtrees:

    - ``terok executor <verb>``      — full executor deep path.
    - ``terok sandbox <verb>``       — shortcut for the sandbox subtree
      (same children as ``terok executor sandbox``).
    - ``terok {vault,gate,ssh} <verb>`` — shortcuts that reference the
      same [`CommandDef`][terok_sandbox.commands.CommandDef] instances
      living under ``sandbox`` so terok's cfg wrap propagates uniformly.
    """
    from terok.lib.integrations.executor import COMMANDS as EXECUTOR_COMMANDS

    from ..lib.core.config import make_sandbox_config

    # Executor exposes ``CommandTree`` (its top-level forest).  Apply
    # terok's cfg wrap to every handler under the ``sandbox`` namespace
    # that declares a ``cfg`` parameter — sandbox-rooted gate, ssh,
    # vault, credentials, etc. all funnel through ``make_sandbox_config``.
    modified = inject_cfg_factory(
        EXECUTOR_COMMANDS,
        subtree_paths=(("sandbox",),),
        factory=make_sandbox_config,
    )

    # The shortcut nodes share identity with their counterparts inside
    # the modified executor tree — both ``terok vault X`` and ``terok
    # executor sandbox vault X`` resolve to the same wrapped handler.
    sandbox_node = modified.find_at(("sandbox",))
    vault_node = modified.find_at(("sandbox", "vault"))
    gate_node = modified.find_at(("sandbox", "gate"))
    ssh_node = modified.find_at(("sandbox", "ssh"))

    return CommandTree(
        (
            CommandDef(
                name="executor",
                help="Task container executor commands (deep path; full executor tree)",
                children=modified.roots,
            ),
            CommandDef(
                name="sandbox",
                help="Sandbox subsystem (shortcut to terok executor sandbox)",
                children=sandbox_node.children,
            ),
            vault_node,
            gate_node,
            ssh_node,
        )
    )


def terokctl_main() -> None:
    """Entry point for the ``terokctl`` scriptable surface.

    Same command tree as ``terok``, but no-args prints the argparse
    usage error instead of launching the TUI — the stable, predictable
    behavior scripts and automation want.
    """
    main(prog="terokctl")


if __name__ == "__main__":
    main()
