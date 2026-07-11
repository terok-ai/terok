# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shield operations and CLI registry — public API surface.

Re-export catalog for the egress-firewall layer.  Sources:
[`terok.lib.integrations.sandbox`][terok.lib.integrations.sandbox] for
the high-level shield surface terok-sandbox owns
([`ShieldManager`][terok_sandbox.ShieldManager],
[`ShieldHooks`][terok_sandbox.ShieldHooks],
[`RecoveryStatus`][terok_sandbox.RecoveryStatus]), and
[`terok.lib.integrations.shield`][terok.lib.integrations.shield] for
the lower-level CLI registry (``COMMANDS``, ``ArgDef``, ``CommandDef``,
``ExecError``) that terok's ``terok shield`` bridge wires into its own
command tree.

shield's ``CommandDef`` is aliased to ``ShieldCommandDef`` so it doesn't
collide with sandbox's ``CommandDef`` (which already flows through
[`terok.lib.api`][terok.lib.api] for the CLI tree).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terok.lib.integrations.sandbox import (
        RecoveryStatus as RecoveryStatus,
        SandboxConfig as SandboxConfig,
        ShieldHooks as ShieldHooks,
        ShieldManager as ShieldManager,
        installed_versions as installed_versions,
        read_stamp as read_stamp,
        stamp_path as stamp_path,
    )
    from terok.lib.integrations.shield import (
        COMMANDS as SHIELD_COMMANDS,
        ArgDef as ArgDef,
        CommandDef as ShieldCommandDef,
        ExecError as ExecError,
        needs_container as shield_needs_container,
        standalone_only as shield_standalone_only,
    )

#: Public name -> defining module (PEP 562 lazy resolution).
_LAZY: dict[str, str] = {
    "ArgDef": "terok.lib.integrations.shield",
    "ExecError": "terok.lib.integrations.shield",
    "RecoveryStatus": "terok.lib.integrations.sandbox",
    "SHIELD_COMMANDS": "terok.lib.integrations.shield:COMMANDS",
    "SandboxConfig": "terok.lib.integrations.sandbox",
    "ShieldCommandDef": "terok.lib.integrations.shield:CommandDef",
    "ShieldHooks": "terok.lib.integrations.sandbox",
    "ShieldManager": "terok.lib.integrations.sandbox",
    "installed_versions": "terok.lib.integrations.sandbox",
    "read_stamp": "terok.lib.integrations.sandbox",
    "shield_needs_container": "terok.lib.integrations.shield:needs_container",
    "shield_standalone_only": "terok.lib.integrations.shield:standalone_only",
    "stamp_path": "terok.lib.integrations.sandbox",
}

__all__ = [
    "ArgDef",
    "ExecError",
    "RecoveryStatus",
    "SHIELD_COMMANDS",
    "ShieldCommandDef",
    "ShieldManager",
    "installed_versions",
    "read_stamp",
    "shield_needs_container",
    "shield_standalone_only",
    "stamp_path",
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
