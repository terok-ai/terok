# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Clearance notifier, unit version checks, CLI registry — public API surface.

Re-export catalog for the operator-prompt layer.  Source:
[`terok.lib.integrations.clearance`][terok.lib.integrations.clearance].

The clearance ``COMMANDS`` registry is aliased to ``CLEARANCE_COMMANDS``
to match the call site in [`terok.cli`][terok.cli] (which wires several
sibling-wheel command trees into the top-level terok CLI under
distinct names).  The clearance ``check_units_outdated`` is exposed as
``check_clearance_units_outdated`` to differentiate it from the
sandbox-side ``check_units_outdated`` re-exported on the package root.
"""

from terok.lib.integrations.clearance import (  # noqa: F401 — re-exported public API
    COMMANDS as CLEARANCE_COMMANDS,
    HUB_UNIT_NAME as CLEARANCE_HUB_UNIT_NAME,
    NOTIFIER_UNIT_NAME as CLEARANCE_NOTIFIER_UNIT_NAME,
    CallbackNotifier,
    EventSubscriber,
    Notification,
    check_units_outdated as check_clearance_units_outdated,
    read_installed_notifier_unit_version,
    read_installed_unit_version,
)

__all__ = [
    "CLEARANCE_COMMANDS",
    "CLEARANCE_HUB_UNIT_NAME",
    "CLEARANCE_NOTIFIER_UNIT_NAME",
    "CallbackNotifier",
    "EventSubscriber",
    "Notification",
    "check_clearance_units_outdated",
    "read_installed_notifier_unit_version",
    "read_installed_unit_version",
]
