# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Story: blocked connection → desktop notification → operator Allow → verdict.

End-to-end through the shield → hub → notifier → D-Bus → action → verdict
pipeline, with mocks only at the outer boundaries:

- :class:`terok_clearance.ClearanceHub` — real, runs on a per-test socket
- :class:`terok_clearance.client.subscriber.EventSubscriber` — real
- :class:`terok_clearance.notifications.desktop.DbusNotifier` — real
- Notification daemon — :mod:`dbusmock` mock on a private session bus
- VerdictClient — a recording stub here, so we can assert the verdict
  actually crossed the hub → shield-exec boundary

The producer end (shield's NFLOG reader) is replaced by a single fake
JSON line written to the hub's reader socket, since the line-on-the-wire
*is* the contract — shield's reader and the hub agree on this format,
nothing else is consulted.

What's deliberately *not* covered yet (tracked as follow-up):

- Launching a real podman container so the connection_blocked actually
  originates from a real NFLOG event, not a synthetic JSON line.
- Container DNS / shield-down-during-verdict edge cases.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.needs_dbus]


# ── Recording stub for VerdictClient ─────────────────────────


class _RecordingVerdictClient:
    """Stand-in for :class:`terok_clearance.verdict.client.VerdictClient`.

    The real client opens a varlink connection to the shield-exec helper
    and submits the verdict.  In tests we don't have the helper running,
    so we record the call and report success — the hub's responsibility
    ends at "did the verdict make it across the boundary".
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def apply(self, container: str, dest: str, action: str) -> tuple[bool, str]:
        """Record and ack as if the real shield-exec succeeded."""
        self.calls.append((container, dest, action))
        return True, ""

    async def stop(self) -> None:  # pragma: no cover — lifecycle no-op
        """No-op: nothing to clean up."""


# ── helpers ──────────────────────────────────────────────────


async def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.05) -> None:
    """Poll *predicate* until it returns truthy, or raise ``TimeoutError``.

    Used in story tests where we have to wait for an asynchronous boundary
    (D-Bus signal delivery, ingester thread picking up a write) to land.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError(f"predicate did not become truthy within {timeout}s")


async def _send_reader_event(socket_path: Path, event: dict[str, Any]) -> None:
    """Send one newline-delimited JSON event over the hub's reader socket.

    The hub's ingester reads line-by-line from an AF_UNIX SOCK_STREAM
    socket — the same shape the real shield NFLOG reader uses.
    """
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        writer.write((json.dumps(event) + "\n").encode("utf-8"))
        await writer.drain()
    finally:
        writer.close()
        with __import__("contextlib").suppress(Exception):
            await writer.wait_closed()
    # Discourage linters from complaining about the unused reader handle.
    del reader


# ── Story ───────────────────────────────────────────────────


