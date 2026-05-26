# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Adapter for the ``terok_clearance`` wheel.

Re-exports every symbol terok consumes from terok-clearance.  Callers
elsewhere in terok import from this module rather than from
``terok_clearance`` directly — see the package docstring in
[`terok.lib.integrations`][terok.lib.integrations] for the rationale.

Every symbol comes from the wheel's top-level public API.  In the
per-container-supervisor model the hub + verdict server are composed
in-process by the supervisor (one of each per container) and operator
UIs multiplex across the per-container sockets via
[`MultiSocketSubscriber`][terok_clearance.MultiSocketSubscriber].
There is no host-side ``HubService`` / ``NotifierService`` daemon
anymore — the legacy systemd-unit installers are gone.
"""

from terok_clearance import (  # noqa: F401 — re-exported public API
    ALL_NOTIFY_CATEGORIES,
    COMMANDS,
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
    "COMMANDS",
    "CallbackNotifier",
    "EventSubscriber",
    "MultiSocketSubscriber",
    "NOTIFY_BLOCKED",
    "NOTIFY_VERDICT",
    "Notification",
    "create_notifier",
]
