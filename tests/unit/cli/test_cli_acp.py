# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``terok acp`` command's stdio-bridge helpers + early-exit surfacing.

The full ``_cmd_connect`` path ends in socket I/O against a live
daemon, but its building blocks are pure functions over a socket fd
and OS pipes — easy to drive with a ``socketpair`` and an ``os.pipe``.
``_fail`` is also covered here: ACP clients launch us as a subprocess
and typically discard stderr, so the JSON-RPC error frame on stdout
is the load-bearing surface for "ACP daemon could not start" errors.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from terok.cli.commands.acp import (
    _check_experimental_ack,
    _fail,
    _forward_socket_to_stdout,
    _forward_stdin_to_socket,
    _send_all,
)


@pytest.fixture
def sock_pair() -> tuple[socket.socket, socket.socket]:
    """Connected non-blocking ``AF_UNIX`` pair: (caller-side, peer-side)."""
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    a.setblocking(False)
    b.setblocking(False)
    try:
        yield a, b
    finally:
        a.close()
        b.close()


class TestSendAll:
    """``_send_all`` keeps writing until every byte is in the kernel buffer."""

    def test_writes_payload_in_one_shot(self, sock_pair) -> None:
        """A small payload fits in a single ``send`` and arrives intact."""
        caller, peer = sock_pair
        _send_all(caller, b"hello")
        assert peer.recv(64) == b"hello"

    def test_loops_past_short_send(self, sock_pair) -> None:
        """Short writes are retried until the whole view drains.

        ``socket.socket`` slots forbid ``setattr``, so wrap the caller
        end in a thin proxy that always sends one byte at a time and
        delegates everything else.
        """
        caller, peer = sock_pair

        class OneByteSock:
            def __init__(self, inner: socket.socket) -> None:
                self.inner = inner
                self.calls: list[int] = []

            def send(self, view: memoryview) -> int:
                n = self.inner.send(view[:1])
                self.calls.append(n)
                return n

        proxy = OneByteSock(caller)
        _send_all(proxy, b"abc")  # type: ignore[arg-type]
        assert proxy.calls == [1, 1, 1]
        assert peer.recv(64) == b"abc"


class TestForwardStdinToSocket:
    """``_forward_stdin_to_socket`` returns False on EOF, True on data."""

    def test_data_is_sent_and_keeps_stdin_open(self, sock_pair) -> None:
        """Non-empty stdin returns True and the bytes reach the socket peer."""
        caller, peer = sock_pair
        r, w = os.pipe()
        try:
            os.write(w, b"frame\n")
            still_open = _forward_stdin_to_socket(r, caller)
        finally:
            os.close(r)
            os.close(w)
        assert still_open is True
        assert peer.recv(64) == b"frame\n"

    def test_eof_signals_shut_wr_and_returns_false(self, sock_pair) -> None:
        """An empty read triggers ``SHUT_WR`` and ends the stdin half-loop."""
        caller, peer = sock_pair
        r, w = os.pipe()
        os.close(w)  # immediate EOF on r
        try:
            still_open = _forward_stdin_to_socket(r, caller)
        finally:
            os.close(r)
        assert still_open is False
        # SHUT_WR makes the peer see EOF on its read side.
        assert peer.recv(64) == b""

    def test_eof_tolerates_already_closed_peer(self, sock_pair) -> None:
        """``shutdown`` raising on a half-closed socket is swallowed."""
        caller, peer = sock_pair
        peer.close()
        r, w = os.pipe()
        os.close(w)
        try:
            still_open = _forward_stdin_to_socket(r, caller)
        finally:
            os.close(r)
        assert still_open is False


