# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Adapter for the ``terok_shield`` wheel.

Re-exports the symbols terok consumes from terok-shield.  Callers
elsewhere in terok import from this module rather than from
``terok_shield`` directly — see the package docstring in
[`terok.lib.integrations`][terok.lib.integrations] for the rationale.

terok consumes a narrow slice of shield: the ``COMMANDS`` registry
(plus ``CommandDef`` / ``ArgDef``) that the ``terok shield`` CLI bridge
wires into terok's command tree, and ``ExecError`` for the error
handling around it.  Higher-level shield operations reach terok through
terok-sandbox's re-exports — see
[`terok.lib.integrations.sandbox`][terok.lib.integrations.sandbox].
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terok_shield import (
        COMMANDS as COMMANDS,
        ArgDef as ArgDef,
        CommandDef as CommandDef,
        ExecError as ExecError,
    )
    from terok_shield.commands import (
        needs_container as needs_container,
        standalone_only as standalone_only,
    )

#: Public name -> defining module (PEP 562 lazy resolution).
_LAZY: dict[str, str] = {
    "ArgDef": "terok_shield",
    "COMMANDS": "terok_shield",
    "CommandDef": "terok_shield",
    "ExecError": "terok_shield",
    "needs_container": "terok_shield.commands",
    "standalone_only": "terok_shield.commands",
}

__all__ = [
    "ArgDef",
    "COMMANDS",
    "CommandDef",
    "ExecError",
    "needs_container",
    "standalone_only",
]


def __getattr__(name: str) -> object:
    """Resolve a re-exported name to its source module on first access (PEP 562)."""
    try:
        target = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module_path, _, source_name = target.partition(":")
    value = getattr(importlib.import_module(module_path), source_name or name)
    globals()[name] = value  # cache so subsequent lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    """Expose the lazy names to ``dir()`` / autocompletion."""
    return sorted({*globals(), *_LAZY})
