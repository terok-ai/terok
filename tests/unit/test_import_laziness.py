# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Guard: ``import terok.cli`` must not pull the heavy runtime stack.

The CLI front door is lazified (PEP 562 ``__getattr__`` barrels + deferred
command-module imports in ``main``) so that merely importing it — as test
collection, shell-completion shims, and ``import terok`` all do — pays for
none of the executor / sandbox wheels nor their transitive ``acp`` /
``pydantic`` / ``cryptography`` / ``textual`` dependencies.  Those load
only when a command that needs them is actually dispatched.

Run in a fresh interpreter (``subprocess``) because ``sys.modules`` is
process-global: any earlier test that imported the stack would mask a
regression if we probed the current interpreter.
"""

from __future__ import annotations

import subprocess
import sys
from unittest import mock

import pytest

#: Modules that mark the heavy runtime stack.  None may be imported by a
#: bare ``import terok.cli``.
_FORBIDDEN = (
    "terok_executor",
    "terok_sandbox",
    "acp",
    "textual",
    "pydantic",
    "cryptography",
)


def test_import_terok_cli_stays_lazy() -> None:
    """A cold ``import terok.cli`` loads none of the heavy runtime modules."""
    probe = (
        "import sys\n"
        "import terok.cli\n"
        f"forbidden = {_FORBIDDEN!r}\n"
        "present = [m for m in forbidden if m in sys.modules]\n"
        "print(','.join(present))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    present = [m for m in result.stdout.strip().split(",") if m]
    assert not present, (
        f"import terok.cli eagerly loaded heavy modules: {present}. "
        "Something on the CLI import path resolved a lazy barrel symbol or "
        "imported a command module at module scope."
    )


# ── Wired-tree gate ────────────────────────────────────────────────────
#
# ``_build_wired_tree`` imports the executor / sandbox / clearance wheels to
# register the sibling passthrough groups.  The gate skips it for terok-own
# verbs.  These tests key off the predicate and off whether the builder is
# invoked — deterministic signals that don't depend on the (registration-
# polluted) module set.


def _run_cli(argv: list[str], report_expr: str) -> str:
    """Run ``terok <argv>`` in a fresh interpreter; return the ``RESULT:`` line body.

    *report_expr* is a Python expression evaluated after dispatch (which exits
    via ``SystemExit`` for ``--help``); its ``str()`` is printed behind a
    ``RESULT:`` sentinel so it survives argparse's own stdout.
    """
    probe = (
        "import sys\n"
        f"sys.argv = {['terok', *argv]!r}\n"
        "import importlib\n"
        "M = importlib.import_module('terok.cli.main')\n"
        "try:\n"
        "    M.main()\n"
        "except SystemExit:\n"
        "    pass\n"
        f"print('RESULT:' + str({report_expr}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    line = next(x for x in result.stdout.splitlines() if x.startswith("RESULT:"))
    return line[len("RESULT:") :]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        # terok-own verbs — skip the wired tree.
        (["config", "paths"], False),
        (["config", "--help"], False),
        (["task", "list"], False),
        (["--no-emoji", "config"], False),
        (["--experimental", "task", "run", "p"], False),
        # sibling-wired groups — must build it.
        (["executor", "run", "claude", "."], True),
        (["sandbox", "gate", "status"], True),
        (["vault", "status"], True),
        (["gate", "status"], True),
        (["ssh", "list"], True),
        (["dbus", "notify"], True),
        # full-surface cases — must build it.
        ([], True),
        (["--help"], True),
        (["-h"], True),
        (["--experimental", "--help"], True),
    ],
)
def test_wired_tree_gate_predicate(argv: list[str], expected: bool) -> None:
    """``_invocation_needs_wired_tree`` matches the intended verb classification."""
    from terok.cli.main import _invocation_needs_wired_tree

    assert _invocation_needs_wired_tree(argv) is expected


def test_wired_tree_gate_completion_forces_full_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shell completion (``_ARGCOMPLETE`` set) always builds the full tree."""
    from terok.cli.main import _invocation_needs_wired_tree

    monkeypatch.setenv("_ARGCOMPLETE", "1")
    assert _invocation_needs_wired_tree(["config"]) is True


