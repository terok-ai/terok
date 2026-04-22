# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``terok-clearance-notifier`` — bridges hub events to desktop popups.

Separate systemd user service (``terok-clearance-notifier.service``)
from the hub (``terok-dbus.service``) so the hub stays UI-agnostic —
headless hosts (CI, servers) run the hub without pulling in a
desktop-notifier dependency, and desktops get richer rendering with
terok's task-aware identity resolution.
"""

from terok.clearance.notifier.app import run_notifier

__all__ = ["run_notifier"]
