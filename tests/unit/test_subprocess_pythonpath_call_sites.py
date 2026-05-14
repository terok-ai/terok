# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Static check: every ``[sys.executable, ...]`` spawn must thread ``PYTHONPATH``.

Walks ``src/terok/`` looking for ``subprocess.run`` / ``subprocess.Popen``
/ ``asyncio.create_subprocess_exec`` calls whose argv starts with
``sys.executable``.  Each such site must pass ``env=child_process_env(...)``
— otherwise terok crashes under wrapped-Python setups (Nix, some Conda
environments) where the parent's ``sys.path`` is hidden from the child's
default import-path discovery (terok-ai/terok#717).

Failing this test means someone added a new spawn site without going
through the helper.  Fix it by importing
``from terok.lib.util.subprocess_env import child_process_env`` and
passing ``env=child_process_env()`` on the call.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "terok"
_SPAWN_CALLABLES = {
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("asyncio", "create_subprocess_exec"),
}


def _is_module_run(call: ast.Call) -> bool:
    """Return True when *call*'s argv looks like ``[sys.executable, "-m", "<mod>", ...]``.

    Only ``python -m module`` invocations need the ``PYTHONPATH`` shim —
    they import the module and so depend on ``sys.path``.  Script-path
    invocations (``python /path/to/script.py``) get their script dir
    on ``sys.path[0]`` automatically, so they don't need the helper
    (and stdlib-only scripts shouldn't pretend to).
    """
    if not call.args:
        return False
    argv = call.args[0]
    if not isinstance(argv, ast.List) or len(argv.elts) < 3:
        return False
    first, second = argv.elts[0], argv.elts[1]
    is_sys_executable = (
        isinstance(first, ast.Attribute)
        and isinstance(first.value, ast.Name)
        and first.value.id == "sys"
        and first.attr == "executable"
    )
    is_dash_m = isinstance(second, ast.Constant) and second.value == "-m"
    return is_sys_executable and is_dash_m


def _is_spawn_call(call: ast.Call) -> bool:
    """Return True when *call* invokes a known subprocess-spawning function."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return (func.value.id, func.attr) in _SPAWN_CALLABLES
    return False


def _env_kwarg_uses_helper(call: ast.Call) -> bool:
    """Return True when *call* has ``env=child_process_env(...)``."""
    for kw in call.keywords:
        if kw.arg != "env":
            continue
        value = kw.value
        # env=child_process_env() / child_process_env({...})
        if isinstance(value, ast.Call):
            func = value.func
            if isinstance(func, ast.Name) and func.id == "child_process_env":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "child_process_env":
                return True
    return False


def _iter_python_files(root: Path):
    """Yield every ``.py`` file under *root*, excluding ``__pycache__`` etc."""
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_every_sys_executable_spawn_uses_child_process_env() -> None:
    """Every ``[sys.executable, ...]`` spawn must pass ``env=child_process_env(...)``.

    Regression guard for terok-ai/terok#717 (Nix-wrapped Python: a
    subprocess that doesn't inherit the parent's ``sys.path`` via
    ``PYTHONPATH`` can't ``import terok``).
    """
    offenders: list[str] = []
    for path in _iter_python_files(_SRC_ROOT):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_spawn_call(node):
                continue
            if not _is_module_run(node):
                continue
            if _env_kwarg_uses_helper(node):
                continue
            offenders.append(
                f"  {path.relative_to(_SRC_ROOT.parents[1])}:{node.lineno} — "
                "missing env=child_process_env(...)"
            )

    assert not offenders, (
        "Found subprocess spawns of ``sys.executable`` that don't go through "
        "``child_process_env`` — these will fail to import terok under "
        "wrapped-Python setups (Nix, terok-ai/terok#717):\n" + "\n".join(offenders)
    )
