# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Gate operations and types — public API surface.

Re-export catalog for the per-container git gate.  Sources:
[`terok.lib.integrations.sandbox`][terok.lib.integrations.sandbox] for
the mirror staleness / auth types and ``mint_gate_token`` (terok-sandbox
owns the gate infrastructure — the gate now runs inside each container's
supervisor, not as a host daemon), and
[`terok.lib.domain.project`][terok.lib.domain.project] for
``make_git_gate`` (terok's per-project gate factory).
"""

from terok.lib.domain.project import make_git_gate  # noqa: F401 — re-exported public API
from terok.lib.integrations.sandbox import (  # noqa: F401 — re-exported public API
    GateAuthNotConfigured,
    GateStalenessInfo,
    mint_gate_token,
)

__all__ = [
    "GateAuthNotConfigured",
    "GateStalenessInfo",
    "make_git_gate",
    "mint_gate_token",
]
