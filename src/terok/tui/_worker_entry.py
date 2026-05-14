# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Child-process entrypoint for ConsoleLog-dispatched actions.

Runs a referenced callable in a fresh Python process so its stdout,
its stderr, and any commands it shells out to (``podman build``,
``git clone --mirror``) own their own file descriptors.  The parent's
[`ConsoleLogRegistry`][terok.tui.console_log.ConsoleLogRegistry] pump
captures that pipe cleanly — in-process fd redirection would clobber
fd 1/2 and corrupt the Textual frame (see issue #473).

Invoked as::

    python -u -m terok.tui._worker_entry "<module.path:function>" "<json-args>"

where ``<json-args>`` is a JSON array of positional arguments.  Process
exit code: 0 on success; 2 on a malformed invocation; 1 on an
unexpected exception (traceback printed).  A ``SystemExit`` from the
callable — the facade's user-facing-error convention — is left to
propagate, so an int code passes straight through and a string code is
printed and exits 1 (the interpreter's default).  Everything printed
lands in the parent's captured log.
"""

from __future__ import annotations

import importlib
import json
import sys
import traceback
from collections.abc import Callable


def _resolve(ref: str) -> Callable[..., object]:
    """Resolve a ``"module.path:function"`` reference to the callable."""
    module_path, sep, attr = ref.partition(":")
    if not module_path or not sep or not attr:
        raise ValueError(f"worker ref must be 'module.path:function', got {ref!r}")
    return getattr(importlib.import_module(module_path), attr)


def main(argv: list[str]) -> int:
    """Resolve and invoke the referenced callable; return its process exit code.

    A ``SystemExit`` raised by the callable is deliberately *not*
    caught — it propagates so the child exits with that status (an int
    code passes through; a string code the interpreter prints and exits
    1).  Only the malformed-invocation and unexpected-crash paths
    return a code from here.
    """
    if len(argv) != 2:
        print("usage: python -m terok.tui._worker_entry '<module.path:function>' '<json-args>'")
        return 2
    ref, raw_args = argv
    try:
        func = _resolve(ref)
        args = json.loads(raw_args)
        if not isinstance(args, list):
            raise TypeError(f"json-args must be a JSON array, got {type(args).__name__}")
    except Exception:
        traceback.print_exc()
        return 2

    try:
        func(*args)
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    sys.exit(main(sys.argv[1:]))
