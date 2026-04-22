# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Bridge clearance-hub events to desktop popups.

Runs as ``terok-clearance-notifier.service`` — a systemd user unit
paired with the hub's own.  Splitting the roles means headless hosts
(CI, servers) run the hub without pulling in a desktop stack, and
notifier crashes never take the firewall or the hub with them.
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
    """Run the notifier until SIGINT/SIGTERM — or until the hub disconnects."""
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
    shutdown_signal = asyncio.create_task(wait_for_shutdown_signal())
    stream_ended = asyncio.create_task(subscriber.wait_closed())
    try:
        done, pending = await asyncio.wait(
            {shutdown_signal, stream_ended}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    finally:
        await _teardown(subscriber, notifier)
    if stream_ended in done and shutdown_signal not in done:
        # Hub went away; exit non-zero so ``Restart=on-failure`` picks us
        # back up on the next hub start.  SIGTERM wins this race cleanly
        # (``done`` contains ``shutdown_signal``), so a normal stop stays
        # a normal stop.
        _log.warning("clearance event stream ended; exiting so systemd can reconnect")
        raise SystemExit(1)


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
    """Systemd-unit ``ExecStart`` target — launches :func:`run_notifier` on an event loop."""
    asyncio.run(run_notifier())


if __name__ == "__main__":
    # Without this guard ``python -m terok.clearance.notifier.app`` under
    # systemd would import the module, define ``main``, and exit 0 without
    # running it — the notifier silently never started and every desktop
    # popup went missing.
    main()
