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

from terok_shield import (  # noqa: F401 — re-exported public API
    COMMANDS,
    ArgDef,
    CommandDef,
    ExecError,
)
from terok_shield.commands import (  # noqa: F401 — re-exported public API
    needs_container,
    standalone_only,
)

__all__ = [
    "ArgDef",
    "COMMANDS",
    "CommandDef",
    "ExecError",
    "needs_container",
    "standalone_only",
]
