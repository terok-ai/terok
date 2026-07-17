#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""CLI entry point and argument parser for terok.

Subcommand registration and dispatch are delegated to focused modules
under ``commands/``.  This file owns only the root parser, version flag,
argcomplete integration, and top-level dispatch loop.
"""

import argparse
import os
import sys
from typing import Any

from ..lib.core.version import format_version_string, get_version_info
from .tree import CommandDef, CommandTree, inject_cfg_factory, inject_pt_resolver

# Optional: bash completion via argcomplete
try:
    import argcomplete
except ImportError:  # pragma: no cover - optional dep
    argcomplete = None  # type: ignore[assignment]


#: Top-level command groups contributed by the sibling-wired tree — the
#: executor passthrough plus the ``sandbox`` / ``vault`` / ``gate`` / ``ssh``
#: shortcuts and the clearance-backed ``dbus`` group.  Single source of truth
#: for both [`_build_wired_tree`][terok.cli.main._build_wired_tree] (which
#: checks the tree it builds matches this set) and the gate predicate
#: [`_invocation_needs_wired_tree`][terok.cli.main._invocation_needs_wired_tree].
#: Building the tree imports the executor / sandbox / clearance wheels, so a
#: terok-own verb (``info``, ``task``, …) skips it entirely.
_WIRED_TREE_ROOTS = frozenset({"executor", "sandbox", "vault", "gate", "ssh", "dbus"})


def _first_verb(argv: list[str]) -> str | None:
    """Return the first positional token in *argv*, or a help/None sentinel.

    Root options (``--experimental`` / ``--no-emoji``) are valueless flags,
    so the first token that isn't an option *is* the subcommand.  A ``-h`` /
    ``--help`` anywhere ahead of a verb is reported as ``"--help"`` so the
    caller keeps the full command surface for the top-level listing.
    """
    for tok in argv:
        if tok in ("-h", "--help"):
            return "--help"
        if not tok.startswith("-"):
            return tok
    return None


def _invocation_needs_wired_tree(argv: list[str]) -> bool:
    """Whether *argv* targets a sibling-wired group and must build the tree.

    The wired tree is expensive — building it imports terok-executor,
    terok-sandbox, and terok-clearance.  A terok-own verb never needs it,
    so we skip it.  The full tree is still built for shell completion, the
    no-verb case, and ``--help`` / ``-h``, where the complete command
    listing must be visible.
    """
    # Shell completion lists every command, so it needs the whole tree.
    if os.environ.get("_ARGCOMPLETE"):
        return True
    verb = _first_verb(argv)
    if verb is None or verb == "--help":
        return True
    return verb in _WIRED_TREE_ROOTS


#: Own top-level verb -> the command module (basename under ``.commands``) that
#: registers and handles it.  Some modules own several verbs (``info`` →
#: ``config``, ``task`` → ``task`` + ``login``).  Kept static so resolving the
#: invoked verb imports exactly one command module rather than all fourteen;
#: ``test_own_verb_registry_matches_registration`` cross-checks it against what
#: the modules actually register so it can't silently drift.
_OWN_VERB_MODULES: dict[str, str] = {
    "panic": "panic",
    "setup": "setup",
    "uninstall": "uninstall",
    "auth": "auth",
    "project": "project",
    "task": "task",
    "login": "task",
    "image": "image",
    "clearance": "clearance",
    "sickbay": "sickbay",
    "shield": "shield",
    "agents": "agents",
    "config": "info",
    "acp": "acp",
    "completions": "completions",
}

#: Command modules in ``--help`` listing order, for the full-surface cases
#: (bare invocation, ``--help``, unknown verb, shell completion).
_OWN_MODULE_ORDER: tuple[str, ...] = (
    "panic",
    "setup",
    "uninstall",
    "auth",
    "project",
    "task",
    "image",
    "clearance",
    "sickbay",
    "shield",
    "agents",
    "info",
    "acp",
    "completions",
)


def _needs_full_own_surface(argv: list[str]) -> bool:
    """Whether every own command must be registered (not just the invoked one).

    The full surface is needed when the top-level command listing must be
    complete: shell completion, a bare invocation, ``--help`` / ``-h`` ahead of
    any verb, or an unknown verb (so argparse can error against the full choice
    list).  A recognised own verb or a sibling-wired verb needs only its own
    module (or none) — the whole point of lazy dispatch.
    """
    if os.environ.get("_ARGCOMPLETE"):
        return True
    verb = _first_verb(argv)
    if verb is None or verb == "--help":
        return True
    return verb not in _OWN_VERB_MODULES and verb not in _WIRED_TREE_ROOTS and verb != "tui"


def _load_own_commands(argv: list[str]) -> list[Any]:
    """Import the command modules needed for *argv*, in registration order.

    Fast path: a recognised own verb imports exactly that verb's module; a
    sibling-wired verb (or ``tui``) imports none.  The full-surface cases import
    every own module.  Returned modules are registered and their ``dispatch``
    hooks wired, in order.
    """
    import importlib

    if _needs_full_own_surface(argv):
        names: list[str] = list(_OWN_MODULE_ORDER)
    else:
        verb = _first_verb(argv)
        module = _OWN_VERB_MODULES.get(verb) if verb is not None else None
        names = [module] if module is not None else []
    return [importlib.import_module(f"{__package__}.commands.{name}") for name in names]


def _commandtree_dispatch(args: argparse.Namespace) -> bool:
    """Dispatch verbs wired by [`CommandTree.wire`][terok_util.cli_types.CommandTree.wire].

    Each leaf parser sets ``_cmd`` to its [`CommandDef`][terok_util.cli_types.CommandDef];
    we hand off to [`CommandTree.dispatch`][terok_util.cli_types.CommandTree.dispatch]
    which extracts kwargs from *args* and calls the (possibly cfg-wrapped)
    handler.  Returns ``True`` if a wired command was matched.
    """
    if not hasattr(args, "_cmd"):
        return False
    CommandTree.dispatch(args)
    return True


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
    # The config helpers are imported here, at dispatch time, rather than at
    # module scope: they pull ``core.config`` (and thus the sandbox wheel), so
    # importing them eagerly would make a bare ``import terok.cli`` pay for the
    # stack.  Command modules are loaded lazily too — only the invoked verb's
    # module (see ``_load_own_commands``) — so ``terok <verb>`` imports one
    # command module instead of all fourteen.
    from terok.lib.core.config import declare_setup_invocation, set_experimental

    declare_setup_invocation()
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

    # Fast-path: ``terok --tmux [args...]`` is a shortcut for ``terok tui
    # --tmux [args...]`` — exec before any parser or subcommand registration
    # work.  A missing ``terok-tui`` falls through to argparse like the bare
    # invocation above.
    if prog == "terok" and sys.argv[1:2] == ["--tmux"]:
        import os

        try:
            os.execlp("terok-tui", "terok-tui", *sys.argv[1:])  # nosec B606 B607 — PATH lookup of our own entry point is the install contract; argv is fixed + user's own flags
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
            f"  4. Work:       {prog} task run <project_name>       (attach into a new CLI task)\n"
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
        version=f"{prog} {version_string}",
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
    # ``--help``.  Lazy: only the invoked verb's module is imported (the whole
    # point — each command module transitively pulls the executor / sandbox
    # stack).  The full-surface cases (bare, ``--help``, unknown verb, shell
    # completion) register every own command so the listing stays complete.
    argv = sys.argv[1:]
    cmd_modules = _load_own_commands(argv)
    for module in cmd_modules:
        if module.__name__.rsplit(".", 1)[-1] == "task":
            # task group + flat ``login`` shortcut; ``prog`` gates terokctl-only verbs.
            module.register(sub, prog=prog)
        else:
            module.register(sub)

    # Build the terok-side CommandTree from upstream packages, apply
    # terok's cfg-injection overlay (concept translation: terok's
    # SandboxConfig wins over sandbox's default), and surface every
    # sibling subtree both deeply (terok executor sandbox vault X) and
    # as top-level shortcuts (terok vault X) that share CommandDef
    # identity with the deep path.
    #
    # Gated: building the tree imports the executor / sandbox / clearance
    # wheels, so a terok-own verb (``config``, ``task``, …) skips it.  The
    # full-surface cases still build it so the command listing (and an unknown
    # verb's error) include the sibling groups.
    if _invocation_needs_wired_tree(argv) or _needs_full_own_surface(argv):
        _build_wired_tree().wire(sub)

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

    from terok.lib.api.vault import NoPassphraseError

    # Dispatch chain — tried in order; first True wins.  Only the modules we
    # actually loaded contribute a ``dispatch`` (own verbs are disjoint, so at
    # most one matches); ``_commandtree_dispatch`` handles the sibling-wired
    # verbs, which set ``args._cmd`` rather than ``args.cmd``.
    dispatchers = [module.dispatch for module in cmd_modules]
    dispatchers.append(_commandtree_dispatch)
    try:
        for dispatch in dispatchers:
            if dispatch(args):
                return
    except NoPassphraseError as exc:
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

    Pulls executor's full [`CommandTree`][terok_util.cli_types.CommandTree]
    (already containing executor's overlays + sandbox spliced under
    ``sandbox``), applies terok's
    [`SandboxConfig`][terok_sandbox.SandboxConfig] injection at every
    handler under the ``sandbox`` namespace that takes ``cfg``, and
    surfaces three views over the same modified subtrees:

    - ``terok executor <verb>``      — full executor deep path.
    - ``terok sandbox <verb>``       — shortcut for the sandbox subtree
      (same children as ``terok executor sandbox``).
    - ``terok {vault,gate,ssh} <verb>`` — shortcuts that reference the
      same [`CommandDef`][terok_util.cli_types.CommandDef] instances
      living under ``sandbox`` so terok's cfg wrap propagates uniformly.
    """
    from terok.lib.api.agents import EXECUTOR_COMMANDS
    from terok.lib.api.clearance import CLEARANCE_COMMANDS

    from ..lib.core.config import make_sandbox_config

    # The executor forest now ships lazy nodes (``source`` set) whose children
    # materialise only on ``resolve()``.  Terok's cfg-injection / ``find_at``
    # composition walks the structure directly, so it needs the fully resolved
    # subtree — deep-resolve up front.  This is gated to sibling verbs
    # (``_invocation_needs_wired_tree``), so a terok-own verb never pays it.
    executor_tree = _resolve_tree(EXECUTOR_COMMANDS)

    # Executor exposes ``CommandTree`` (its top-level forest).  Apply
    # terok's cfg wrap to every handler that declares a ``cfg``
    # parameter, anywhere in the tree — executor-native verbs (run,
    # build, setup, show-config, …) and the sandbox subtree (gate,
    # ssh, vault, credentials) all funnel through
    # ``make_sandbox_config``.  Without this, ``terok executor run``
    # would silently use sandbox's bare-default ``SandboxConfig`` while
    # ``terok vault start`` saw terok's resolved one — a layer
    # inconsistency users hit as "I authenticated already, why is
    # ``terok executor run`` re-prompting?"
    modified = inject_cfg_factory(
        executor_tree,
        subtree_paths=((),),
        factory=make_sandbox_config,
    )

    # Per-container verbs accept either ``<container-id>`` or
    # ``<project>/<task>``; the resolver splits on ``/`` (invalid in
    # podman container names and in any reasonable project / task
    # slug) and looks the (p, t) pair up in terok's task store.
    # Future verbs (``exec``, ``logs``, ``state``, ``login``) join
    # this list as they're added in executor.
    modified = inject_pt_resolver(
        modified,
        verb_specs=((("stop",), "name"),),
    )

    # ``vault passphrase change`` needs the credentials DB exclusively;
    # sandbox's handler can't know about terok's fleet, so terok wraps
    # it with the stop → re-encrypt → restart conversation (the CLI
    # twin of the TUI flow's ``_clear_rekey_blockers``).
    from .commands.vault_change import wrap_passphrase_change

    modified = wrap_passphrase_change(modified)

    # The shortcut nodes share identity with their counterparts inside
    # the modified executor tree — both ``terok vault X`` and ``terok
    # executor sandbox vault X`` resolve to the same wrapped handler.
    sandbox_node = modified.find_at(("sandbox",))
    vault_node = modified.find_at(("sandbox", "vault"))
    gate_node = modified.find_at(("sandbox", "gate"))
    ssh_node = modified.find_at(("sandbox", "ssh"))

    # Clearance's CommandDef is structurally compatible with sandbox's
    # via the duck-typed wire layer.  Wrap as a ``dbus`` group at
    # terok's top level — same UX as the old hand-rolled dbus.py,
    # implemented as a CommandTree composition instead.
    # Post-W2.5 clearance uses terok-util's ``CommandDef`` natively, so
    # the children-types match without a bridge.
    dbus_node = CommandDef(
        name="dbus",
        help="D-Bus tools (notifications, clearance)",
        children=CLEARANCE_COMMANDS,
    )

    tree = CommandTree(
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
            dbus_node,
        )
    )
    # Drift guard: the gate predicate keys off ``_WIRED_TREE_ROOTS``; keep it
    # in lockstep with what we actually wire so a new sibling group can't
    # silently become ungated (and unreachable for terok-own verbs).
    if {root.name for root in tree.roots} != set(_WIRED_TREE_ROOTS):
        raise RuntimeError("wired-tree roots drifted from _WIRED_TREE_ROOTS — update the gate set")
    return tree


def _resolve_node(cmd: CommandDef) -> CommandDef:
    """Resolve *cmd* (importing its ``source`` if lazy) and all descendants."""
    resolved = cmd.resolve()
    if resolved.children:
        resolved = resolved.with_children(
            tuple(_resolve_node(child) for child in resolved.children)
        )
    return resolved


def _resolve_tree(tree: CommandTree) -> CommandTree:
    """Deep-resolve a lazy [`CommandTree`][terok_util.cli_types.CommandTree].

    The sibling forests ship lazy [`CommandDef`][terok_util.cli_types.CommandDef]
    nodes whose children materialise only on ``resolve()``.  Terok's wired-tree
    composition (``inject_cfg_factory`` / ``find_at`` / ``extend_at``) walks the
    structure directly, so it needs the resolved subtree — materialise every
    node and its descendants once, up front.
    """
    return CommandTree(tuple(_resolve_node(root) for root in tree.roots))


def terokctl_main() -> None:
    """Entry point for the ``terokctl`` scriptable surface.

    Same command tree as ``terok``, but no-args prints the argparse
    usage error instead of launching the TUI — the stable, predictable
    behavior scripts and automation want.
    """
    main(prog="terokctl")


if __name__ == "__main__":
    main()
