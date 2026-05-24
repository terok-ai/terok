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

from terok.lib.integrations.sandbox import (  # noqa: F401 — re-exported public API
    RecoveryStatus,
    SandboxConfig,
    ShieldHooks,
    ShieldManager,
    installed_versions,
    read_stamp,
    stamp_path,
)
from terok.lib.integrations.shield import (  # noqa: F401 — re-exported public API
    COMMANDS as SHIELD_COMMANDS,
    ArgDef,
    CommandDef as ShieldCommandDef,
    ExecError,
    needs_container as shield_needs_container,
    standalone_only as shield_standalone_only,
)

__all__ = [
    "ArgDef",
    "ExecError",
    "RecoveryStatus",
    "SHIELD_COMMANDS",
    "SandboxConfig",
    "ShieldCommandDef",
    "ShieldHooks",
    "ShieldManager",
    "installed_versions",
    "read_stamp",
    "shield_needs_container",
    "shield_standalone_only",
    "stamp_path",
]
