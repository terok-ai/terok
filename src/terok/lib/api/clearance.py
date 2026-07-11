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

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terok.lib.integrations.clearance import (
        ALL_NOTIFY_CATEGORIES as ALL_NOTIFY_CATEGORIES,
        COMMANDS as CLEARANCE_COMMANDS,
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
    "ALL_NOTIFY_CATEGORIES": "terok.lib.integrations.clearance",
    "CLEARANCE_COMMANDS": "terok.lib.integrations.clearance:COMMANDS",
    "CallbackNotifier": "terok.lib.integrations.clearance",
    "EventSubscriber": "terok.lib.integrations.clearance",
    "MultiSocketSubscriber": "terok.lib.integrations.clearance",
    "NOTIFY_BLOCKED": "terok.lib.integrations.clearance",
    "NOTIFY_VERDICT": "terok.lib.integrations.clearance",
    "Notification": "terok.lib.integrations.clearance",
    "create_notifier": "terok.lib.integrations.clearance",
}

__all__ = [
    "CLEARANCE_COMMANDS",
    "CallbackNotifier",
    "MultiSocketSubscriber",
    "Notification",
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
