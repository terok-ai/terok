# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Adapter for the ``terok_clearance`` wheel.

Re-exports every symbol terok consumes from terok-clearance.  Callers
elsewhere in terok import from this module rather than from
``terok_clearance`` directly — see the package docstring in
[`terok.lib.integrations`][terok.lib.integrations] for the rationale.

Every symbol comes from the wheel's top-level public API.  The hub +
verdict server are composed in-process by the supervisor (one of each
per container) and operator UIs multiplex across the per-container
sockets via
[`MultiSocketSubscriber`][terok_clearance.MultiSocketSubscriber].
There is no host-side ``HubService`` / ``NotifierService`` daemon.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terok_clearance import (
        ALL_NOTIFY_CATEGORIES as ALL_NOTIFY_CATEGORIES,
        COMMANDS as COMMANDS,
        NOTIFY_BLOCKED as NOTIFY_BLOCKED,
        NOTIFY_VERDICT as NOTIFY_VERDICT,
        CallbackNotifier as CallbackNotifier,
        EventSubscriber as EventSubscriber,
        MultiSocketSubscriber as MultiSocketSubscriber,
        Notification as Notification,
        create_notifier as create_notifier,
    )

#: Public name -> defining module (PEP 562 lazy resolution).
_LAZY: dict[str, str] = {
    "ALL_NOTIFY_CATEGORIES": "terok_clearance",
    "COMMANDS": "terok_clearance",
    "CallbackNotifier": "terok_clearance",
    "EventSubscriber": "terok_clearance",
    "MultiSocketSubscriber": "terok_clearance",
    "NOTIFY_BLOCKED": "terok_clearance",
    "NOTIFY_VERDICT": "terok_clearance",
    "Notification": "terok_clearance",
    "create_notifier": "terok_clearance",
}

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
