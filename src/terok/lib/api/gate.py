# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Gate-server operations and types — public API surface.

Re-export catalog for the egress-gate daemon.  Sources:
[`terok.lib.integrations.sandbox`][terok.lib.integrations.sandbox] for
the daemon / manager / status types (terok-sandbox owns the gate
infrastructure), and
[`terok.lib.domain.project`][terok.lib.domain.project] for
``make_git_gate`` (terok's per-project gate factory).
"""

from terok.lib.domain.project import make_git_gate  # noqa: F401 — re-exported public API
from terok.lib.integrations.sandbox import (  # noqa: F401 — re-exported public API
    GateAuthNotConfigured,
    GateServerManager,
    GateServerStatus,
    GateStalenessInfo,
)

__all__ = [
    "GateAuthNotConfigured",
    "GateServerManager",
    "GateServerStatus",
    "GateStalenessInfo",
    "make_git_gate",
]
