# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Adapter for the ``terok_clearance`` wheel.

Re-exports every symbol terok consumes from terok-clearance.  Callers
elsewhere in terok import from this module rather than from
``terok_clearance`` directly — see the package docstring in
[`terok.lib.integrations`][terok.lib.integrations] for the rationale.

Every symbol comes from the wheel's top-level public API.
"""

from terok_clearance import (  # noqa: F401 — re-exported public API
    COMMANDS,
    HUB_UNIT_NAME,
    NOTIFIER_UNIT_NAME,
    CallbackNotifier,
    EventSubscriber,
    Notification,
    check_units_outdated,
    read_installed_notifier_unit_version,
    read_installed_unit_version,
)

__all__ = [
    "COMMANDS",
    "CallbackNotifier",
    "EventSubscriber",
    "HUB_UNIT_NAME",
    "NOTIFIER_UNIT_NAME",
    "Notification",
    "check_units_outdated",
    "read_installed_notifier_unit_version",
    "read_installed_unit_version",
]
