# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Desktop clearance notifier — hub varlink client + freedesktop popups.

One long-lived coroutine that:

1. Connects to ``terok-dbus``'s clearance hub via varlink.
2. Connects to ``org.freedesktop.Notifications`` on the session bus.
3. Streams hub events; each one flows into
   :class:`terok_dbus.EventSubscriber` which renders a popup, tracks
   pending-verdict state, and routes the operator's Allow / Deny click
   back to the hub over the same varlink connection.

Runs as ``terok-clearance-notifier.service`` — a systemd user unit
installed by ``terok setup`` alongside the hub's own unit.  Crashes
here never take the firewall (shield) or the hub with them; systemd
restarts the notifier independently.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from terok_dbus import EventSubscriber, Notifier, create_notifier
from terok_dbus._service import configure_logging, wait_for_shutdown_signal

from terok.clearance.identity import IdentityResolver

_log = logging.getLogger(__name__)

#: Seconds granted to each teardown step during shutdown.  Prevents a
#: flaky session bus (unresponsive freedesktop notifications daemon,
#: hung varlink stream) from burning systemd's stop-sigterm deadline.
_CLEANUP_STEP_TIMEOUT_S = 2.0


async def run_notifier() -> None:
    """Run the notifier until SIGINT/SIGTERM."""
    configure_logging()
    notifier = await create_notifier("terok-clearance")
    subscriber = EventSubscriber(notifier, identity_resolver=IdentityResolver())
    try:
        await subscriber.start()
    except Exception:
        _log.exception("clearance subscriber failed to connect to hub — exiting")
        with contextlib.suppress(Exception):
            await notifier.disconnect()
        raise SystemExit(1) from None

    _log.info("terok-clearance-notifier online")
    try:
        await wait_for_shutdown_signal()
    finally:
        await _teardown(subscriber, notifier)


async def _teardown(subscriber: EventSubscriber, notifier: Notifier) -> None:
    """Stop subscriber + disconnect notifier under per-step timeouts."""
    for name, coro in (
        ("subscriber", subscriber.stop()),
        ("notifier", notifier.disconnect()),
    ):
        try:
            await asyncio.wait_for(coro, timeout=_CLEANUP_STEP_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — shutdown must continue past any step
            _log.warning(
                "clearance-notifier shutdown: %s didn't finish within %gs (%s)",
                name,
                _CLEANUP_STEP_TIMEOUT_S,
                exc,
            )


def main() -> None:  # pragma: no cover — CLI entry point
    """Entry point exposed as ``terok-clearance-notifier`` in pyproject.toml."""
    asyncio.run(run_notifier())