class TestForwardSocketToStdout:
    """``_forward_socket_to_stdout`` writes recv'd bytes and signals daemon EOF."""

    def test_data_is_written_to_stdout_fd(self, sock_pair) -> None:
        """A frame from the peer is copied to the supplied stdout fd."""
        caller, peer = sock_pair
        peer.send(b"reply\n")
        r, w = os.pipe()
        try:
            keep_going = _forward_socket_to_stdout(caller, w, Path("/tmp/test.log"))
            os.close(w)
            assert keep_going is True
            assert os.read(r, 64) == b"reply\n"
        finally:
            os.close(r)

    def test_blocking_io_is_a_no_op(self, sock_pair) -> None:
        """A spurious wakeup with no data returns True without touching stdout."""
        caller, _peer = sock_pair
        r, w = os.pipe()
        try:
            assert _forward_socket_to_stdout(caller, w, Path("/tmp/test.log")) is True
        finally:
            os.close(r)
            os.close(w)

    def test_peer_close_returns_false(self, sock_pair) -> None:
        """When the peer closes, ``recv`` yields b'' and the helper signals EOF."""
        caller, peer = sock_pair
        peer.close()
        r, w = os.pipe()
        try:
            keep_going = _forward_socket_to_stdout(caller, w, Path("/tmp/test.log"))
        finally:
            os.close(r)
            os.close(w)
        assert keep_going is False

    def test_connection_reset_exits_via_fail(self, capsys: pytest.CaptureFixture[str]) -> None:
        """``ConnectionResetError`` exits via ``_fail`` with the log path on stderr.

        Builds a ``recv`` that raises ``ConnectionResetError`` directly
        rather than wrestling with the kernel into emitting a real RST,
        which is timing-sensitive across Linux versions.  Stdout stays
        clean: an unsolicited ``id: null`` JSON-RPC frame would be
        rejected by Zed's parser as "neither id nor method".
        """

        class ResetSock:
            def recv(self, _n: int) -> bytes:
                raise ConnectionResetError(104, "Connection reset by peer")

        with pytest.raises(SystemExit) as excinfo:
            _forward_socket_to_stdout(ResetSock(), 1, Path("/tmp/probe.log"))  # type: ignore[arg-type]
        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""  # no malformed JSON-RPC frame
        assert "/tmp/probe.log" in captured.err
        assert "connection reset" in captured.err.lower()


class TestExperimentalAck:
    """``_check_experimental_ack`` gates ``terok acp connect`` on the existing ``is_experimental()`` axis.

    Same opt-in axis as the rest of the codebase's experimental
    features (``--experimental`` CLI flag, ``experimental: true`` in
    config.yml).  The threat-model banner prints regardless so users
    discover what they're consenting to even before they flip the
    flag.
    """

    def test_passes_when_experimental_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With experimental on, the gate prints the banner but does not exit."""
        from terok.cli.commands import acp as acp_mod

        monkeypatch.setattr(acp_mod, "is_experimental", lambda: True)
        _check_experimental_ack()

    def test_exits_when_experimental_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """With experimental off, gate exits 2 after printing banner + how-to-enable.

        Asserts only structural invariants: ``EXPERIMENTAL`` appears
        (so it's recognisable as the discouragement banner, not a
        stack trace), the ``--experimental`` opt-in mechanism is
        named (so the user knows what to flip), and the body is more
        than a one-liner.  Avoids pinning specific phrasing in case
        the wording evolves with the threat model.
        """
        from terok.cli.commands import acp as acp_mod

        monkeypatch.setattr(acp_mod, "is_experimental", lambda: False)
        with pytest.raises(SystemExit) as excinfo:
            _check_experimental_ack()
        assert excinfo.value.code == 2
        captured = capsys.readouterr()
        assert "EXPERIMENTAL" in captured.err
        assert "--experimental" in captured.err
        assert captured.err.count("\n") >= 5

    def test_cmd_connect_invokes_check_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The gate runs before ``_cmd_connect`` does any real work.

        A refactor that moved ``_check_experimental_ack`` below the
        ``_spawn_daemon`` / ``_inprocess_pump`` calls would still pass
        the unit-level tests above, but it would also defeat the gate:
        the daemon would already be alive by the time the user is told
        no.  Mock the gate to raise a sentinel and confirm that nothing
        downstream of it ran.
        """
        from terok.cli.commands import acp as acp_mod

        called: list[str] = []

        def _stub_check() -> None:
            called.append("check")
            raise SystemExit(2)

        def _unreachable_spawn(*_args: object, **_kwargs: object) -> None:
            called.append("spawn")  # pragma: no cover — must not run

        def _unreachable_pump(*_args: object, **_kwargs: object) -> None:
            called.append("pump")  # pragma: no cover — must not run

        monkeypatch.setattr(acp_mod, "_check_experimental_ack", _stub_check)
        monkeypatch.setattr(acp_mod, "_spawn_daemon", _unreachable_spawn)
        monkeypatch.setattr(acp_mod, "_inprocess_pump", _unreachable_pump)

        with pytest.raises(SystemExit):
            acp_mod._cmd_connect("project", "task")
        assert called == ["check"]


class TestFail:
    """``_fail`` writes to stderr only — no malformed JSON-RPC frame."""

    def test_writes_message_to_stderr_and_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Stderr carries the message; stdout stays clean.

        An unsolicited ``{id: null, error}`` frame on stdout used to be
        emitted here so Zed would surface the error, but Zed's parser
        rejected it as "neither id nor method" — and Zed already
        captures the agent subprocess's stderr at WARN level, which is
        the right channel for this kind of unsolicited error anyway.
        """
        with pytest.raises(SystemExit) as excinfo:
            _fail("daemon won't start")
        assert excinfo.value.code == 1

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "daemon won't start" in captured.err
