# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Behavioural tests for the ``child_process_env`` Nix-wrapped-Python shim.

The wrapped-Python failure mode (Nix, some Conda envs):

* The parent process has its ``sys.path`` augmented at import time by a
  wrapper script or ``.pth`` file — terok lives at a path the bare
  interpreter doesn't auto-discover.
* That augmentation is *not* reflected in ``os.environ['PYTHONPATH']``.
* A subprocess started directly via ``[sys.executable, '-m', 'terok', …]``
  inherits the empty/wrong ``PYTHONPATH`` and can't ``import terok``.

These tests prove
[`child_process_env`][terok.lib.util.subprocess_env.child_process_env]
actually fixes that path without needing real Nix.  They drop a stub
module into a tmpdir, add the dir to the *parent's* ``sys.path`` only,
then exercise both the broken (naive) and fixed (via the helper)
subprocess patterns and assert the expected outcomes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from terok.lib.util.subprocess_env import child_process_env

_STUB_MODULE_NAME = "_terok_nix_regression_stub"


@pytest.fixture
def parent_only_sys_path_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Drop a stub package into *tmp_path* and add it to the parent's sys.path only.

    Simulates the Nix-wrapped-Python setup where a directory is added to
    the parent's ``sys.path`` via wrapper magic (not via ``PYTHONPATH``),
    so a naive subprocess can't see it.
    """
    stub = tmp_path / f"{_STUB_MODULE_NAME}.py"
    stub.write_text(f"VALUE = 'imported-from-{tmp_path.name}'\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    # PYTHONPATH must *not* contain tmp_path — that's the whole point.
    # ``monkeypatch.syspath_prepend`` only touches the in-process
    # ``sys.path``, never the env var, so this invariant is automatic.
    return stub


def _subprocess_can_import(env: dict[str, str]) -> tuple[int, str]:
    """Return ``(returncode, stdout)`` of a subprocess trying to import the stub."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import {_STUB_MODULE_NAME}; print({_STUB_MODULE_NAME}.VALUE)",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result.returncode, result.stdout.strip()


def test_naive_subprocess_cannot_import_parent_only_module(
    parent_only_sys_path_module: Path,
) -> None:
    """Without the helper, the child can't see the parent's wrapped sys.path.

    This *is* the bug Franz Pöschel hit (terok-ai/terok#717): a fresh
    Python subprocess started from a Nix-wrapped parent doesn't inherit
    the wrapper's sys.path adjustments.
    """
    # Naive: inherit os.environ (no PYTHONPATH override).  Strip any
    # ambient PYTHONPATH so the test is hermetic regardless of how
    # CI launched it.
    rc, _stdout = _subprocess_can_import({"PATH": "/usr/bin:/bin"})
    assert rc != 0, (
        "Test fixture is broken: the child found the stub module without "
        "PYTHONPATH forwarding.  Check that the parent's sys.path "
        "augmentation isn't leaking into PYTHONPATH."
    )


def test_child_process_env_fixes_wrapped_python_import(
    parent_only_sys_path_module: Path,
) -> None:
    """With ``child_process_env``, the child finds the parent-only module.

    The other half of the regression test: prove that the same subprocess
    invocation that failed above succeeds once ``env=child_process_env()``
    threads the parent's ``sys.path`` through ``PYTHONPATH``.
    """
    rc, stdout = _subprocess_can_import(child_process_env())
    assert rc == 0, "child_process_env() failed to forward parent sys.path"
    assert stdout.startswith("imported-from-"), (
        f"unexpected child stdout: {stdout!r} — the stub module rendered "
        "differently than expected; check the fixture."
    )
