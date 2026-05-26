# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Clearance notifier + multi-socket subscriber + CLI registry — public API surface.

Re-export catalog for the operator-prompt layer.  Source:
[`terok.lib.integrations.clearance`][terok.lib.integrations.clearance].

The clearance ``COMMANDS`` registry is aliased to ``CLEARANCE_COMMANDS``
to match the call site in [`terok.cli`][terok.cli] (which wires several
sibling-wheel command trees into the top-level terok CLI under
distinct names).

Every container hosts its own hub + verdict server in the supervisor
process; there is no host-side ``HubService`` / ``NotifierService``
daemon.  Operator UIs multiplex across the per-container sockets via
[`MultiSocketSubscriber`][terok_clearance.MultiSocketSubscriber].
"""

from terok.lib.integrations.clearance import (  # noqa: F401 — re-exported public API
    ALL_NOTIFY_CATEGORIES,
    COMMANDS as CLEARANCE_COMMANDS,
    NOTIFY_BLOCKED,
    NOTIFY_VERDICT,
    CallbackNotifier,
    EventSubscriber,
    MultiSocketSubscriber,
    Notification,
    create_notifier,
)

__all__ = [
    "ALL_NOTIFY_CATEGORIES",
    "CLEARANCE_COMMANDS",
    "CallbackNotifier",
    "EventSubscriber",
    "MultiSocketSubscriber",
    "NOTIFY_BLOCKED",
    "NOTIFY_VERDICT",
    "Notification",
    "create_notifier",
]
