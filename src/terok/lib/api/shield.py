# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shield operations and CLI registry — public API surface.

Re-export catalog for the egress-firewall layer.  Sources:
[`terok.lib.integrations.sandbox`][terok.lib.integrations.sandbox] for
the high-level shield wrappers terok-sandbox owns
(``make_shield``/``up``/``down``/``status``/``run_setup`` /
``recovery_status`` and friends), and
[`terok.lib.integrations.shield`][terok.lib.integrations.shield] for
the lower-level CLI registry (``COMMANDS``, ``ArgDef``, ``CommandDef``,
``ExecError``) that terok's ``terok shield`` bridge wires into its own
command tree.

shield's ``CommandDef`` is aliased to ``ShieldCommandDef`` so it doesn't
collide with sandbox's ``CommandDef`` (which already flows through
[`terok.lib.api`][terok.lib.api] for the CLI tree).
"""

from terok.lib.integrations.sandbox import (  # noqa: F401 — re-exported public API
    acknowledge_recovery,
    down as shield_down,
    installed_versions,
    is_recovery_acknowledged,
    make_shield,
    read_stamp,
    recovery_status,
    run_setup as shield_run_setup,
    stamp_path,
    state as shield_state,
    status as shield_status,
    up as shield_up,
)
from terok.lib.integrations.shield import (  # noqa: F401 — re-exported public API
    COMMANDS as SHIELD_COMMANDS,
    ArgDef,
    CommandDef as ShieldCommandDef,
    ExecError,
)

__all__ = [
    "ArgDef",
    "ExecError",
    "SHIELD_COMMANDS",
    "ShieldCommandDef",
    "acknowledge_recovery",
    "installed_versions",
    "is_recovery_acknowledged",
    "make_shield",
    "read_stamp",
    "recovery_status",
    "shield_down",
    "shield_run_setup",
    "shield_state",
    "shield_status",
    "shield_up",
    "stamp_path",
]
