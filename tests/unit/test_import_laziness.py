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


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        # terok-own verbs — skip the wired tree.
        (["info"], False),
        (["info", "--help"], False),
        (["task", "list"], False),
        (["--no-emoji", "info"], False),
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
    assert _invocation_needs_wired_tree(["info"]) is True


def test_wired_tree_roots_match_builder() -> None:
    """The gate's root set stays in lockstep with what ``_build_wired_tree`` wires."""
    import importlib

    main = importlib.import_module("terok.cli.main")
    tree = main._build_wired_tree()
    assert {root.name for root in tree.roots} == set(main._WIRED_TREE_ROOTS)


def test_info_invocation_skips_building_wired_tree() -> None:
    """``terok info --help`` must not call ``_build_wired_tree`` (siblings stay unloaded)."""
    import importlib

    main = importlib.import_module("terok.cli.main")
    with (
        mock.patch.object(main, "_build_wired_tree") as build,
        mock.patch("sys.argv", ["terok", "info", "--help"]),
        pytest.raises(SystemExit),
    ):
        main.main()
    build.assert_not_called()


#: Modules whose only load path on a terok-own verb is the wired tree (or the
#: ACP subsystem).  The gate keeps them out of ``terok info``.  Note:
#: ``terok_executor`` / ``terok_sandbox`` are NOT here — they still load via
#: command *registration* (command modules import ``core.config`` /
#: ``orchestration`` / ``domain`` at module scope), which a follow-up
#: lazy-registration change would address.
_WIRED_ONLY = ("terok_clearance", "acp")


def test_info_invocation_does_not_load_wired_only_siblings() -> None:
    """``terok info --help`` keeps the wired-tree-only siblings (clearance, acp) unloaded."""
    probe = (
        "import sys\n"
        "sys.argv = ['terok', 'info', '--help']\n"
        "import importlib\n"
        "M = importlib.import_module('terok.cli.main')\n"
        "try:\n"
        "    M.main()\n"
        "except SystemExit:\n"
        "    pass\n"
        f"present = [m for m in {_WIRED_ONLY!r} if m in sys.modules]\n"
        "print('RESULT:' + ','.join(present))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    line = next(x for x in result.stdout.splitlines() if x.startswith("RESULT:"))
    present = [m for m in line[len("RESULT:") :].split(",") if m]
    assert not present, (
        f"terok info loaded wired-tree-only siblings {present} — the gate should "
        "have skipped _build_wired_tree for a terok-own verb."
    )


def test_executor_invocation_loads_siblings() -> None:
    """``terok executor --help`` does build the wired tree, so the clearance wheel loads."""
    probe = (
        "import sys\n"
        "sys.argv = ['terok', 'executor', '--help']\n"
        "import importlib\n"
        "M = importlib.import_module('terok.cli.main')\n"
        "try:\n"
        "    M.main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "print('RESULT:' + str('terok_clearance' in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    line = next(x for x in result.stdout.splitlines() if x.startswith("RESULT:"))
    assert line == "RESULT:True"


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
