# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Clearance notifier, service installers, CLI registry — public API surface.

Re-export catalog for the operator-prompt layer.  Source:
[`terok.lib.integrations.clearance`][terok.lib.integrations.clearance].

The clearance ``COMMANDS`` registry is aliased to ``CLEARANCE_COMMANDS``
to match the call site in [`terok.cli`][terok.cli] (which wires several
sibling-wheel command trees into the top-level terok CLI under
distinct names).  The clearance ``outdated_summary`` is exposed as
``clearance_outdated_summary`` to differentiate it from sandbox's
own gate/vault drift check.

The ``HubService`` and ``NotifierService`` classes (W5.E) replace the
six free fns the previous catalog re-exported (install/uninstall pairs
+ version probes); the version probes are now classmethod-style on the
two services.
"""

from terok.lib.integrations.clearance import (  # noqa: F401 — re-exported public API
    COMMANDS as CLEARANCE_COMMANDS,
    HUB_UNIT_NAME as CLEARANCE_HUB_UNIT_NAME,
    NOTIFIER_UNIT_NAME as CLEARANCE_NOTIFIER_UNIT_NAME,
    CallbackNotifier,
    EventSubscriber,
    HubService,
    Notification,
    NotifierService,
    outdated_summary as clearance_outdated_summary,
)

# ── Thin shims wrapping the post-W5.E class API ─────────
#
# Pre-W5.E free-function names kept as one-liner pass-throughs so
# terok's sickbay + TUI keep one stable surface as the clearance
# installer evolves.


def check_clearance_units_outdated() -> str | None:
    """Drift summary across hub + notifier — shim around ``outdated_summary``."""
    return clearance_outdated_summary()


def read_installed_unit_version() -> int | None:
    """Hub unit version — shim around ``HubService.installed_version``."""
    return HubService.installed_version()


def read_installed_notifier_unit_version() -> int | None:
    """Notifier unit version — shim around ``NotifierService.installed_version``."""
    return NotifierService.installed_version()


__all__ = [
    "CLEARANCE_COMMANDS",
    "CLEARANCE_HUB_UNIT_NAME",
    "CLEARANCE_NOTIFIER_UNIT_NAME",
    "CallbackNotifier",
    "EventSubscriber",
    "HubService",
    "Notification",
    "NotifierService",
    "check_clearance_units_outdated",
    "clearance_outdated_summary",
    "read_installed_notifier_unit_version",
    "read_installed_unit_version",
]