async def test_verdict_loop_blocked_to_allow(
    terok_env,  # noqa: ANN001 — parent-conftest fixture
    tmp_path: Path,
    dbusmock_session: Any,
    notification_daemon: Any,
) -> None:
    """One blocked connection → one notification → Allow → verdict to shield.

    Six observable transitions, one assertion at each:

    1. We write a ``connection_blocked`` line into the reader socket.
    2. The hub's varlink stream produces a ``ConnectionBlocked`` event.
    3. The subscriber posts a ``Notify`` call onto the session bus.
    4. The mocked notification daemon records the call (its template
       captures every method invocation it sees).
    5. We invoke ``ActionInvoked`` from the daemon with action_key
       ``"allow"`` — simulating the operator pressing the button.
    6. The subscriber dispatches that action through the hub, which
       calls ``apply()`` on the (stubbed) verdict client.
    """
    pytest.importorskip("dbusmock")
    pytest.importorskip("terok_clearance")

    from terok_clearance import ClearanceHub, create_notifier
    from terok_clearance.client.subscriber import EventSubscriber

    clearance_sock = tmp_path / "clearance.sock"
    reader_sock = tmp_path / "reader.sock"

    # 0. Stand up the recording verdict client + real hub.
    verdicts = _RecordingVerdictClient()
    hub = ClearanceHub(
        clearance_socket=clearance_sock,
        reader_socket=reader_sock,
        verdict_client=verdicts,  # type: ignore[arg-type]  — duck-typed
    )
    await hub.start()
    try:
        # 1. Real D-Bus notifier on the private session bus.  Skip the
        #    test gracefully if the bus didn't come up (CI flake) — the
        #    factory returns a NullNotifier in that case, which would
        #    silently swallow the notifications under test.
        notifier = await create_notifier()
        from terok_clearance.notifications.desktop import DbusNotifier

        if not isinstance(notifier, DbusNotifier):
            pytest.skip("DbusNotifier not available on this session bus")

        # 2. Wire up the subscriber.
        subscriber = EventSubscriber(
            notifier,
            socket_path=clearance_sock,
            enabled_categories={"blocked", "verdict"},
        )
        await subscriber.start()
        try:
            # 3. Send a blocked-connection wire event.
            #
            #    ``subscriber.start()`` returns once the varlink connection
            #    is open, but the ``Subscribe()`` async generator may not
            #    have entered the hub's subscribers set yet — that
            #    happens on the next event-loop turn after the
            #    background ``_run_stream`` task is scheduled.  The hub
            #    fans out events only to *currently registered*
            #    subscribers, so an event posted in that window is
            #    silently dropped.  Yield long enough for the
            #    subscription to land before injecting the test event.
            await asyncio.sleep(0.25)
            await _send_reader_event(
                reader_sock,
                {
                    "type": "connection_blocked",
                    "container": "story-test-ctr",
                    "id": "req-001",
                    "dest": "203.0.113.42",
                    "port": 53,
                    "proto": 6,
                    "domain": "",
                },
            )

            # 4. Wait for the daemon to record the Notify call.
            #    python-dbusmock's notification_daemon template tracks
            #    every method call; we reach the mock's control
            #    interface via SpawnedMock.obj — a dbus-python proxy
            #    already bound to the mocked object, exposing the
            #    "org.freedesktop.DBus.Mock" methods (GetMethodCalls,
            #    EmitSignal, AddMethod, …).  ``dbusmock_session`` (a
            #    ``PrivateDBus`` in python-dbusmock >= 0.36) doesn't
            #    expose ``get_object`` itself, so this is the right
            #    handle for both querying and signalling.
            notif_calls = await _wait_for_notify_calls(notification_daemon)
            assert len(notif_calls) >= 1, "no Notify call landed on the daemon"
            _, args = notif_calls[0]
            # Notify signature: (app_name, replaces_id, app_icon, summary,
            # body, actions, hints, expire_timeout)
            summary, body = args[3], args[4]
            assert "203.0.113.42" in summary or "203.0.113.42" in body, (
                f"dest IP missing from notification: summary={summary!r} body={body!r}"
            )
            assert "story-test-ctr" in body, (
                f"container name missing from notification body: {body!r}"
            )

            # 5. Simulate the operator pressing "Allow".  The notification
            #    daemon mock emits an ActionInvoked signal carrying the
            #    notification id + action key; the DbusNotifier listens
            #    for that signal and dispatches the action to the
            #    subscriber's callback.  The template's id allocation
            #    is monotonic from 1, so the i-th Notify gets id == i.
            notif_id = len(notif_calls)
            _emit_action_invoked(notification_daemon, notif_id, "allow")

            # 6. Wait for the verdict to reach the (stubbed) shield-exec
            #    boundary.  The default short timeout keeps a stuck test
            #    from hanging — every step is ≤100ms on a warm host.
            await _wait_until(lambda: bool(verdicts.calls), timeout=5.0)
            assert verdicts.calls == [("story-test-ctr", "203.0.113.42", "allow")], (
                f"verdict not delivered with the expected arguments: {verdicts.calls!r}"
            )
        finally:
            await subscriber.stop()
            await notifier.disconnect()
    finally:
        await hub.stop()


# ── dbusmock helpers ─────────────────────────────────────────


async def _wait_for_notify_calls(
    mock: Any, *, timeout: float = 10.0
) -> list[tuple[int, list[Any]]]:
    """Poll the mocked notification daemon for recorded ``Notify`` calls.

    ``mock`` is a [`dbusmock.SpawnedMock`][dbusmock.SpawnedMock]; its
    ``.obj`` attribute is a dbus-python proxy already bound to the
    mocked object that exposes the ``org.freedesktop.DBus.Mock`` control
    interface (``GetMethodCalls`` here, ``EmitSignal`` below).  Returns
    the call list as it stands the first time it's non-empty.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        calls = mock.obj.GetMethodCalls("Notify", dbus_interface="org.freedesktop.DBus.Mock")
        if calls:
            # dbusmock returns a list of (timestamp, args) pairs.
            return [(int(ts), list(args)) for ts, args in calls]
        await asyncio.sleep(0.05)
    raise TimeoutError("notification daemon never recorded a Notify call")


def _emit_action_invoked(mock: Any, notif_id: int, action_key: str) -> None:
    """Have the mocked daemon emit an ``ActionInvoked`` signal.

    Signature ``us`` matches the freedesktop Notifications spec — ``u``
    is the notification id, ``s`` the action key (``"allow"`` here).
    """
    mock.obj.EmitSignal(
        "org.freedesktop.Notifications",
        "ActionInvoked",
        "us",
        [notif_id, action_key],
        dbus_interface="org.freedesktop.DBus.Mock",
    )