def test_wired_tree_roots_match_builder() -> None:
    """The gate's root set stays in lockstep with what ``_build_wired_tree`` wires."""
    import importlib

    main = importlib.import_module("terok.cli.main")
    tree = main._build_wired_tree()
    assert {root.name for root in tree.roots} == set(main._WIRED_TREE_ROOTS)


def test_own_verb_invocation_skips_building_wired_tree() -> None:
    """A terok-own verb (``config``) must not call ``_build_wired_tree``."""
    import importlib

    main = importlib.import_module("terok.cli.main")
    with (
        mock.patch.object(main, "_build_wired_tree") as build,
        mock.patch("sys.argv", ["terok", "config", "--help"]),
        pytest.raises(SystemExit),
    ):
        main.main()
    build.assert_not_called()


def test_executor_invocation_builds_wired_tree() -> None:
    """``terok executor --help`` must build the wired tree (gate doesn't over-block)."""
    import importlib

    main = importlib.import_module("terok.cli.main")
    real = main._build_wired_tree
    with (
        mock.patch.object(main, "_build_wired_tree", wraps=real) as build,
        mock.patch("sys.argv", ["terok", "executor", "--help"]),
        pytest.raises(SystemExit),
    ):
        main.main()
    build.assert_called_once()


# ── Per-verb command-module laziness ───────────────────────────────────
#
# ``main`` imports only the invoked verb's command module (plus shared,
# stack-free helpers), never all fourteen.  ``config`` (owned by the ``info``
# module) is the clean reference: its module defers every heavy import into
# handlers, so registering it pulls no wheels at all.

#: Every own-verb command module *except* ``info`` (which owns ``config``).
_OTHER_OWN_MODULES = (
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
    "acp",
    "completions",
)


def test_config_invocation_imports_only_its_own_command_module() -> None:
    """``terok config --help`` imports the ``info`` module and none of the other verbs'."""
    loaded = _run_cli(
        ["config", "--help"],
        "[n for n in " + repr(_OTHER_OWN_MODULES) + " if 'terok.cli.commands.' + n in sys.modules]",
    )
    others = [m for m in loaded.strip("[]").replace("'", "").split(", ") if m]
    assert not others, f"terok config eagerly imported other command modules: {others}"


def test_config_invocation_loads_no_heavy_stack() -> None:
    """``terok config --help`` pulls none of the executor / sandbox / pydantic stack."""
    present = _run_cli(
        ["config", "--help"],
        "[m for m in " + repr(_FORBIDDEN) + " if m in sys.modules]",
    )
    heavy = [m for m in present.strip("[]").replace("'", "").split(", ") if m]
    assert not heavy, f"terok config eagerly loaded the heavy stack: {heavy}"


def test_help_lists_every_verb() -> None:
    """``terok --help`` still lists all own and sibling-wired verbs."""
    result = subprocess.run(
        [sys.executable, "-m", "terok.cli", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    out = result.stdout
    for verb in (
        # own verbs
        "panic",
        "setup",
        "auth",
        "project",
        "task",
        "config",
        "acp",
        "completions",
        # sibling-wired verbs
        "executor",
        "sandbox",
        "vault",
        "gate",
        "ssh",
        "dbus",
    ):
        assert verb in out, f"terok --help omitted the {verb!r} verb"


def test_own_verb_registry_matches_registration() -> None:
    """``_OWN_VERB_MODULES`` maps each verb to the module that actually registers it."""
    import argparse
    import importlib

    from terok.cli.main import _OWN_MODULE_ORDER, _OWN_VERB_MODULES

    actual: dict[str, str] = {}
    for name in _OWN_MODULE_ORDER:
        module = importlib.import_module(f"terok.cli.commands.{name}")
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        before = set(sub.choices)
        if name == "task":
            module.register(sub, prog="terok")
        else:
            module.register(sub)
        for verb in set(sub.choices) - before:
            actual[verb] = name
    assert actual == _OWN_VERB_MODULES
