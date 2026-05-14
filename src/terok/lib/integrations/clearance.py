# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Adapter for the ``terok_clearance`` wheel.

Re-exports every symbol terok consumes from terok-clearance.  Callers
elsewhere in terok import from this module rather than from
``terok_clearance`` directly — see the package docstring in
[`terok.lib.integrations`][terok.lib.integrations] for the rationale.

``COMMANDS`` / ``ArgDef`` (the CLI registry) and the
``runtime.installer`` unit-name constants are not exposed at the
wheel's top level yet, so they are pulled from their submodules here.
"""

from terok_clearance import (  # noqa: F401 — re-exported public API
    CallbackNotifier,
    EventSubscriber,
    Notification,
    check_units_outdated,
    read_installed_notifier_unit_version,
    read_installed_unit_version,
)
from terok_clearance.cli.registry import (  # noqa: F401 — re-exported public API
    COMMANDS,
    ArgDef,
)
from terok_clearance.runtime.installer import (  # noqa: F401 — re-exported public API
    HUB_UNIT_NAME,
    NOTIFIER_UNIT_NAME,
)

__all__ = [
    "ArgDef",
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
