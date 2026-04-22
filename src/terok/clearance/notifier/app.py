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
import signal
import sys

from terok_dbus import EventSubscriber, create_notifier

from terok.clearance.identity import IdentityResolver

_log = logging.getLogger(__name__)

#: Seconds granted to each teardown step during shutdown.  Prevents a
#: flaky session bus (unresponsive freedesktop notifications daemon,
#: hung varlink stream) from burning systemd's stop-sigterm deadline.
_CLEANUP_STEP_TIMEOUT_S = 2.0


async def run_notifier() -> None:
    """Run the notifier until SIGINT/SIGTERM."""
    _configure_logging()
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
        await _wait_for_shutdown_signal()
    finally:
        await _teardown(subscriber, notifier)


async def _teardown(subscriber: EventSubscriber, notifier) -> None:  # noqa: ANN001
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


async def _wait_for_shutdown_signal() -> None:  # pragma: no cover — real signal delivery
    """Block until SIGINT/SIGTERM arrives so systemd can stop us cleanly."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()


def _configure_logging() -> None:
    """Send INFO-level logs to stderr so journald / systemd pick them up."""
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
        stream=sys.stderr,
    )


def main() -> None:  # pragma: no cover — CLI entry point
    """Entry point exposed as ``terok-clearance-notifier`` in pyproject.toml."""
    asyncio.run(run_notifier())
